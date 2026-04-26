import asyncio
import calendar
import feedparser
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Any
import logging
import re

from config import settings

logger = logging.getLogger(__name__)

# Known timezone abbreviations → UTC offset in hours
# Russian sources often use MSK without numeric offset
_TZ_ABBR = {
    "MSK": 3, "МСК": 3,
    "MSD": 4,
    "UTC": 0, "GMT": 0, "Z": 0,
    "EST": -5, "EDT": -4,
    "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    "CET": 1, "CEST": 2,
    "EET": 2, "EEST": 3,
}


def _parse_published(entry) -> Optional[datetime]:
    """
    Parse article publish time as timezone-aware UTC datetime.

    Strategy (in order):
    1. Parse raw 'published' or 'updated' string via email.utils — handles RFC 2822
       with full tz offset (e.g. '+0300'), returning tz-aware datetime → convert to UTC.
    2. Check raw string for known tz abbreviations (e.g. 'MSK') and apply manually.
    3. Fall back to feedparser's published_parsed via calendar.timegm (correct only
       when feedparser already saw a numeric offset).
    4. If nothing works, return None (don't guess).
    """
    raw: Optional[str] = (
        getattr(entry, "published", None)
        or getattr(entry, "updated", None)
    )

    # Strategy 1: RFC 2822 with numeric offset → email.utils handles it perfectly
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            # dt is tz-aware; convert to UTC naive for uniform storage
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass

        # Strategy 2: try known abbreviation in raw string (e.g. "MSK", "UTC")
        for abbr, offset_h in _TZ_ABBR.items():
            if abbr in raw:
                # Strip the abbreviation and parse the rest
                cleaned = raw.replace(abbr, "+0000").strip()
                try:
                    dt = parsedate_to_datetime(cleaned)
                    # Apply real offset
                    utc_dt = dt.replace(tzinfo=timezone.utc) - timedelta(hours=offset_h)
                    return utc_dt.replace(tzinfo=None)
                except Exception:
                    pass

    # Strategy 3: feedparser's struct_time (already converted to UTC by feedparser
    # when a numeric offset was present; unreliable without offset)
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if t:
        try:
            return datetime.utcfromtimestamp(calendar.timegm(t))
        except Exception:
            pass

    return None


def _now_utc() -> datetime:
    """Return current time as naive UTC datetime (consistent with stored dates)."""
    return datetime.utcnow()


# ─── Raw Article Data ─────────────────────────────────────────────────────────

class RawArticle:
    def __init__(self):
        self.title: str = ""
        self.url: str = ""
        self.body: str = ""
        self.published_at: Optional[datetime] = None
        self.language: str = "en"
        self.source_name: str = ""


# ─── RSS Crawler ──────────────────────────────────────────────────────────────

async def fetch_rss(url: str, source_name: str) -> List[RawArticle]:
    """Fetch and parse an RSS/Atom feed."""
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "NewsMonitor/1.0"})
            resp.raise_for_status()
            content = resp.text
    except Exception as e:
        logger.warning(f"RSS fetch failed for {url}: {e}")
        return []

    feed = feedparser.parse(content)
    articles: List[RawArticle] = []

    for entry in feed.entries[:settings.max_articles_per_feed]:
        art = RawArticle()
        art.title = entry.get("title", "").strip()
        art.url = entry.get("link", "").strip()
        art.source_name = source_name

        # Try to get body
        if hasattr(entry, "content") and entry.content:
            art.body = _clean_html(entry.content[0].get("value", ""))
        elif hasattr(entry, "summary"):
            art.body = _clean_html(entry.get("summary", ""))

        # Parse published date — always UTC
        art.published_at = _parse_published(entry)

        if art.title and art.url:
            articles.append(art)

    logger.info(f"RSS {url}: fetched {len(articles)} articles")
    return articles


# ─── HTML Crawler ─────────────────────────────────────────────────────────────

