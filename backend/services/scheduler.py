import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.database import (
    FeedModel, SourceModel, ArticleModel, ClusterModel,
    AsyncSessionLocal,
)
from models.schemas import PipelineStatus
from services.crawler import crawl_source, RawArticle
from services.embeddings import get_embedding
from services.deduplication import find_embedding_duplicates, store_embeddings
from services.clustering import cluster_articles, generate_cluster_metadata, assign_article_key_angles
from services.ai_processor import summarize_article, generate_why_it_matters, filter_by_relevance
from services.translation import translate_article
from config import settings

logger = logging.getLogger(__name__)


# ─── Full Pipeline ─────────────────────────────────────────────────────────────

async def run_feed_pipeline(
    feed_id: int,
    force: bool = False,
    on_status: Optional[Callable[[str], None]] = None,
) -> PipelineStatus:
    """
    Execute the full news processing pipeline for a feed.
    on_status: optional callback called with human-readable progress messages.
    """
    def update(msg: str):
        logger.info(f"[Feed {feed_id}] {msg}")
        if on_status:
            on_status(msg)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(FeedModel).where(FeedModel.id == feed_id))
        feed: Optional[FeedModel] = result.scalar_one_or_none()
        if not feed:
            return PipelineStatus(status="error", message=f"Feed {feed_id} not found")

        src_result = await db.execute(
            select(SourceModel).where(
                SourceModel.feed_id == feed_id,
                SourceModel.is_active == True,
            ).order_by(SourceModel.id)
        )
        sources: List[SourceModel] = src_result.scalars().all()

        if not sources:
            return PipelineStatus(status="warning", message="Нет активных источников")

        target_lang = feed.language or settings.preferred_language
        pipeline_start = time.monotonic()

        # ── Step 1: Crawl ALL sources IN PARALLEL ──────────────────────────
        update(f"Сбор из {len(sources)} источников…")

        async def crawl_one(src: SourceModel) -> List[Dict]:
            try:
                raws = await crawl_source(src.id, src.url, src.name, src.source_type)
                src.last_fetched_at = datetime.utcnow()
                return [
                    {
                        "source_id": src.id,
                        "source_name": src.name,
                        "title": r.title,
                        "url": r.url,
                        "body": r.body,
                        "language": r.language or src.language,
                        "published_at": r.published_at,
                        "embedding": None,
                        "summary": "",
                        "why_it_matters": "",
                        "key_angle": "",
                    }
                    for r in raws
                ]
            except Exception as e:
                logger.error(f"Crawl error for source {src.id}: {e}")
                return []

        # Parallel fetch — limit concurrency to 10 to avoid overwhelming servers
        semaphore = asyncio.Semaphore(10)
        async def crawl_limited(src):
            async with semaphore:
                return await crawl_one(src)

        results = await asyncio.gather(*[crawl_limited(s) for s in sources])
        raw_articles: List[Dict] = [a for batch in results for a in batch]
        await db.commit()  # save last_fetched_at for all sources

        crawl_elapsed = time.monotonic() - pipeline_start
        update(f"Получено {len(raw_articles)} статей за {crawl_elapsed:.0f}с")

        if not raw_articles:
            return PipelineStatus(status="ok", message="Новых статей не найдено", articles_fetched=0)

        # ── Step 2: Filter already-stored URLs ────────────────────────────
        existing_urls_result = await db.execute(select(ArticleModel.url))
        existing_urls = set(row[0] for row in existing_urls_result.all())
        raw_articles = [a for a in raw_articles if a["url"] not in existing_urls]
        update(f"{len(raw_articles)} новых после проверки дубликатов по URL")

        # ── Step 3: Topic relevance filter (local LLM) ────────────────────
        raw_articles = await filter_by_relevance(raw_articles, feed.description or feed.name)

        # ── Step 4: Compute embeddings IN PARALLEL ────────────────────────
        total = len(raw_articles)
        if total == 0:
            return PipelineStatus(status="ok", message="Все статьи уже были обработаны ранее",
                                  articles_fetched=len(raw_articles))

        update(f"Вычисление эмбеддингов для {total} статей…")

        async def embed_one(art: Dict) -> None:
            text = f"{art['title']} {art['body'][:500]}"
            art["embedding"] = await get_embedding(text)

        emb_semaphore = asyncio.Semaphore(5)
        async def embed_limited(art):
            async with emb_semaphore:
                await embed_one(art)

        await asyncio.gather(*[embed_limited(a) for a in raw_articles])

        # ── Step 5: Embedding-based dedup ─────────────────────────────────
        update("Дедупликация…")
        raw_articles = await find_embedding_duplicates(db, raw_articles)
        await store_embeddings(db, raw_articles)

        articles_new = len(raw_articles)
        if articles_new == 0:
            return PipelineStatus(status="ok", message="Новых статей не найдено (все дубликаты)",
                                  articles_fetched=total)

        # ── ETA estimate: ~5s per article for AI processing ───────────────
        estimated_remaining = articles_new * 5
        update(f"Кластеризация {articles_new} статей… (~{estimated_remaining}с)")

        # ── Step 6: Cluster ───────────────────────────────────────────────
        groups = await cluster_articles(raw_articles)
        update(f"Создано {len(groups)} историй, генерация сводок…")

        # ── Step 7: Persist clusters and articles ─────────────────────────
        clusters_created = 0
        for group_idx, group in enumerate(groups):
            meta = await generate_cluster_metadata(group)

            # Compute latest article publication time for sorting
            latest_pub = max(
                (a.get("published_at") or a.get("fetched_at") for a in group),
                default=None
            )

            cluster = ClusterModel(
                feed_id=feed_id,
                title=meta["title"],
                summary=meta["summary"],
                why_it_matters=meta["why_it_matters"],
                key_angles=meta["key_angles"],
                article_count=len(group),
                latest_article_at=latest_pub,
            )
            db.add(cluster)
            await db.flush()

            await assign_article_key_angles(group, meta["key_angles"])

            # Summarize articles in parallel (capped at 3 concurrent AI calls)
            ai_sem = asyncio.Semaphore(3)

            async def process_article(art: Dict):
                async with ai_sem:
                    summary = await summarize_article(
                        art["title"], art["body"], mode="short", target_language=target_lang
                    )
                    why_matters = await generate_why_it_matters(
                        art["title"], art["body"],
                        cluster_context=meta["title"], target_language=target_lang
                    )
                    translated_title = art["title"]
                    is_translated = False
                    if art.get("language", "en") != target_lang:
                        from services.translation import translate_text
                        translated_title = await translate_text(art["title"], target_lang=target_lang)
                        is_translated = True

                    return ArticleModel(
                        source_id=art["source_id"],
                        cluster_id=cluster.id,
                        title=translated_title,
                        original_title=art["title"],
                        url=art["url"],
                        body=art["body"][:10000],
                        summary=summary,
                        why_it_matters=why_matters,
                        key_angle=art.get("key_angle", ""),
                        language=target_lang,
                        published_at=art.get("published_at"),
                        is_translated=is_translated,
                    )

            article_models = await asyncio.gather(*[process_article(a) for a in group])
            for am in article_models:
                db.add(am)

            clusters_created += 1
            elapsed = time.monotonic() - pipeline_start
            remaining_groups = len(groups) - group_idx - 1
            eta = int(remaining_groups * (elapsed / (group_idx + 1)))
            update(f"Обработано {group_idx + 1}/{len(groups)} историй… (≈{eta}с)")

        feed.last_run_at = datetime.utcnow()
        await db.commit()

        total_elapsed = int(time.monotonic() - pipeline_start)
        msg = f"Готово за {total_elapsed}с: +{articles_new} статей, {clusters_created} историй"
        return PipelineStatus(
            status="ok",
            message=msg,
            articles_fetched=total,
            articles_new=articles_new,
            clusters_created=clusters_created,
        )


