from datetime import datetime
from types import SimpleNamespace

from study_assistant.models.entities import TaskStatus
from study_assistant.schemas.contracts import InterpretedMessage
from study_assistant.services.assistant_brain import AssistantBrain


class DummyInterpreter:
    async def interpret(self, **kwargs):
        raise NotImplementedError


def make_task(task_id: str, title: str, topic: str = "", *, end_at):
    return SimpleNamespace(
        id=task_id,
        title=title,
        topic=topic,
        end_at=end_at,
        status=TaskStatus.IN_PROGRESS,
    )


def test_expand_actions_prefers_explicit_target_task_ids_for_multiple_scope():
    brain = AssistantBrain(message_interpreter=DummyInterpreter())
    now = datetime(2026, 3, 30, 20, 0)
    today_tasks = [
        make_task("math-1", "수학", "적분", end_at=now),
        make_task("english-1", "영어", "독해", end_at=now),
        make_task("science-1", "과학", "복습", end_at=now),
    ]
    interpreted = InterpretedMessage(
        kind="mark_missed",
        target_scope="multiple",
        summary="Math and English were missed.",
        confidence=0.91,
        target_task_ids=["math-1", "english-1"],
        mentioned_task_titles=["수학", "영어"],
    )

    result = brain._build_result(
        interpreted=interpreted,
        source="openai",
        text="수학이랑 영어 둘 다 못 했어",
        active_task=None,
        today_tasks=today_tasks,
        now=now,
    )

    assert [action.target_task_id for action in result.actions] == ["math-1", "english-1"]
    assert [action.target_task_title for action in result.actions] == ["수학", "영어"]


def test_expand_actions_requests_clarification_when_active_task_target_is_unresolved():
    brain = AssistantBrain(message_interpreter=DummyInterpreter())
    now = datetime(2026, 3, 30, 20, 0)
    interpreted = InterpretedMessage(
        kind="mark_completed",
        target_scope="active_task",
        summary="Task completed.",
        confidence=0.88,
    )

    result = brain._build_result(
        interpreted=interpreted,
        source="openai",
        text="끝냈어",
        active_task=None,
        today_tasks=[],
        now=now,
    )

    assert result.actions == []
    assert result.needs_clarification is True
    assert result.response_mode == "clarify"