async def fetch_html_page(url: str, source_name: str) -> List[RawArticle]:
    """Crawl a plain HTML page for article links."""
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "NewsMonitor/1.0"})
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.warning(f"HTML fetch failed for {url}: {e}")
        return []

    soup = BeautifulSoup(html, "lxml")
    articles: List[RawArticle] = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)

        # Build absolute URL
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            continue

        if href in seen_urls or len(text) < 20:
            continue

        # Heuristic: looks like article link
        if _is_article_url(href):
            seen_urls.add(href)
            art = RawArticle()
            art.title = text[:300]
            art.url = href
            art.source_name = source_name
            articles.append(art)

        if len(articles) >= settings.max_articles_per_feed:
            break

    # Fetch bodies concurrently (limited)
    await _enrich_articles_bodies(articles[:20])

    logger.info(f"HTML {url}: found {len(articles)} article links")
    return articles


async def fetch_article_body(url: str) -> str:
    """Fetch and extract the main body text of a single article."""
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "NewsMonitor/1.0"})
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Remove noise
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # Try known article containers
    for selector in ["article", "main", '[class*="article"]', '[class*="content"]', '[class*="post-body"]']:
        el = soup.select_one(selector)
        if el:
            return el.get_text(separator="\n", strip=True)[:5000]

    return soup.body.get_text(separator="\n", strip=True)[:5000] if soup.body else ""


# ─── Agent Crawler (Playwright) ───────────────────────────────────────────────

async def fetch_with_playwright(url: str, source_name: str) -> List[RawArticle]:
    """Headless browser fallback for JS-heavy pages."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            await browser.close()

        return await _parse_rendered_html(html, url, source_name)
    except Exception as e:
        logger.warning(f"Playwright failed for {url}: {e}")
        return []


# ─── Dispatcher ───────────────────────────────────────────────────────────────

async def crawl_source(source_id: int, url: str, name: str, source_type: str) -> List[RawArticle]:
    """Dispatch to the correct crawler based on source_type."""
    if source_type == "rss":
        articles = await fetch_rss(url, name)
    elif source_type == "html":
        articles = await fetch_html_page(url, name)
    elif source_type == "agent":
        articles = await fetch_with_playwright(url, name)
    else:
        articles = await fetch_rss(url, name)

    # Fallback: try RSS if HTML returned nothing
    if not articles and source_type == "html":
        # Try appending /feed or /rss
        for suffix in ["/feed", "/rss", "/feed.xml", "/atom.xml"]:
            rss_url = url.rstrip("/") + suffix
            articles = await fetch_rss(rss_url, name)
            if articles:
                break

    return articles


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clean_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator=" ", strip=True)[:5000]


def _is_article_url(url: str) -> bool:
    """Heuristic: does this URL look like an article?"""
    noise = ["#", "javascript:", "mailto:", "/tag/", "/category/", "/author/",
             "/page/", "?s=", "wp-content", ".jpg", ".png", ".pdf", ".zip"]
    url_lower = url.lower()
    if any(n in url_lower for n in noise):
        return False
    # Must have some path depth
    from urllib.parse import urlparse
    path = urlparse(url).path
    return len(path) > 5 and "/" in path[1:]


async def _enrich_articles_bodies(articles: List[RawArticle]) -> None:
    """Fetch article bodies concurrently."""
    async def _fetch_one(art: RawArticle):
        art.body = await fetch_article_body(art.url)

    await asyncio.gather(*[_fetch_one(a) for a in articles], return_exceptions=True)


async def _parse_rendered_html(html: str, base_url: str, source_name: str) -> List[RawArticle]:
    """Parse rendered HTML from Playwright into RawArticle list."""
    soup = BeautifulSoup(html, "lxml")
    articles: List[RawArticle] = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)
        if not href.startswith("http"):
            from urllib.parse import urlparse, urljoin
            href = urljoin(base_url, href)
        if href in seen or len(text) < 20:
            continue
        if _is_article_url(href):
            seen.add(href)
            art = RawArticle()
            art.title = text[:300]
            art.url = href
            art.source_name = source_name
            articles.append(art)
        if len(articles) >= settings.max_articles_per_feed:
            break

    return articles
