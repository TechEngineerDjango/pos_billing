from urllib.parse import quote_plus
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, computed_field


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    # REQUIRED — app will crash on startup if not set in .env
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours work shift

    # Seed credentials — MUST be overridden in production
    SUPERADMIN_PASSWORD: str = "superadmin"
    OWNER_PASSWORD: str = "owner"

    # Database Components
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DB_HOST: str = "localhost"
    DB_PORT: str = "5432"
    DB_NAME: str = "pos"

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        # Construct PostgreSQL URL with safe password encoding
        safe_password = quote_plus(self.DB_PASSWORD)
        return f"postgresql+asyncpg://{self.DB_USER}:{safe_password}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # Security
    IS_PRODUCTION: bool = False
    ALLOWED_ORIGINS: str = "http://localhost:8000"

    # Default Printer Settings (can be overridden by Shop)
    DEFAULT_PRINTER_IP: str = "mock"

    # Redis — used for feature caching and rate limiting
    REDIS_URL: str = "redis://localhost:6379/0"
    FEATURE_CACHE_TTL: int = 300  # seconds (5 minutes)

    # NOTE: Set SECRET_KEY in .env to a strong random string (e.g. openssl rand -hex 32)
    # NOTE: Set IS_PRODUCTION=True in .env for production environments


settings = Settings()
