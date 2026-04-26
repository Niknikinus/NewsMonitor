from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.database import get_db, AppSettingsModel
from models.schemas import SettingsUpdate, SettingsOut, ConnectionTestResult
from config import settings

router = APIRouter(prefix="/settings", tags=["settings"])

SETTING_KEYS = [
    "grok_api_key", "openai_api_key", "deepl_api_key",
    "preferred_language", "local_llm_enabled", "local_llm_base",
    "dedup_cosine_threshold", "cluster_cosine_threshold",
]


async def _load_settings_from_db(db: AsyncSession):
    """Load all settings from DB into the global settings object."""
    result = await db.execute(select(AppSettingsModel))
    rows = result.scalars().all()
    for row in rows:
        if hasattr(settings, row.key):
            val = row.value
            field = settings.model_fields.get(row.key)
            if field and field.annotation == bool:
                val = val.lower() in ("true", "1", "yes")
            elif field and field.annotation == float:
                try:
                    val = float(val)
                except ValueError:
                    pass
            setattr(settings, row.key, val)


async def _save_setting(db: AsyncSession, key: str, value: str):
    result = await db.execute(
        select(AppSettingsModel).where(AppSettingsModel.key == key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSettingsModel(key=key, value=value))
    await db.commit()


@router.get("", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    await _load_settings_from_db(db)
    return SettingsOut(
        grok_api_key_set=bool(settings.grok_api_key),
        openai_api_key_set=bool(settings.openai_api_key),
        deepl_api_key_set=bool(settings.deepl_api_key),
        preferred_language=settings.preferred_language,
        local_llm_enabled=settings.local_llm_enabled,
        local_llm_base=settings.local_llm_base,
        dedup_cosine_threshold=settings.dedup_cosine_threshold,
        cluster_cosine_threshold=settings.cluster_cosine_threshold,
        grok_model=settings.grok_model,
        openai_embedding_model=settings.openai_embedding_model,
    )


@router.patch("", response_model=SettingsOut)
async def update_settings(data: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    for key, val in data.model_dump(exclude_none=True).items():
        if hasattr(settings, key):
            setattr(settings, key, val)
            await _save_setting(db, key, str(val))

    return await get_settings(db)


@router.post("/test/grok", response_model=ConnectionTestResult)
async def test_grok():
    from services.ai_processor import test_grok_connection
    success, message = await test_grok_connection()
    return ConnectionTestResult(service="grok-3", success=success, message=message)


@router.post("/test/embeddings", response_model=ConnectionTestResult)
async def test_embeddings():
    from services.ai_processor import test_embedding_connection
    success, message = await test_embedding_connection()
    return ConnectionTestResult(service="openai-embeddings", success=success, message=message)


@router.post("/test/deepl", response_model=ConnectionTestResult)
async def test_deepl():
    from services.translation import translate_text
    try:
        result = await translate_text("Hello world", target_lang="ru")
        if result and result != "Hello world":
            return ConnectionTestResult(service="deepl", success=True, message=f"OK: {result}")
        return ConnectionTestResult(service="deepl", success=False, message="Translation unchanged — check key")
    except Exception as e:
        return ConnectionTestResult(service="deepl", success=False, message=str(e))
