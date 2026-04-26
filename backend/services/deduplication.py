import logging
from typing import List, Tuple, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.database import ArticleModel, EmbeddingModel
from services.embeddings import (
    cosine_similarity,
    deserialize_embedding,
    serialize_embedding,
    get_embedding,
)
from services.ai_processor import ask_grok
from config import settings

logger = logging.getLogger(__name__)


# ─── Step 1: Embedding-based pre-filter ───────────────────────────────────────

async def find_embedding_duplicates(
    db: AsyncSession,
    new_articles: List[Dict],
) -> List[Dict]:
    """
    Mark articles as duplicates if cosine similarity with any stored embedding
    exceeds the dedup threshold.
    Returns filtered list (non-duplicates).
    """
    if not settings.openai_api_key:
        logger.info("Embeddings disabled — skipping dedup step 1")
        return new_articles

    # Load stored embeddings
    result = await db.execute(select(EmbeddingModel))
    stored: List[EmbeddingModel] = result.scalars().all()
    stored_vecs = [(e.article_url, deserialize_embedding(e.embedding)) for e in stored]

    non_dupes = []

    for art in new_articles:
        text = f"{art['title']} {art['body'][:500]}"
        emb = await get_embedding(text)

        if emb is None:
            # Can't check — assume not duplicate
            art["embedding"] = None
            non_dupes.append(art)
            continue

        art["embedding"] = emb

        # Check against history
        is_dupe = False
        for stored_url, stored_vec in stored_vecs:
            if stored_url == art["url"]:
                is_dupe = True
                break
            sim = cosine_similarity(emb, stored_vec)
            if sim >= settings.dedup_cosine_threshold:
                logger.debug(f"Embedding dupe ({sim:.3f}): {art['url']}")
                is_dupe = True
                break

        if not is_dupe:
            non_dupes.append(art)

    logger.info(f"Dedup step 1: {len(new_articles)} → {len(non_dupes)} articles")
    return non_dupes


# ─── Step 2: Grok-3 confirmation for borderline cases ─────────────────────────

async def confirm_duplicates_with_grok(
    candidates: List[Tuple[Dict, Dict]],
) -> List[Tuple[Dict, Dict, bool]]:
    """
    For pairs that embedding similarity flagged as close (0.80-0.92),
    ask Grok-3 to confirm if they're about the same event.
    Returns list of (art1, art2, is_duplicate).
    """
    results = []

    for art1, art2 in candidates:
        prompt = (
            "You are a news deduplication assistant.\n"
            "Determine if the two articles below are reporting on the SAME event or story.\n"
            "Respond with only: YES or NO\n\n"
            f"Article 1:\nTitle: {art1['title']}\nSummary: {art1['body'][:300]}\n\n"
            f"Article 2:\nTitle: {art2['title']}\nSummary: {art2['body'][:300]}"
        )

        response = await ask_grok(prompt, max_tokens=10)
        is_dupe = response.strip().upper().startswith("YES")
        results.append((art1, art2, is_dupe))

    return results


# ─── Store Embeddings ─────────────────────────────────────────────────────────

async def store_embeddings(db: AsyncSession, articles: List[Dict]) -> None:
    """Persist embeddings to the database for future dedup checks."""
    for art in articles:
        emb = art.get("embedding")
        if emb is None:
            continue

        existing = await db.execute(
            select(EmbeddingModel).where(EmbeddingModel.article_url == art["url"])
        )
        if existing.scalar_one_or_none():
            continue

        record = EmbeddingModel(
            article_url=art["url"],
            embedding=serialize_embedding(emb),
            model=settings.openai_embedding_model,
        )
        db.add(record)

    await db.commit()
