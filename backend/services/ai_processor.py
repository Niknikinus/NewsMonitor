import httpx
import logging
from typing import Optional, Dict, List
from config import settings

logger = logging.getLogger(__name__)


# ─── Grok-3 via GitHub Marketplace ───────────────────────────────────────────

async def ask_grok(
    prompt: str,
    system: str = "You are a helpful news analysis assistant.",
    max_tokens: int = 1000,
    temperature: float = 0.3,
    max_retries: int = 5,
) -> str:
    if not settings.grok_api_key:
        return ""

    messages = [{"role": "user", "content": prompt}]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{settings.grok_api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.grok_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.grok_model,
                        "messages": [{"role": "system", "content": system}] + messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt  # 1, 2, 4, 8, 16 сек
                    logger.warning(f"Grok-3 rate limited, waiting {wait}s (attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            if "429" in str(e):
                wait = 2 ** attempt
                await asyncio.sleep(wait)
                continue
            logger.error(f"Grok-3 error: {e}")
            return ""

    logger.error("Grok-3 max retries exceeded")
    return ""


# ─── Local LLM (Ollama/Qwen2.5) ──────────────────────────────────────────────

async def ask_local_llm(
    prompt: str,
    system: str = "You are a concise news assistant.",
    max_tokens: int = 500,
) -> str:
    """Send a prompt to local LLM (Ollama OpenAI-compatible endpoint)."""
    if not settings.local_llm_enabled:
        return ""

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{settings.local_llm_base}/chat/completions",
                json={
                    "model": settings.local_llm_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"Local LLM error: {e}")
        return ""


# ─── Topic Relevance Filter ───────────────────────────────────────────────────

async def filter_by_relevance(
    articles: List[Dict],
    topic: str,
) -> List[Dict]:
    """
    Use local LLM (fast) to filter articles by topic relevance.
    Falls back to keeping all articles if LLM unavailable.
    """
    if not settings.local_llm_enabled:
        return articles

    relevant = []
    for art in articles:
        prompt = (
            f"Topic: {topic}\n"
            f"Article title: {art.get('title', '')}\n"
            f"Article snippet: {art.get('body', '')[:300]}\n\n"
            "Is this article relevant to the topic? Reply YES or NO only."
        )
        resp = await ask_local_llm(prompt, max_tokens=5)
        if "YES" in resp.upper():
            relevant.append(art)

    logger.info(f"Relevance filter: {len(articles)} → {len(relevant)} articles")
    return relevant if relevant else articles  # safety: keep all if filter kills everything


# ─── Summarization ────────────────────────────────────────────────────────────

async def summarize_article(
    title: str,
    body: str,
    mode: str = "short",
    target_language: str = "en",
) -> str:
    """
    Summarize an article.
    mode: "headline" | "short" | "analytical"
    """
    lang_instruction = (
        "Respond in Russian." if target_language == "ru" else "Respond in English."
    )

    if mode == "headline":
        prompt = (
            f"Write a single concise headline for this article. {lang_instruction}\n\n"
            f"Title: {title}\n{body[:500]}"
        )
        max_tok = 50
    elif mode == "analytical":
        prompt = (
            f"Write a 3-4 paragraph analytical summary of this article, "
            f"covering context, key facts, and implications. {lang_instruction}\n\n"
            f"Title: {title}\n{body[:3000]}"
        )
        max_tok = 600
    else:  # short
        prompt = (
            f"Write a 1-2 sentence summary of this article. {lang_instruction}\n\n"
            f"Title: {title}\n{body[:1000]}"
        )
        max_tok = 150

    # Try Grok-3 first, fall back to local LLM
    result = await ask_grok(prompt, max_tokens=max_tok)
    if not result and settings.local_llm_enabled:
        result = await ask_local_llm(prompt, max_tokens=max_tok)

    return result.strip()


# ─── Why It Matters ───────────────────────────────────────────────────────────

async def generate_why_it_matters(
    title: str,
    body: str,
    cluster_context: str = "",
    target_language: str = "en",
) -> str:
    """Generate a brief 'why it matters' explanation via Grok-3."""
    lang_instruction = (
        "Respond in Russian." if target_language == "ru" else "Respond in English."
    )

    context = f"Story context: {cluster_context}\n" if cluster_context else ""
    prompt = (
        f"In 1-2 sentences, explain why this news story matters and what impact it could have. "
        f"{lang_instruction}\n\n"
        f"{context}"
        f"Title: {title}\n{body[:800]}"
    )

    return (await ask_grok(prompt, max_tokens=150)).strip()


# ─── Source Discovery ─────────────────────────────────────────────────────────

async def discover_sources(topic: str) -> List[Dict]:
    """Ask Grok-3 to suggest news sources for a given topic (supports Russian topics)."""
    prompt = f"""You are a news research assistant. The user wants to monitor news on this topic: "{topic}"

Suggest 10 high-quality news sources covering this topic. Include both English and Russian sources if relevant.

Return a JSON array where each object has:
- "name": source name
- "url": RSS feed URL or website URL
- "tier": 1 (major/mainstream media), 2 (niche/trade/specialist), or 3 (blog/reddit/social)
- "rating": quality score 0-100
- "language": "en" or "ru"
- "sample_headlines": array of 2 realistic example headlines in the source's language

Important: Return ONLY a valid JSON array. No markdown, no explanation, no preamble."""

    resp = await ask_grok(prompt, max_tokens=1500)
    try:
        import json
        clean = resp.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except Exception as e:
        logger.error(f"Source discovery parse error: {e}")
        return []


# ─── API Connection Test ──────────────────────────────────────────────────────

async def test_grok_connection() -> tuple[bool, str]:
    resp = await ask_grok("Say 'OK' and nothing else.", max_tokens=5)
    if resp and "OK" in resp.upper():
        return True, "Connected to Grok-3 successfully"
    if resp:
        return True, f"Connected (response: {resp[:50]})"
    return False, "No response — check API key and endpoint"


async def test_embedding_connection() -> tuple[bool, str]:
    from services.embeddings import get_embedding
    emb = await get_embedding("test connection")
    if emb and len(emb) > 0:
        return True, f"Connected to embeddings (dim={len(emb)})"
    return False, "No embedding returned — check API key"
