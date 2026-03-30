from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from study_assistant.core.config import Settings
from study_assistant.db.session import Base
from study_assistant.repositories.assistant_repository import AssistantRepository
from study_assistant.schemas.contracts import CreateUserRequest
from study_assistant.services.context_assembler import ContextAssembler


@pytest.mark.asyncio
async def test_context_assembler_includes_recent_dialogue():
    db_path = Path("tests") / ".assistant-context.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db_path}", default_timezone="Asia/Seoul")
    assembler = ContextAssembler(settings.timezone)
    now = datetime(2026, 3, 27, 18, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_or_create_user(
                CreateUserRequest(
                    telegram_user_id=9001,
                    telegram_chat_id=9001,
                    display_name="LG",
                ),
                timezone=settings.default_timezone,
            )
            conversation = await repo.get_or_create_daily_conversation(user.id, now.date())
            await repo.append_conversation_turn(
                conversation,
                role="user",
                text="오늘 6시로 옮겨줘",
                occurred_at=now,
            )
            await repo.append_conversation_turn(
                conversation,
                role="assistant",
                text="좋아요. 오늘 18:00으로 옮겨둘게요.",
                occurred_at=now,
            )
            await session.commit()

        async with session_factory() as session:
            repo = AssistantRepository(session)
            context = await assembler.build_message_context(
                repo,
                telegram_user_id=9001,
                chat_id=9001,
                display_name="LG",
                default_timezone=settings.default_timezone,
                now=now,
            )

        assert [turn["role"] for turn in context.recent_dialogue] == ["user", "assistant"]
        assert context.recent_dialogue[0]["text"] == "오늘 6시로 옮겨줘"
        assert "18:00" in context.recent_dialogue[1]["text"]
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()
