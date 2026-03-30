from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from study_assistant.core.config import Settings
from study_assistant.db.session import Base
from study_assistant.models.entities import StudyTask, TaskSource, TaskStatus, User
from study_assistant.repositories.assistant_repository import AssistantRepository
from study_assistant.schemas.contracts import CreateUserRequest
from study_assistant.services.task_executor import TaskExecutor


@pytest.mark.asyncio
async def test_bulk_replan_spills_to_next_evening_when_today_window_is_full():
    db_path = Path("tests") / ".assistant-task-executor.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db_path}", default_timezone="Asia/Seoul")
    executor = TaskExecutor(settings.timezone)
    now = datetime(2026, 3, 27, 18, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            repo = AssistantRepository(session)
            await repo.get_or_create_user(
                CreateUserRequest(
                    telegram_user_id=9101,
                    telegram_chat_id=9101,
                    display_name="LG",
                ),
                timezone=settings.default_timezone,
            )
            user = (await session.execute(select(User).where(User.telegram_user_id == 9101))).scalar_one()

            first_task = StudyTask(
                user_id=user.id,
                title="수학",
                start_at=now - timedelta(hours=3),
                end_at=now - timedelta(hours=1),
                source=TaskSource.MANUAL,
                status=TaskStatus.MISSED,
            )
            second_task = StudyTask(
                user_id=user.id,
                title="영어",
                start_at=now - timedelta(hours=2),
                end_at=now,
                source=TaskSource.MANUAL,
                status=TaskStatus.MISSED,
            )
            session.add_all([first_task, second_task])
            await session.flush()

            await executor.replan_multiple_tasks(repo, [first_task, second_task], now=now)
            await session.commit()

        async with session_factory() as session:
            tasks = (
                await session.execute(select(StudyTask).order_by(StudyTask.title.asc()))
            ).scalars().all()
            by_title = {task.title: task for task in tasks}

            assert by_title["수학"].start_at.hour == 19
            assert by_title["수학"].start_at.date() == now.date()
            assert by_title["영어"].start_at.hour == 19
            assert by_title["영어"].start_at.date() == now.date() + timedelta(days=1)
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()
