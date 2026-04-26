from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from datetime import datetime
from pathlib import Path

APP_DIR = Path.home() / ".newsmonitoer"
APP_DIR.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{APP_DIR}/news.db"


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(200), default="")
    role = Column(String(20), default="pending")  # pending | approved | admin
    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    notes = Column(Text, default="")


class FeedModel(Base):
    __tablename__ = "feeds"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # null = legacy/local
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    language = Column(String(10), default="ru")
    is_active = Column(Boolean, default=True)
    schedule_days = Column(JSON, default=list)
    delivery_times = Column(JSON, default=list)
    mode = Column(String(30), default="standard")
    important_only = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)
    last_delivered_at = Column(DateTime, nullable=True)
    sources = relationship("SourceModel", back_populates="feed", cascade="all, delete-orphan")
    clusters = relationship("ClusterModel", back_populates="feed", cascade="all, delete-orphan")


class SourceModel(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    feed_id = Column(Integer, ForeignKey("feeds.id"), nullable=False)
    name = Column(String(200), nullable=False)
    url = Column(String(500), nullable=False)
    tier = Column(Integer, default=2)
    rating = Column(Integer, default=50)
    is_active = Column(Boolean, default=True)
    source_type = Column(String(20), default="rss")
    language = Column(String(10), default="en")
    sample_headlines = Column(JSON, default=list)
    last_fetched_at = Column(DateTime, nullable=True)
    feed = relationship("FeedModel", back_populates="sources")
    articles = relationship("ArticleModel", back_populates="source")


class ArticleModel(Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=True)
    title = Column(String(500), nullable=False)
    original_title = Column(String(500), default="")
    url = Column(String(1000), nullable=False, unique=True)
    body = Column(Text, default="")
    summary = Column(Text, default="")
    why_it_matters = Column(Text, default="")
    key_angle = Column(String(300), default="")
    language = Column(String(10), default="en")
    published_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    is_translated = Column(Boolean, default=False)
    is_duplicate = Column(Boolean, default=False)
    is_delivered = Column(Boolean, default=False)
    embedding_id = Column(String(100), nullable=True)
    source = relationship("SourceModel", back_populates="articles")
    cluster = relationship("ClusterModel", back_populates="articles")


class ClusterModel(Base):
    __tablename__ = "clusters"
    id = Column(Integer, primary_key=True)
    feed_id = Column(Integer, ForeignKey("feeds.id"), nullable=False)
    title = Column(String(500), nullable=False)
    summary = Column(Text, default="")
    why_it_matters = Column(Text, default="")
    key_angles = Column(JSON, default=list)
    article_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    latest_article_at = Column(DateTime, nullable=True)
    is_read = Column(Boolean, default=False)
    feed = relationship("FeedModel", back_populates="clusters")
    articles = relationship("ArticleModel", back_populates="cluster")


class EmbeddingModel(Base):
    __tablename__ = "embeddings"
    id = Column(Integer, primary_key=True)
    article_url = Column(String(1000), nullable=False, unique=True)
    embedding = Column(Text, nullable=False)
    model = Column(String(100), default="text-embedding-3-large")
    created_at = Column(DateTime, default=datetime.utcnow)


class AppSettingsModel(Base):
    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(100), nullable=False, unique=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