# ─── Scheduler ────────────────────────────────────────────────────────────────

_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def _deliver_feed(feed_id: int):
    """
    Run pipeline for a feed and then notify the Mac app via a push to
    the /feeds/{id}/notify endpoint (which the Swift app polls).
    The pipeline is triggered 5 minutes before the delivery time so
    results are ready at the scheduled moment.
    """
    logger.info(f"Delivery job: running pipeline for feed {feed_id}")
    result = await run_feed_pipeline(feed_id, force=False)

    # Mark delivery time in DB
    async with AsyncSessionLocal() as db:
        feed_result = await db.execute(select(FeedModel).where(FeedModel.id == feed_id))
        feed = feed_result.scalar_one_or_none()
        if feed:
            feed.last_delivered_at = datetime.utcnow()
            await db.commit()

    logger.info(f"Delivery complete for feed {feed_id}: {result.message}")

    # Signal to the polling endpoint that a new digest is ready
    _delivery_notifications[feed_id] = {
        "ready": True,
        "message": result.message,
        "articles_new": result.articles_new,
        "clusters_created": result.clusters_created,
        "at": datetime.utcnow().isoformat() + "Z",
    }


# In-memory notification store — polled by Swift app
_delivery_notifications: dict[int, dict] = {}


def get_delivery_notification(feed_id: int) -> dict | None:
    return _delivery_notifications.pop(feed_id, None)


async def schedule_feeds():
    """
    Set up delivery jobs for all active feeds.
    Each delivery_time triggers the pipeline 5 minutes early so results
    are ready exactly at the scheduled time.
    """
    from apscheduler.triggers.cron import CronTrigger
    scheduler = get_scheduler()
    scheduler.remove_all_jobs()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FeedModel).where(FeedModel.is_active == True)
        )
        feeds = result.scalars().all()

    for feed in feeds:
        delivery_times = feed.delivery_times or ["08:00"]
        days = feed.schedule_days or [0, 1, 2, 3, 4]
        day_str = ",".join(str(d) for d in days)

        for time_str in delivery_times:
            try:
                hour, minute = map(int, time_str.split(":"))
            except ValueError:
                continue

            # Start pipeline 5 minutes before delivery
            start_minute = minute - 5
            start_hour = hour
            if start_minute < 0:
                start_minute += 60
                start_hour = (hour - 1) % 24

            scheduler.add_job(
                _deliver_feed,
                CronTrigger(day_of_week=day_str, hour=start_hour, minute=start_minute),
                args=[feed.id],
                id=f"deliver_{feed.id}_{time_str}",
                replace_existing=True,
            )
            logger.info(
                f"Scheduled feed {feed.id} '{feed.name}': "
                f"pipeline at {start_hour:02d}:{start_minute:02d}, "
                f"delivery at {hour:02d}:{minute:02d} on days {day_str}"
            )

    if not scheduler.running:
        scheduler.start()
