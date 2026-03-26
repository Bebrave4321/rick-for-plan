from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from study_assistant.core.config import Settings
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
        self.webhook_calls = 0

    async def close(self):
        return None

    async def set_webhook(self):
        self.webhook_calls += 1


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
