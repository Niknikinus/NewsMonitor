import httpx
import logging
from typing import Optional
from config import settings

logger = logging.getLogger(__name__)

DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"

LANG_MAP_DEEPL = {"en": "EN", "ru": "RU"}
LANG_MAP_GOOGLE = {"en": "en", "ru": "ru"}


async def translate_text(text: str, target_lang: str = "en", source_lang: str = "auto") -> str:
    """
    Translate text to target_lang.
    Primary: DeepL. Fallback: Google Translate (free endpoint).
    """
    if not text.strip():
        return text

    # Skip if source and target are the same (simple heuristic)
    if _detect_language_heuristic(text) == target_lang:
        return text

    result = None

    # Try DeepL
    if settings.deepl_api_key:
        result = await _translate_deepl(text, target_lang, source_lang)

    # Fallback to Google Translate
    if not result:
        result = await _translate_google(text, target_lang)

    return result or text


async def _translate_deepl(text: str, target_lang: str, source_lang: str = "auto") -> Optional[str]:
    tl = LANG_MAP_DEEPL.get(target_lang, "EN")
    sl = None if source_lang == "auto" else LANG_MAP_DEEPL.get(source_lang)

    params = {
        "auth_key": settings.deepl_api_key,
        "text": text[:5000],
        "target_lang": tl,
    }
    if sl:
        params["source_lang"] = sl

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(DEEPL_API_URL, data=params)
            resp.raise_for_status()
            data = resp.json()
            return data["translations"][0]["text"]
    except Exception as e:
        logger.warning(f"DeepL error: {e}")
        return None


async def _translate_google(text: str, target_lang: str) -> Optional[str]:
    """Google Translate unofficial free endpoint."""
    tl = LANG_MAP_GOOGLE.get(target_lang, "en")
    url = "https://translate.googleapis.com/translate_a/single"

    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": tl,
        "dt": "t",
        "q": text[:5000],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            # Flatten translation chunks
            chunks = [item[0] for item in data[0] if item[0]]
            return "".join(chunks)
    except Exception as e:
        logger.warning(f"Google Translate error: {e}")
        return None


def _detect_language_heuristic(text: str) -> str:
    """Very simple Cyrillic detection — returns 'ru' if mostly Cyrillic, else 'en'."""
    sample = text[:200]
    cyrillic = sum(1 for c in sample if "\u0400" <= c <= "\u04FF")
    return "ru" if cyrillic / max(len(sample), 1) > 0.2 else "en"


async def translate_article(
    title: str,
    body: str,
    summary: str,
    target_lang: str,
) -> tuple[str, str, str]:
    """Translate article title, body, and summary concurrently."""
    import asyncio
    results = await asyncio.gather(
        translate_text(title, target_lang),
        translate_text(body[:2000], target_lang),
        translate_text(summary, target_lang),
        return_exceptions=True,
    )
    t = results[0] if isinstance(results[0], str) else title
    b = results[1] if isinstance(results[1], str) else body
    s = results[2] if isinstance(results[2], str) else summary
    return t, b, s
