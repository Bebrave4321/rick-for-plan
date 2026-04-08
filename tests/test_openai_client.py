import json
from datetime import date, datetime, time
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
        self.summary_context = None


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
async def test_interpret_message_includes_recent_and_derived_dialogue_context():
    payload = {
        "kind": "postpone_10",
        "target_scope": "active_task",
        "summary": "Delay by ten minutes",
        "confidence": 0.9,
        "clarification_message": None,
        "reschedule_minutes": 10,
        "feedback_type": None,
        "target_task_ids": [],
        "mentioned_task_titles": [],
    }
    client = OpenAIAssistantClient(Settings(openai_api_key="test-key"))
    fake_client = FakeOpenAIClient(payload)
    client.client = fake_client
    conversation = DummyConversation()

    interpreted = await client.interpret_message(
        text="Delay it by ten minutes.",
        user=DummyUser(),
        daily_conversation=conversation,
        active_task=None,
        today_tasks=[],
        conversation_summary="User prefers practical replies.",
        recent_dialogue=[
            {"role": "user", "text": "I am running late.", "occurred_at": "2026-03-27T17:40:00+09:00"},
            {
                "role": "assistant",
                "text": "No problem. Tell me how you want to adjust it.",
                "occurred_at": "2026-03-27T17:40:02+09:00",
            },
        ],
        dialogue_transcript=(
            "user: I am running late.\n"
            "assistant: No problem. Tell me how you want to adjust it."
        ),
        last_user_turn={"role": "user", "text": "I am running late.", "occurred_at": "2026-03-27T17:40:00+09:00"},
        last_assistant_turn={
            "role": "assistant",
            "text": "No problem. Tell me how you want to adjust it.",
            "occurred_at": "2026-03-27T17:40:02+09:00",
        },
        active_prompt_kind="reschedule",
        now=datetime(2026, 3, 27, 18, 0),
    )

    assert interpreted is not None
    assert interpreted.kind == "postpone_10"
    call = fake_client.responses.calls[0]
    assert "conversation" not in call
    assert "tools" not in call
    assert call["text"]["format"]["type"] == "json_schema"
    prompt = json.loads(call["input"][1]["content"])
    assert prompt["conversation_summary"] == "User prefers practical replies."
    assert len(prompt["recent_dialogue"]) == 2
    assert "user: I am running late." in prompt["dialogue_transcript"]
    assert prompt["current_date"] == "2026-03-27"
    assert prompt["current_time"] == "2026-03-27T18:00:00"
    assert prompt["last_user_turn"]["text"] == "I am running late."
    assert prompt["last_assistant_turn"]["text"] == "No problem. Tell me how you want to adjust it."
    assert prompt["active_prompt_kind"] == "reschedule"
    developer_prompt = call["input"][0]["content"]
    assert "conversation_summary, recent_dialogue, dialogue_transcript, last_user_turn," in developer_prompt
    assert "active_prompt_kind" in developer_prompt
    assert "target_scope='multiple'" in developer_prompt
    assert "today 6 PM" not in developer_prompt  # sanity check that this test only inspects context additions
    assert conversation.last_response_id == "resp_test"
