from __future__ import annotations

import logging
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import URL
from sqlalchemy.engine.url import make_url


logger = logging.getLogger(__name__)


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
    pghost: str | None = None
    pgport: int | None = None
    pgdatabase: str | None = None
    pguser: str | None = None
    pgpassword: str | None = None

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
        fallback_url = "sqlite+aiosqlite:///./study_assistant.db"
        raw_url = (self.database_url or "").strip()
        if raw_url:
            if raw_url.startswith("postgresql://"):
                candidate = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif raw_url.startswith("postgres://"):
                candidate = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
            else:
                candidate = raw_url

            try:
                make_url(candidate)
            except ArgumentError:
                logger.warning("Invalid DATABASE_URL provided; attempting Postgres component fallback.")
            else:
                return candidate

        component_url = self._build_component_postgres_url()
        if component_url is not None:
            logger.info("Using Postgres component environment variables to build DATABASE_URL.")
            return component_url

        logger.warning("No valid DATABASE_URL detected; falling back to local SQLite.")
        return fallback_url

    @property
    def database_backend_label(self) -> str:
        resolved = self.resolved_database_url
        if resolved.startswith("postgresql+asyncpg://"):
            return "postgresql+asyncpg"
        if resolved.startswith("sqlite+aiosqlite:///./"):
            return "sqlite+aiosqlite (local fallback)"
        if resolved.startswith("sqlite+aiosqlite:"):
            return "sqlite+aiosqlite"
        return resolved.split("://", 1)[0]

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.telegram_webhook_path}"

    def _build_component_postgres_url(self) -> str | None:
        if not all([self.pguser, self.pgpassword, self.pghost, self.pgdatabase]):
            return None

        port = self.pgport or 5432
        return URL.create(
            "postgresql+asyncpg",
            username=self.pguser,
            password=self.pgpassword,
            host=self.pghost,
            port=port,
            database=self.pgdatabase,
        ).render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
