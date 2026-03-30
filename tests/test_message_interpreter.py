from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from study_assistant.services.message_interpreter import MessageInterpreterService


class DisabledOpenAI:
    enabled = False


def test_interpreter_detects_replan_today():
    service = MessageInterpreterService(openai_client=DisabledOpenAI())
    result = service._rule_based_interpretation(
        text="\uc624\ub298\uc740 \uadf8\ub0e5 \ub9dd\uace0 \uc788\uc5b4",
        active_task=None,
        today_tasks=[],
        now=datetime.now(ZoneInfo("Asia/Seoul")),
    )

    assert result.kind == "replan_today"
    assert result.target_scope == "today"


def test_interpreter_detects_missed_multiple():
    service = MessageInterpreterService(openai_client=DisabledOpenAI())
    result = service._rule_based_interpretation(
        text="\ub458 \ub2e4 \ubabb\ud588\ub124",
        active_task=object(),
        today_tasks=[object(), object()],
        now=datetime.now(ZoneInfo("Asia/Seoul")),
    )

    assert result.kind == "mark_missed"
    assert result.target_scope == "multiple"


def test_interpreter_extracts_multiple_task_titles_from_free_text():
    service = MessageInterpreterService(openai_client=DisabledOpenAI())
    tasks = [
        SimpleNamespace(title="\uc218\ud559", topic="\uc801\ubd84"),
        SimpleNamespace(title="\uc601\uc5b4", topic="\ub3c5\ud574"),
        SimpleNamespace(title="\uacfc\ud559", topic="\ubcf5\uc2b5"),
    ]

    result = service._rule_based_interpretation(
        text="\uc624\ub298 \uc218\ud559\uc774\ub791 \uc601\uc5b4 \ub458 \ub2e4 \ubabb\ud588\ub124",
        active_task=None,
        today_tasks=tasks,
        now=datetime.now(ZoneInfo("Asia/Seoul")),
    )

    assert result.kind == "mark_missed"
    assert result.target_scope == "multiple"
    assert result.mentioned_task_titles == ["\uc218\ud559", "\uc601\uc5b4"]


def test_interpreter_detects_specific_time_reschedule_request():
    service = MessageInterpreterService(openai_client=DisabledOpenAI())

    result = service._rule_based_interpretation(
        text="\ub0b4\uc77c \uc800\ub141 6\uc2dc\ub85c \uc62e\uaca8\uc918",
        active_task=object(),
        today_tasks=[],
        now=datetime.now(ZoneInfo("Asia/Seoul")),
    )

    assert result.kind == "reschedule_specific_time"
    assert result.target_scope == "active_task"
