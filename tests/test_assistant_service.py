from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from study_assistant.core.config import Settings
from study_assistant.db.session import Base
from study_assistant.models.entities import PendingPromptType, StudyTask, TaskSource, TaskStatus
from study_assistant.services.message_interpreter import MessageInterpreterService
from study_assistant.services.assistant import StudyAssistantService


class DummyTask:
    def __init__(self, start_at, end_at, last_progress_check_at=None):
        self.start_at = start_at
        self.end_at = end_at
        self.latest_prompt_sent_at = None
        self.prep_reminder_sent_at = None
        self.checkin_sent_at = None
        self.recheck_sent_at = None
        self.last_progress_check_at = last_progress_check_at
        self.completion_prompt_sent_at = None
        self.completed_at = None


class DummyClient:
    def __init__(self):
        self.enabled = False
        self.webhook_calls = 0
        self.messages = []

    async def close(self):
        return None

    async def set_webhook(self):
        self.webhook_calls += 1

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

def build_service():
    settings = Settings(
        database_url="sqlite+aiosqlite:///./test_assistant.db",
        default_timezone="Asia/Seoul",
    )
    return StudyAssistantService(
        settings=settings,
        session_factory=None,
        planning_service=None,
        message_interpreter=None,
        telegram_client=DummyClient(),
        openai_client=DummyClient(),
    )


async def build_db_service(db_name: str):
    db_path = Path("tests") / db_name
    if db_path.exists():
        db_path.unlink()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    telegram_client = DummyClient()
    service = StudyAssistantService(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            default_timezone="Asia/Seoul",
        ),
        session_factory=session_factory,
        planning_service=None,
        message_interpreter=MessageInterpreterService(openai_client=DummyClient()),
        telegram_client=telegram_client,
        openai_client=DummyClient(),
    )
    return service, telegram_client, session_factory, engine, db_path


def test_needs_progress_check_handles_naive_datetimes():
    service = build_service()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    task = DummyTask(
        start_at=(now - timedelta(hours=2)).replace(tzinfo=None),
        end_at=(now + timedelta(hours=1)).replace(tzinfo=None),
    )

    assert service._needs_progress_check(task, now) is True
    assert task.start_at.tzinfo is not None
    assert task.end_at.tzinfo is not None


def test_retention_week_start_defaults_to_current_week():
    service = build_service()

    assert service._retention_week_start(date(2026, 4, 1)) == date(2026, 3, 30)


@pytest.mark.asyncio
async def test_ensure_integrations_ready_registers_webhook_for_public_base_url():
    settings = Settings(
        database_url="sqlite+aiosqlite:///./test_assistant.db",
        default_timezone="Asia/Seoul",
        telegram_bot_token="token",
        base_url="https://study-assistant-production.up.railway.app",
    )
    telegram_client = DummyClient()
    service = StudyAssistantService(
        settings=settings,
        session_factory=None,
        planning_service=None,
        message_interpreter=None,
        telegram_client=telegram_client,
        openai_client=DummyClient(),
    )

    await service.ensure_integrations_ready()

    assert telegram_client.webhook_calls == 1


@pytest.mark.asyncio
async def test_testcomplete_command_creates_manual_completion_prompt():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-fast-complete.db")

    try:
        await service.process_text_message(
            telegram_user_id=1001,
            chat_id=1001,
            display_name="LG",
            text="/testcomplete",
        )

        async with session_factory() as session:
            task = (await session.execute(select(StudyTask))).scalar_one()
            assert task.source == TaskSource.MANUAL
            assert task.status == TaskStatus.IN_PROGRESS
            assert task.pending_prompt_type == PendingPromptType.COMPLETION
            assert task.completion_prompt_sent_at is not None

        assert telegram_client.messages[-1]["text"].startswith("빠른 테스트예요.")
        keyboard = telegram_client.messages[-1]["reply_markup"]["inline_keyboard"]
        assert keyboard[0][0]["callback_data"].endswith(":done")
        assert keyboard[1][0]["callback_data"].endswith(":missed")
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_delay10_suppresses_duplicate_prep_reminder_for_immediate_reschedule():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-delay10.db")

    try:
        await service.process_text_message(
            telegram_user_id=1002,
            chat_id=1002,
            display_name="LG",
            text="/testcheckin",
        )

        async with session_factory() as session:
            task = (await session.execute(select(StudyTask))).scalar_one()
            original_start = task.start_at
            task_id = task.id

        await service.process_callback_query(
            telegram_user_id=1002,
            chat_id=1002,
            callback_data=f"task:{task_id}:delay10",
        )

        async with session_factory() as session:
            task = (await session.execute(select(StudyTask))).scalar_one()
            assert task.start_at == original_start + timedelta(minutes=10)
            assert task.prep_reminder_sent_at is not None
            assert task.pending_prompt_type is None

        assert "10분 뒤로 옮겼어요" in telegram_client.messages[-1]["text"]
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_reschedule_prompt_accepts_free_text_and_confirms_exact_time():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-reschedule-text.db")

    try:
        await service.process_text_message(
            telegram_user_id=1003,
            chat_id=1003,
            display_name="LG",
            text="/testcomplete",
        )
        await service.process_text_message(
            telegram_user_id=1003,
            chat_id=1003,
            display_name="LG",
            text="못 했어요",
        )

        async with session_factory() as session:
            task = (await session.execute(select(StudyTask))).scalar_one()
            assert task.status == TaskStatus.MISSED
            assert task.pending_prompt_type == PendingPromptType.RESCHEDULE

        assert "버튼 대신 '오늘 저녁으로'" in telegram_client.messages[-1]["text"]

        await service.process_text_message(
            telegram_user_id=1003,
            chat_id=1003,
            display_name="LG",
            text="오늘 저녁으로",
        )

        async with session_factory() as session:
            task = (await session.execute(select(StudyTask))).scalar_one()
            assert task.status == TaskStatus.RESCHEDULED
            assert task.pending_prompt_type is None

        assert "새 시간:" in telegram_client.messages[-1]["text"]
        assert "19:00" in telegram_client.messages[-1]["text"]
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()
