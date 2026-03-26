from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from study_assistant.core.config import Settings
from study_assistant.db.session import Base
from study_assistant.models.entities import (
    ChangeType,
    PendingPromptType,
    StudyTask,
    TaskChangeLog,
    TaskSource,
    TaskStatus,
    User,
)
from study_assistant.schemas.contracts import CreateUserRequest
from study_assistant.services.assistant import StudyAssistantService
from study_assistant.services.message_interpreter import MessageInterpreterService


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


async def create_user(service: StudyAssistantService, telegram_user_id: int) -> None:
    await service.bootstrap_user(
        CreateUserRequest(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_user_id,
            display_name="LG",
        )
    )


async def load_single_task(session_factory) -> StudyTask:
    async with session_factory() as session:
        return (await session.execute(select(StudyTask))).scalar_one()


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

        task = await load_single_task(session_factory)
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

        task = await load_single_task(session_factory)
        original_start = task.start_at
        task_id = task.id

        await service.process_callback_query(
            telegram_user_id=1002,
            chat_id=1002,
            callback_data=f"task:{task_id}:delay10",
        )

        task = await load_single_task(session_factory)
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

        task = await load_single_task(session_factory)
        assert task.status == TaskStatus.MISSED
        assert task.pending_prompt_type == PendingPromptType.RESCHEDULE

        assert "언제로 다시 잡을까요?" in telegram_client.messages[-1]["text"]

        await service.process_text_message(
            telegram_user_id=1003,
            chat_id=1003,
            display_name="LG",
            text="오늘 저녁 6시로 일정 옮겨줄래?",
        )

        task = await load_single_task(session_factory)
        assert task.status == TaskStatus.RESCHEDULED
        assert task.pending_prompt_type is None
        assert task.start_at.hour == 18
        assert task.start_at.minute == 0

        assert "새 시간:" in telegram_client.messages[-1]["text"]
        assert "18:00" in telegram_client.messages[-1]["text"]
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_reschedule_prompt_clarifies_ambiguous_free_text_once():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-reschedule-clarify.db")

    try:
        await service.process_text_message(
            telegram_user_id=1004,
            chat_id=1004,
            display_name="LG",
            text="/testcomplete",
        )
        await service.process_text_message(
            telegram_user_id=1004,
            chat_id=1004,
            display_name="LG",
            text="못 했어요",
        )
        await service.process_text_message(
            telegram_user_id=1004,
            chat_id=1004,
            display_name="LG",
            text="좀 늦춰줘",
        )

        task = await load_single_task(session_factory)
        assert task.status == TaskStatus.MISSED
        assert task.pending_prompt_type == PendingPromptType.RESCHEDULE

        assert telegram_client.messages[-1]["text"].startswith("언제로 다시 잡을까요?")
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_multiple_missed_message_replans_only_matched_tasks():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-multi-missed.db")

    try:
        await create_user(service, 1005)
        now = service.now()

        async with session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_user_id == 1005))).scalar_one()
            math_task = StudyTask(
                user_id=user.id,
                title="수학",
                topic="적분",
                start_at=now - timedelta(hours=3),
                end_at=now - timedelta(hours=2, minutes=20),
                source=TaskSource.MANUAL,
                status=TaskStatus.IN_PROGRESS,
            )
            english_task = StudyTask(
                user_id=user.id,
                title="영어",
                topic="독해",
                start_at=now - timedelta(hours=2),
                end_at=now - timedelta(hours=1, minutes=20),
                source=TaskSource.MANUAL,
                status=TaskStatus.IN_PROGRESS,
            )
            other_task = StudyTask(
                user_id=user.id,
                title="과학",
                topic="복습",
                start_at=now - timedelta(hours=1),
                end_at=now - timedelta(minutes=20),
                source=TaskSource.MANUAL,
                status=TaskStatus.IN_PROGRESS,
            )
            session.add_all([math_task, english_task, other_task])
            await session.commit()

        await service.process_text_message(
            telegram_user_id=1005,
            chat_id=1005,
            display_name="LG",
            text="오늘 수학이랑 영어 둘 다 못했네",
        )

        async with session_factory() as session:
            tasks = (
                await session.execute(select(StudyTask).order_by(StudyTask.title.asc()))
            ).scalars().all()
            by_title = {task.title: task for task in tasks}
            assert by_title["수학"].status == TaskStatus.RESCHEDULED
            assert by_title["영어"].status == TaskStatus.RESCHEDULED
            assert by_title["과학"].status == TaskStatus.IN_PROGRESS

        assert "수학, 영어" in telegram_client.messages[-1]["text"]
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_weekly_report_summarizes_completion_reschedules_and_best_window():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-weekly-report.db")

    try:
        await create_user(service, 1006)
        now = service.now()
        week_start = now.date() - timedelta(days=now.date().weekday())

        async with session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_user_id == 1006))).scalar_one()
            completed_evening = StudyTask(
                user_id=user.id,
                title="영어 독해",
                start_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=1, hours=19),
                end_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=1, hours=20),
                source=TaskSource.MANUAL,
                status=TaskStatus.COMPLETED,
                completed_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=1, hours=20),
            )
            completed_evening_two = StudyTask(
                user_id=user.id,
                title="수학 문제풀이",
                start_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=2, hours=18),
                end_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=2, hours=19),
                source=TaskSource.MANUAL,
                status=TaskStatus.COMPLETED,
                completed_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=2, hours=19),
            )
            missed_task = StudyTask(
                user_id=user.id,
                title="과학 복습",
                start_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=3, hours=10),
                end_at=datetime.combine(week_start, datetime.min.time(), tzinfo=service.settings.timezone)
                + timedelta(days=3, hours=11),
                source=TaskSource.MANUAL,
                status=TaskStatus.MISSED,
            )
            session.add_all([completed_evening, completed_evening_two, missed_task])
            await session.flush()
            session.add(
                TaskChangeLog(
                    task_id=missed_task.id,
                    old_start_at=missed_task.start_at,
                    old_end_at=missed_task.end_at,
                    new_start_at=missed_task.start_at + timedelta(hours=2),
                    new_end_at=missed_task.end_at + timedelta(hours=2),
                    change_type=ChangeType.RESCHEDULED,
                    reason="Moved after failure.",
                )
            )
            await session.commit()

        report = await service.get_weekly_report(1006)

        assert report.total_tasks == 3
        assert report.completed_tasks == 2
        assert report.completion_rate == pytest.approx(2 / 3)
        assert report.rescheduled_count == 1
        assert report.best_time_window == "저녁"

        await service.process_text_message(
            telegram_user_id=1006,
            chat_id=1006,
            display_name="LG",
            text="/weeklyreport",
        )

        assert "이번 주 간단 리포트예요." in telegram_client.messages[-1]["text"]
        assert "완료율: 2/3" in telegram_client.messages[-1]["text"]
        assert "미룬 횟수: 1회" in telegram_client.messages[-1]["text"]
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_run_due_scan_dispatches_scheduler_event_for_checkin():
    service, telegram_client, session_factory, engine, db_path = await build_db_service(".assistant-run-due-scan.db")

    try:
        await create_user(service, 1007)
        now = service.now()

        async with session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_user_id == 1007))).scalar_one()
            task = StudyTask(
                user_id=user.id,
                title="즉시 체크인 테스트",
                topic="test",
                start_at=now - timedelta(minutes=2),
                end_at=now + timedelta(minutes=23),
                source=TaskSource.MANUAL,
                status=TaskStatus.PLANNED,
            )
            session.add(task)
            await session.commit()

        result = await service.run_due_scan()

        assert result["sent_count"] >= 1
        assert telegram_client.messages[-1]["text"].startswith("지금 '즉시 체크인 테스트'")

        task = await load_single_task(session_factory)
        assert task.status == TaskStatus.CHECKIN_PENDING
        assert task.pending_prompt_type == PendingPromptType.CHECKIN
        assert task.checkin_sent_at is not None
    finally:
        await engine.dispose()
        if db_path.exists():
            db_path.unlink()
