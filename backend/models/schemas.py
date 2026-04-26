from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime

class FeedCreate(BaseModel):
    name: str
    description: str = ""
    language: str = "ru"
    schedule_days: List[int] = [0, 1, 2, 3, 4]
    delivery_times: List[str] = ["08:00", "18:00"]
    mode: str = "standard"
    important_only: bool = False

class FeedUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    is_active: Optional[bool] = None
    schedule_days: Optional[List[int]] = None
    delivery_times: Optional[List[str]] = None
    mode: Optional[str] = None
    important_only: Optional[bool] = None

class FeedOut(BaseModel):
    id: int
    name: str
    description: str
    language: str
    is_active: bool
    schedule_days: List[int]
    delivery_times: List[str]
    mode: str
    important_only: bool
    created_at: datetime
    last_run_at: Optional[datetime] = None
    last_delivered_at: Optional[datetime] = None
    source_count: int = 0
    unread_cluster_count: int = 0
    class Config:
        from_attributes = True

class SourceCreate(BaseModel):
    feed_id: int
    name: str
    url: str
    tier: int = 2
    rating: int = 50
    source_type: str = "rss"
    language: str = "en"

class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    tier: Optional[int] = None
    rating: Optional[int] = None
    is_active: Optional[bool] = None
    source_type: Optional[str] = None

class SourceOut(BaseModel):
    id: int
    feed_id: int
    name: str
    url: str
    tier: int
    rating: int
    is_active: bool
    source_type: str
    language: str
    sample_headlines: List[str]
    last_fetched_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class SourceDiscoverRequest(BaseModel):
    feed_id: int
    topic_description: str

class ArticleOut(BaseModel):
    id: int
    source_id: int
    cluster_id: Optional[int] = None
    title: str
    url: str
    summary: str
    why_it_matters: str
    key_angle: str
    language: str
    published_at: Optional[datetime] = None
    fetched_at: datetime
    is_translated: bool
    source_name: str = ""
    class Config:
        from_attributes = True

class ClusterOut(BaseModel):
    id: int
    feed_id: int
    title: str
    summary: str
    why_it_matters: str
    key_angles: List[str]
    article_count: int
    created_at: datetime
    latest_article_at: Optional[datetime] = None
    is_read: bool
    articles: List[ArticleOut] = []
    class Config:
        from_attributes = True

class SettingsUpdate(BaseModel):
    grok_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    deepl_api_key: Optional[str] = None
    preferred_language: Optional[str] = None
    local_llm_enabled: Optional[bool] = None
    local_llm_base: Optional[str] = None
    dedup_cosine_threshold: Optional[float] = None
    cluster_cosine_threshold: Optional[float] = None

class SettingsOut(BaseModel):
    grok_api_key_set: bool
    openai_api_key_set: bool
    deepl_api_key_set: bool
    preferred_language: str
    local_llm_enabled: bool
    local_llm_base: str
    dedup_cosine_threshold: float
    cluster_cosine_threshold: float
    grok_model: str
    openai_embedding_model: str

class RunFeedRequest(BaseModel):
    feed_id: int
    force: bool = False

class PipelineStatus(BaseModel):
    status: str
    message: str
    articles_fetched: int = 0
    articles_new: int = 0
    clusters_created: int = 0

class ConnectionTestResult(BaseModel):
    service: str
    success: bool
    message: str

class ExportRequest(BaseModel):
    feed_id: int
    format: str = "markdown"
    cluster_ids: Optional[List[int]] = None
