from __future__ import annotations

from datetime import datetime

from study_assistant.models.entities import TaskStatus
from study_assistant.schemas.contracts import ActionProposal, BrainResult, InterpretedMessage


FINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.PARTIAL,
    TaskStatus.MISSED,
    TaskStatus.CANCELLED,
}


class AssistantBrain:
    def __init__(self, message_interpreter):
        self.message_interpreter = message_interpreter

    async def interpret_message(
        self,
        *,
        text: str,
        user,
        daily_conversation,
        active_task,
        today_tasks,
        now: datetime,
    ) -> BrainResult:
        interpreted = await self.message_interpreter.interpret(
            text=text,
            user=user,
            daily_conversation=daily_conversation,
            active_task=active_task,
            today_tasks=today_tasks,
            now=now,
        )
        return self._build_result(
            interpreted=interpreted,
            text=text,
            active_task=active_task,
            today_tasks=today_tasks,
            now=now,
        )

    def _build_result(
        self,
        *,
        interpreted: InterpretedMessage,
        text: str,
        active_task,
        today_tasks,
        now: datetime,
    ) -> BrainResult:
        actions = self._expand_actions(
            interpreted=interpreted,
            text=text,
            active_task=active_task,
            today_tasks=today_tasks,
            now=now,
        )
        return BrainResult(
            actions=actions,
            summary=interpreted.summary,
        )

    def _expand_actions(
        self,
        *,
        interpreted: InterpretedMessage,
        text: str,
        active_task,
        today_tasks,
        now: datetime,
    ) -> list[ActionProposal]:
        if interpreted.kind == "unknown":
            return []

        if interpreted.kind == "mark_missed" and interpreted.target_scope == "multiple":
            matched_tasks = self._select_multiple_tasks(text=text, today_tasks=today_tasks, now=now)
            if matched_tasks:
                return [
                    ActionProposal(
                        kind=interpreted.kind,
                        target_scope="multiple",
                        target_task_id=task.id,
                        summary=f"Mark '{task.title}' as missed.",
                        confidence=interpreted.confidence,
                        feedback_type=interpreted.feedback_type,
                    )
                    for task in matched_tasks
                ]

        return [
            ActionProposal(
                kind=interpreted.kind,
                target_scope=interpreted.target_scope,
                target_task_id=getattr(active_task, "id", None),
                summary=interpreted.summary,
                confidence=interpreted.confidence,
                reschedule_minutes=interpreted.reschedule_minutes,
                feedback_type=interpreted.feedback_type,
            )
        ]

    def _select_multiple_tasks(self, *, text: str, today_tasks, now: datetime) -> list[object]:
        normalized_text = self._normalize(text)
        candidates = [
            task
            for task in today_tasks
            if task.status not in FINAL_TASK_STATUSES and task.end_at <= now
        ]
        if not candidates:
            return []

        matched = []
        for task in candidates:
            title = self._normalize(task.title)
            topic = self._normalize(task.topic or "")
            if title and title in normalized_text:
                matched.append(task)
                continue
            if topic and topic in normalized_text:
                matched.append(task)

        if matched:
            return matched

        if any(keyword in normalized_text for keyword in ["둘다", "둘", "전부", "모두", "다"]):
            return candidates

        return []

    def _normalize(self, value: str) -> str:
        return "".join(ch for ch in value.lower().strip() if not ch.isspace())
