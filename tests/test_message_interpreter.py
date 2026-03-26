from datetime import datetime
from zoneinfo import ZoneInfo

from study_assistant.services.message_interpreter import MessageInterpreterService


class DisabledOpenAI:
    enabled = False


def test_interpreter_detects_replan_today():
    service = MessageInterpreterService(openai_client=DisabledOpenAI())
    result = service._rule_based_interpretation(
        text="오늘은 그냥 쉬고 싶어",
        active_task=None,
        today_tasks=[],
        now=datetime.now(ZoneInfo("Asia/Seoul")),
    )

    assert result.kind == "replan_today"
    assert result.target_scope == "today"


def test_interpreter_detects_missed_multiple():
    service = MessageInterpreterService(openai_client=DisabledOpenAI())
    result = service._rule_based_interpretation(
        text="둘 다 못 했네",
        active_task=object(),
        today_tasks=[object(), object()],
        now=datetime.now(ZoneInfo("Asia/Seoul")),
    )

    assert result.kind == "mark_missed"
    assert result.target_scope == "multiple"
