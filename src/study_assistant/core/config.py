from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    debug: bool = False
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./study_assistant.db"

    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_webhook_path: str = "/api/telegram/webhook"

    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"

    default_timezone: str = "Asia/Seoul"
    default_study_window_start: str = Field(default="07:00:00")
    default_study_window_end: str = Field(default="23:00:00")
    scanner_interval_seconds: int = 60
    data_retention_weeks: int = Field(default=1, ge=1, le=12)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.default_timezone)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self.database_url

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.telegram_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
