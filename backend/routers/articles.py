from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
import json

from models.database import get_db, ArticleModel, ClusterModel, SourceModel
from models.schemas import ArticleOut, ClusterOut, ExportRequest

router = APIRouter(tags=["articles"])

# ─── Clusters ─────────────────────────────────────────────────────────────────

@router.get("/feeds/{feed_id}/clusters", response_model=List[ClusterOut])
async def list_clusters(
    feed_id: int,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ClusterModel)
        .where(ClusterModel.feed_id == feed_id)
        .options(selectinload(ClusterModel.articles))
    )
    if unread_only:
        query = query.where(ClusterModel.is_read == False)

    result = await db.execute(query)
    clusters = result.scalars().all()

    out = []
    for c in clusters:
        articles_out = []
        for art in sorted(
            c.articles,
            key=lambda a: a.published_at or a.fetched_at,
            reverse=True,
        ):
            src_result = await db.execute(
                select(SourceModel).where(SourceModel.id == art.source_id)
            )
            src = src_result.scalar_one_or_none()
            ao = ArticleOut.model_validate(art)
            ao.source_name = src.name if src else ""
            articles_out.append(ao)

        co = ClusterOut.model_validate(c)
        co.articles = articles_out
        out.append(co)

    # Sort clusters by newest article publication time
    out.sort(key=lambda c: c.latest_article_at or c.created_at, reverse=True)
    return out


@router.patch("/clusters/{cluster_id}/read")
async def mark_cluster_read(
    cluster_id: int,
    is_read: bool = True,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ClusterModel).where(ClusterModel.id == cluster_id))
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    cluster.is_read = is_read
    await db.commit()
    return {"cluster_id": cluster_id, "is_read": is_read}


@router.post("/clusters/{cluster_id}/translate")
async def translate_cluster_endpoint(
    cluster_id: int,
    target_lang: str = "ru",
    db: AsyncSession = Depends(get_db),
):
    """
    Translate cluster + articles to target_lang.
    target_lang='original' → restore original_title for all articles.
    """
    result = await db.execute(
        select(ClusterModel)
        .where(ClusterModel.id == cluster_id)
        .options(selectinload(ClusterModel.articles))
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    from services.translation import translate_text, translate_article

    if target_lang == "original":
        for art in cluster.articles:
            if art.original_title:
                art.title = art.original_title
            art.is_translated = False
        first_orig = next((a.original_title for a in cluster.articles if a.original_title), None)
        if first_orig:
            cluster.title = first_orig
    else:
        cluster.title = await translate_text(cluster.title, target_lang=target_lang)
        if cluster.summary:
            cluster.summary = await translate_text(cluster.summary, target_lang=target_lang)
        if cluster.why_it_matters:
            cluster.why_it_matters = await translate_text(cluster.why_it_matters, target_lang=target_lang)

        for art in cluster.articles:
            # Save original before first translation
            if not art.original_title:
                art.original_title = art.title
            t_title, t_body, t_summary = await translate_article(
                art.original_title or art.title,
                art.body or "",
                art.summary or "",
                target_lang,
            )
            art.title = t_title
            art.body = t_body
            art.summary = t_summary
            art.is_translated = True
            art.language = target_lang

    await db.commit()

    result2 = await db.execute(
        select(ClusterModel)
        .where(ClusterModel.id == cluster_id)
        .options(selectinload(ClusterModel.articles))
    )
    c = result2.scalar_one()
    articles_out = []
    for art in sorted(c.articles, key=lambda a: a.published_at or a.fetched_at, reverse=True):
        src_r = await db.execute(select(SourceModel).where(SourceModel.id == art.source_id))
        src = src_r.scalar_one_or_none()
        ao = ArticleOut.model_validate(art)
        ao.source_name = src.name if src else ""
        articles_out.append(ao)

    co = ClusterOut.model_validate(c)
    co.articles = articles_out
    return co


@router.delete("/clusters/{cluster_id}")
async def delete_cluster(cluster_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ClusterModel).where(ClusterModel.id == cluster_id))
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    await db.delete(cluster)
    await db.commit()
    return {"deleted": cluster_id}


# ─── Articles ─────────────────────────────────────────────────────────────────

@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(article_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ArticleModel).where(ArticleModel.id == article_id))
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    src_result = await db.execute(select(SourceModel).where(SourceModel.id == article.source_id))
    src = src_result.scalar_one_or_none()
    ao = ArticleOut.model_validate(article)
    ao.source_name = src.name if src else ""
    return ao


@router.delete("/articles/{article_id}")
async def delete_article(article_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ArticleModel).where(ArticleModel.id == article_id))
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    await db.delete(article)
    await db.commit()
    return {"deleted": article_id}


@router.post("/articles/{article_id}/translate")
async def translate_article_endpoint(
    article_id: int,
    target_lang: str = "ru",
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ArticleModel).where(ArticleModel.id == article_id))
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    from services.translation import translate_article
    if not article.original_title:
        article.original_title = article.title

    t_title, t_body, t_summary = await translate_article(
        article.original_title or article.title,
        article.body, article.summary, target_lang
    )
    article.title = t_title
    article.body = t_body
    article.summary = t_summary
    article.is_translated = True
    article.language = target_lang
    await db.commit()
    await db.refresh(article)
    return ArticleOut.model_validate(article)


# ─── Export ───────────────────────────────────────────────────────────────────

@router.post("/export")
async def export_feed(data: ExportRequest, db: AsyncSession = Depends(get_db)):
    query = (
        select(ClusterModel)
        .where(ClusterModel.feed_id == data.feed_id)
        .options(selectinload(ClusterModel.articles))
        .order_by(ClusterModel.created_at.desc())
    )
    if data.cluster_ids:
        query = query.where(ClusterModel.id.in_(data.cluster_ids))
    result = await db.execute(query)
    clusters = result.scalars().all()

    if data.format == "markdown":
        lines = ["# Экспорт новостей\n"]
        for c in clusters:
            lines.append(f"## {c.title}\n")
            if c.summary:
                lines.append(f"{c.summary}\n")
            if c.why_it_matters:
                lines.append(f"**Почему важно:** {c.why_it_matters}\n")
            lines.append(f"*{c.created_at.strftime('%Y-%m-%d %H:%M')} UTC — {c.article_count} статей*\n")
            lines.append("")
            for art in sorted(c.articles, key=lambda a: a.published_at or a.fetched_at, reverse=True):
                angle = f" _{art.key_angle}_" if art.key_angle else ""
                lines.append(f"- [{art.title}]({art.url}){angle}")
                if art.summary:
                    lines.append(f"  > {art.summary}")
            lines.append("")
        md = "\n".join(lines)
        return Response(
            content=md, media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="news_export.md"'},
        )
    raise HTTPException(status_code=400, detail="Only markdown export in MVP")
