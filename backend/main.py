import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import json
from pathlib import Path

from database import init_db
from routers import feeds, sources, articles, settings as settings_router
from routers.auth import router as auth_router, admin_router, migrate_add_user_columns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class UTCJSONResponse(JSONResponse):
    """JSON response that serializes naive datetimes with 'Z' suffix."""
    def render(self, content) -> bytes:
        return json.dumps(content, default=self._default,
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    @staticmethod
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initialising database…")
    await init_db()
    await migrate_add_user_columns()

    logger.info("Setting up feed schedules…")
    try:
        from services.scheduler import schedule_feeds
        await schedule_feeds()
    except Exception as e:
        logger.warning(f"Scheduler setup failed: {e}")

    logger.info("NewsMonitor backend ready ✓")
    yield

    from services.scheduler import get_scheduler
    scheduler = get_scheduler()
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("Shutdown complete")


app = FastAPI(
    title="NewsMonitor API",
    description="AI-powered news monitoring backend",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=UTCJSONResponse,
)

# CORS — allow both local macOS app and web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(feeds.router)
app.include_router(sources.router)
app.include_router(articles.router)
app.include_router(settings_router.router)

# Serve web frontend as static files if web/ directory exists
WEB_DIR = Path(__file__).parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    logger.info(f"Web frontend served at /app from {WEB_DIR}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "NewsMonitor Backend"}


@app.get("/")
async def root():
    return {"app": "NewsMonitor", "version": "1.0.0",
            "web_ui": "/app", "docs": "/docs"}
