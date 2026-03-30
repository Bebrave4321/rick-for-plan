from __future__ import annotations

from collections.abc import AsyncIterator
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from study_assistant.core.config import get_settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger.info("Configuring database engine with backend: %s", settings.database_backend_label)
engine = create_async_engine(settings.resolved_database_url, echo=settings.debug, future=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    from study_assistant.models import entities  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
