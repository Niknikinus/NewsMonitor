import asyncio
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List

from models.database import get_db, FeedModel, SourceModel, ClusterModel
from models.schemas import FeedCreate, FeedUpdate, FeedOut, RunFeedRequest, PipelineStatus

router = APIRouter(prefix="/feeds", tags=["feeds"])

# Simple in-memory pipeline status tracker
_pipeline_status: dict[int, dict] = {}  # feed_id -> {running, message, clusters_before}


async def _run_pipeline_tracked(feed_id: int, force: bool):
    """Wrapper that tracks pipeline status in memory with live progress."""
    from services.scheduler import run_feed_pipeline

    _pipeline_status[feed_id] = {"running": True, "message": "Запуск…", "done": False}

    def on_status(msg: str):
        _pipeline_status[feed_id]["message"] = msg

    try:
        result = await run_feed_pipeline(feed_id, force, on_status=on_status)
        _pipeline_status[feed_id] = {
            "running": False,
            "done": True,
            "message": result.message,
            "articles_new": result.articles_new,
            "clusters_created": result.clusters_created,
        }
    except Exception as e:
        _pipeline_status[feed_id] = {
            "running": False, "done": True, "message": str(e), "error": True
        }



@router.get("", response_model=List[FeedOut])
async def list_feeds(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedModel))
    feeds = result.scalars().all()

    out = []
    for feed in feeds:
        src_count = await db.execute(
            select(func.count()).where(SourceModel.feed_id == feed.id)
        )
        cluster_count = await db.execute(
            select(func.count()).where(
                ClusterModel.feed_id == feed.id,
                ClusterModel.is_read == False,
            )
        )
        fo = FeedOut.model_validate(feed)
        fo.source_count = src_count.scalar() or 0
        fo.unread_cluster_count = cluster_count.scalar() or 0
        out.append(fo)

    return out


@router.post("", response_model=FeedOut)
async def create_feed(data: FeedCreate, db: AsyncSession = Depends(get_db)):
    # Max 15 feeds
    count_result = await db.execute(select(func.count()).select_from(FeedModel))
    count = count_result.scalar() or 0
    if count >= 15:
        raise HTTPException(status_code=400, detail="Maximum 15 feeds allowed")

    feed = FeedModel(**data.model_dump())
    db.add(feed)
    await db.commit()
    await db.refresh(feed)

    fo = FeedOut.model_validate(feed)
    fo.source_count = 0
    fo.unread_cluster_count = 0
    return fo


@router.get("/{feed_id}", response_model=FeedOut)
async def get_feed(feed_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedModel).where(FeedModel.id == feed_id))
    feed = result.scalar_one_or_none()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    return FeedOut.model_validate(feed)


@router.patch("/{feed_id}", response_model=FeedOut)
async def update_feed(feed_id: int, data: FeedUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedModel).where(FeedModel.id == feed_id))
    feed = result.scalar_one_or_none()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    for key, val in data.model_dump(exclude_none=True).items():
        setattr(feed, key, val)

    await db.commit()
    await db.refresh(feed)

    # Reschedule so new delivery times take effect immediately
    try:
        from services.scheduler import schedule_feeds
        await schedule_feeds()
    except Exception as e:
        pass  # Don't fail the update if scheduler is unavailable

    fo = FeedOut.model_validate(feed)
    fo.source_count = 0
    fo.unread_cluster_count = 0
    return fo


@router.delete("/{feed_id}")
async def delete_feed(feed_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedModel).where(FeedModel.id == feed_id))
    feed = result.scalar_one_or_none()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    await db.delete(feed)
    await db.commit()
    return {"deleted": feed_id}


@router.post("/{feed_id}/run", response_model=PipelineStatus)
async def run_feed(
    feed_id: int,
    background_tasks: BackgroundTasks,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Trigger pipeline run for a feed (runs in background)."""
    result = await db.execute(select(FeedModel).where(FeedModel.id == feed_id))
    feed = result.scalar_one_or_none()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    # If already running, return current status
    status = _pipeline_status.get(feed_id, {})
    if status.get("running"):
        return PipelineStatus(
            status="running",
            message=status.get("message", "Идёт сбор…"),
        )

    background_tasks.add_task(_run_pipeline_tracked, feed_id, force)

    return PipelineStatus(
        status="started",
        message=f"Сбор запущен: '{feed.name}'",
    )


@router.get("/{feed_id}/status", response_model=PipelineStatus)
async def get_feed_status(feed_id: int):
    """Poll pipeline status for a feed."""
    status = _pipeline_status.get(feed_id)
    if not status:
        return PipelineStatus(status="idle", message="Нет активного сбора")

    if status.get("running"):
        return PipelineStatus(status="running", message=status.get("message", "Идёт сбор…"))

    if status.get("done"):
        return PipelineStatus(
            status="done",
            message=status.get("message", "Готово"),
            articles_new=status.get("articles_new", 0),
            clusters_created=status.get("clusters_created", 0),
        )

    return PipelineStatus(status="idle", message="")


@router.get("/{feed_id}/notification")
async def get_notification(feed_id: int):
    """Poll whether a scheduled digest is ready. Returns and clears the notification."""
    from services.scheduler import get_delivery_notification
    notif = get_delivery_notification(feed_id)
    if notif:
        return notif
    return {"ready": False}


@router.post("/{feed_id}/mark-read")
async def mark_all_read(feed_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ClusterModel).where(
            ClusterModel.feed_id == feed_id,
            ClusterModel.is_read == False,
        )
    )
    clusters = result.scalars().all()
    for c in clusters:
        c.is_read = True
    await db.commit()
    return {"marked_read": len(clusters)}
