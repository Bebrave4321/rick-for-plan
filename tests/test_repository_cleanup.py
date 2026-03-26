from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from study_assistant.models.entities import (
    ChangeType,
    DailyConversation,
    ResponseSource,
    StudyTask,
    TaskChangeLog,
    TaskResponse,
    TaskSource,
    User,
    WeeklyPlan,
    WeeklyPlanStatus,
)
from study_assistant.db.session import Base
from study_assistant.repositories.assistant_repository import AssistantRepository


@pytest.mark.asyncio
async def test_prune_historical_data_removes_previous_week_records():
    timezone = ZoneInfo("Asia/Seoul")
    db_path = Path("tests") / ".cleanup-test.db"
    if db_path.exists():
        db_path.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = User(telegram_user_id=1001, telegram_chat_id=1001, timezone="Asia/Seoul")
        session.add(user)
        await session.flush()

        old_plan = WeeklyPlan(
            user_id=user.id,
            week_start_date=date(2026, 3, 23),
            status=WeeklyPlanStatus.DRAFT,
            plan_origin=TaskSource.HEURISTIC,
            draft_summary="old plan",
        )
        current_plan = WeeklyPlan(
            user_id=user.id,
            week_start_date=date(2026, 3, 30),
            status=WeeklyPlanStatus.DRAFT,
            plan_origin=TaskSource.HEURISTIC,
            draft_summary="current plan",
        )
        session.add_all([old_plan, current_plan])
        await session.flush()

        old_task = StudyTask(
            user_id=user.id,
            weekly_plan_id=old_plan.id,
            title="Old task",
            start_at=datetime(2026, 3, 29, 19, 0, tzinfo=timezone),
            end_at=datetime(2026, 3, 29, 20, 0, tzinfo=timezone),
            source=TaskSource.HEURISTIC,
        )
        current_task = StudyTask(
            user_id=user.id,
            weekly_plan_id=current_plan.id,
            title="Current task",
            start_at=datetime(2026, 3, 31, 19, 0, tzinfo=timezone),
            end_at=datetime(2026, 3, 31, 20, 0, tzinfo=timezone),
            source=TaskSource.HEURISTIC,
        )
        session.add_all([old_task, current_task])
        await session.flush()

        session.add_all(
            [
                TaskResponse(
                    task_id=old_task.id,
                    user_id=user.id,
                    source=ResponseSource.SYSTEM,
                    interpreted_kind="mark_completed",
                    interpreted_payload={"source": "test"},
                ),
                TaskChangeLog(
                    task_id=old_task.id,
                    change_type=ChangeType.RESCHEDULED,
                    reason="test",
                ),
                DailyConversation(user_id=user.id, conversation_date=date(2026, 3, 29)),
                DailyConversation(user_id=user.id, conversation_date=date(2026, 3, 30)),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        repo = AssistantRepository(session)
        result = await repo.prune_historical_data(
            task_cutoff=datetime(2026, 3, 30, 0, 0, tzinfo=timezone),
            conversation_cutoff=date(2026, 3, 30),
            plan_cutoff=date(2026, 3, 30),
        )
        await session.commit()

        assert result == {
            "deleted_task_responses": 1,
            "deleted_change_logs": 1,
            "deleted_tasks": 1,
            "deleted_daily_conversations": 1,
            "deleted_weekly_plans": 1,
        }

        remaining_tasks = await session.scalar(select(func.count()).select_from(StudyTask))
        remaining_plans = await session.scalar(select(func.count()).select_from(WeeklyPlan))
        remaining_conversations = await session.scalar(select(func.count()).select_from(DailyConversation))
        remaining_responses = await session.scalar(select(func.count()).select_from(TaskResponse))
        remaining_logs = await session.scalar(select(func.count()).select_from(TaskChangeLog))

        assert remaining_tasks == 1
        assert remaining_plans == 1
        assert remaining_conversations == 1
        assert remaining_responses == 0
        assert remaining_logs == 0

    await engine.dispose()
    if db_path.exists():
        db_path.unlink()
