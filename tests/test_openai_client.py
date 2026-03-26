import json
from datetime import date, time
from types import SimpleNamespace

import pytest

from study_assistant.core.config import Settings
from study_assistant.schemas.contracts import WeeklyPlanningRequest
from study_assistant.services.openai_client import OpenAIAssistantClient


class FakeResponsesAPI:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="resp_test", output_text=json.dumps(self.payload, ensure_ascii=False))


class FakeOpenAIClient:
    def __init__(self, payload: dict):
        self.responses = FakeResponsesAPI(payload)

    async def close(self):
        return None


class DummyUser:
    timezone = "Asia/Seoul"
    default_study_window_start = time(7, 0)
    default_study_window_end = time(23, 0)


class DummyConversation:
    def __init__(self):
        self.openai_conversation_id = "conv_old"
        self.last_response_id = None


@pytest.mark.asyncio
async def test_generate_weekly_plan_uses_json_schema_response_format():
    payload = {
        "summary": "Test plan",
        "sessions": [
            {
                "title": "English reading",
                "topic": "vocabulary",
                "start_at": "2026-03-26T20:00:00+09:00",
                "end_at": "2026-03-26T21:00:00+09:00",
                "importance": 4,
                "notes": None,
            }
        ],
        "overflow_notes": [],
    }
    client = OpenAIAssistantClient(Settings(openai_api_key="test-key"))
    fake_client = FakeOpenAIClient(payload)
    client.client = fake_client
    conversation = DummyConversation()
    request = WeeklyPlanningRequest(week_start_date=date(2026, 3, 23))

    draft = await client.generate_weekly_plan(request, DummyUser(), conversation)

    assert draft is not None
    assert draft.summary == "Test plan"
    call = fake_client.responses.calls[0]
    assert "conversation" not in call
    assert "tools" not in call
    assert call["text"]["format"]["type"] == "json_schema"
    assert conversation.last_response_id == "resp_test"


@pytest.mark.asyncio
async def test_interpret_message_uses_json_schema_response_format():
    payload = {
        "kind": "postpone_10",
        "target_scope": "active_task",
        "summary": "Delay by ten minutes",
        "confidence": 0.9,
        "reschedule_minutes": 10,
        "feedback_type": None,
    }
    client = OpenAIAssistantClient(Settings(openai_api_key="test-key"))
    fake_client = FakeOpenAIClient(payload)
    client.client = fake_client
    conversation = DummyConversation()

    interpreted = await client.interpret_message(
        text="10분 미뤄줘",
        user=DummyUser(),
        daily_conversation=conversation,
        active_task=None,
        today_tasks=[],
    )

    assert interpreted is not None
    assert interpreted.kind == "postpone_10"
    call = fake_client.responses.calls[0]
    assert "conversation" not in call
    assert "tools" not in call
    assert call["text"]["format"]["type"] == "json_schema"
    assert conversation.last_response_id == "resp_test"
