import os
from pydantic_settings import BaseSettings
from pathlib import Path

APP_DIR = Path.home() / ".newsmonitoer"
APP_DIR.mkdir(exist_ok=True)

class Settings(BaseSettings):
    app_name: str = "NewsMonitor"
    database_url: str = f"sqlite+aiosqlite:///{APP_DIR}/news.db"
    
    # API Keys (user-provided, stored in DB/config)
    grok_api_key: str = ""
    grok_api_base: str = "https://models.inference.ai.azure.com"
    grok_model: str = "grok-3"
    
    openai_api_key: str = ""
    openai_api_base: str = "https://models.inference.ai.azure.com"
    openai_embedding_model: str = "text-embedding-3-large"
    
    # Local LLM
    local_llm_enabled: bool = False
    local_llm_base: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen2.5:3b"
    
    # Translation
    deepl_api_key: str = ""
    preferred_language: str = "en"  # "en" or "ru"
    
    # Similarity thresholds
    dedup_cosine_threshold: float = 0.92
    cluster_cosine_threshold: float = 0.75
    
    # Crawler
    max_articles_per_feed: int = 50
    request_timeout: int = 15
    
    class Config:
        env_file = str(APP_DIR / ".env")

settings = Settings()
