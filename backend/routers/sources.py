from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from models.database import get_db, SourceModel
from models.schemas import SourceCreate, SourceUpdate, SourceOut, SourceDiscoverRequest

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/feed/{feed_id}", response_model=List[SourceOut])
async def list_sources(feed_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceModel).where(SourceModel.feed_id == feed_id)
    )
    return result.scalars().all()


@router.post("", response_model=SourceOut)
async def create_source(data: SourceCreate, db: AsyncSession = Depends(get_db)):
    source = SourceModel(**data.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(source_id: int, data: SourceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SourceModel).where(SourceModel.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    for key, val in data.model_dump(exclude_none=True).items():
        setattr(source, key, val)
    await db.commit()
    await db.refresh(source)
    return source


@router.delete("/{source_id}")
async def delete_source(source_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SourceModel).where(SourceModel.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    await db.commit()
    return {"deleted": source_id}


@router.post("/discover", response_model=List[SourceOut])
async def discover_sources(
    data: SourceDiscoverRequest,
    db: AsyncSession = Depends(get_db),
):
    """Use Grok-3 to discover relevant news sources for a topic."""
    from services.ai_processor import discover_sources as ai_discover

    suggestions = await ai_discover(data.topic_description)
    created = []

    for s in suggestions:
        source = SourceModel(
            feed_id=data.feed_id,
            name=s.get("name", "Unknown"),
            url=s.get("url", ""),
            tier=s.get("tier", 2),
            rating=s.get("rating", 50),
            language=s.get("language", "en"),
            sample_headlines=s.get("sample_headlines", []),
            is_active=False,  # User must enable manually
        )
        db.add(source)
        created.append(source)

    await db.commit()
    for s in created:
        await db.refresh(s)

    return created
