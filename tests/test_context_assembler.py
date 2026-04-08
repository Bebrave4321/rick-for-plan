from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from study_assistant.core.config import Settings
from study_assistant.db.session import Base
from study_assistant.models.entities import PendingPromptType, StudyTask, TaskSource, TaskStatus
from study_assistant.repositories.assistant_repository import AssistantRepository
from study_assistant.schemas.contracts import CreateUserRequest
from study_assistant.services.context_assembler import ContextAssembler


@pytest.mark.asyncio
async def test_context_assembler_includes_recent_dialogue_and_derived_turns():
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
                text="Can you move it to 6 PM?",
                occurred_at=now,
            )
            await repo.append_conversation_turn(
                conversation,
                role="assistant",
                text="Sure, I can move it to 18:00.",
                occurred_at=now,
            )
            session.add(
                StudyTask(
                    user_id=user.id,
                    title="English reading",
                    start_at=now,
                    end_at=now.replace(hour=19),
                    source=TaskSource.MANUAL,
                    status=TaskStatus.IN_PROGRESS,
                    pending_prompt_type=PendingPromptType.RESCHEDULE,
                )
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
        assert context.dialogue_transcript is not None
        assert "user: Can you move it to 6 PM?" in context.dialogue_transcript
        assert "assistant: Sure, I can move it to 18:00." in context.dialogue_transcript
        assert context.last_user_turn is not None
        assert context.last_user_turn["text"] == "Can you move it to 6 PM?"
        assert context.last_assistant_turn is not None
        assert "18:00" in context.last_assistant_turn["text"]
        assert context.active_prompt_kind == "reschedule"
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_context_assembler_includes_nearby_after_midnight_tasks():
    db_path = Path("tests") / ".assistant-context-upcoming.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db_path}", default_timezone="Asia/Seoul")
    assembler = ContextAssembler(settings.timezone)
    now = datetime(2026, 3, 31, 21, 30, tzinfo=ZoneInfo("Asia/Seoul"))

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_or_create_user(
                CreateUserRequest(
                    telegram_user_id=9002,
                    telegram_chat_id=9002,
                    display_name="LG",
                ),
                timezone=settings.default_timezone,
            )
            session.add(
                StudyTask(
                    user_id=user.id,
                    title="Late-night English",
                    start_at=datetime(2026, 4, 1, 1, 0, tzinfo=ZoneInfo("Asia/Seoul")),
                    end_at=datetime(2026, 4, 1, 2, 0, tzinfo=ZoneInfo("Asia/Seoul")),
                    source=TaskSource.MANUAL,
                    status=TaskStatus.PLANNED,
                )
            )
            await session.commit()

        async with session_factory() as session:
            repo = AssistantRepository(session)
            context = await assembler.build_message_context(
                repo,
                telegram_user_id=9002,
                chat_id=9002,
                display_name="LG",
                default_timezone=settings.default_timezone,
                now=now,
            )

        assert any(task.title == "Late-night English" for task in context.today_tasks)
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()
