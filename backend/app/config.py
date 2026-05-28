"""Application configuration loaded from environment variables."""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI ShortVideo Platform"
    APP_VERSION: str = "0.6.1"
    DEBUG: bool = True
    API_PREFIX: str = "/api"

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@db:5432/shortvideo"

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # JWT
    SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Storage
    UPLOAD_DIR: str = "/app/storage/uploads"
    ASSETS_DIR: str = "/app/storage/assets"
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    # Whether to mount /storage as a public StaticFiles route in FastAPI.
    # Dev: true (convenient). Prod: false (forces auth-checked download).
    SERVE_STORAGE_DIRECT: bool = True

    # ---- AI provider (v0.6) ----
    # mock | claude | openai | openai_compatible
    AI_PROVIDER: str = "mock"
    # Generic knobs — used as fallback for whichever provider is active.
    AI_MODEL: str = ""
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_TIMEOUT_SECONDS: int = 60
    AI_MAX_RETRIES: int = 2
    AI_TEMPERATURE: float = 0.7
    AI_MAX_OUTPUT_TOKENS: int = 4096
    # Soft per-user daily cap on AI generation calls. <= 0 disables the cap.
    AI_DAILY_CALL_LIMIT_PER_USER: int = 200

    # Provider-specific overrides. When set, they win over the generic AI_* knobs.
    CLAUDE_MODEL: str = ""
    CLAUDE_API_KEY: str = ""
    OPENAI_MODEL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    LOCAL_LLM_BASE_URL: str = "http://host.docker.internal:11434/v1"
    LOCAL_LLM_MODEL: str = ""

    # CORS
    CORS_ORIGINS: str = "*"

    # ---- Rate limiting (Redis-backed) ----
    RATE_LIMIT_ENABLED: bool = True
    RL_LOGIN_IP_LIMIT: int = 10
    RL_LOGIN_IP_WINDOW: int = 60          # seconds
    RL_LOGIN_USER_LIMIT: int = 10         # failed attempts
    RL_LOGIN_USER_WINDOW: int = 300       # seconds
    RL_REGISTER_IP_LIMIT: int = 10
    RL_REGISTER_IP_WINDOW: int = 3600     # seconds
    RL_LOOKUP_IP_LIMIT: int = 30
    RL_LOOKUP_IP_WINDOW: int = 60         # seconds

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
