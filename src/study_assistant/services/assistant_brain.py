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
        conversation_summary: str | None,
        recent_dialogue: list[dict[str, str]],
        last_user_turn: dict[str, str] | None,
        last_assistant_turn: dict[str, str] | None,
        active_prompt_kind: str | None,
        now: datetime,
    ) -> BrainResult:
        interpreted, source = await self.message_interpreter.interpret(
            text=text,
            user=user,
            daily_conversation=daily_conversation,
            active_task=active_task,
            today_tasks=today_tasks,
            conversation_summary=conversation_summary,
            recent_dialogue=recent_dialogue,
            last_user_turn=last_user_turn,
            last_assistant_turn=last_assistant_turn,
            active_prompt_kind=active_prompt_kind,
            now=now,
        )
        return self._build_result(
            interpreted=interpreted,
            source=source,
            text=text,
            active_task=active_task,
            today_tasks=today_tasks,
            now=now,
        )

    def _build_result(
        self,
        *,
        interpreted: InterpretedMessage,
        source: str,
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
        needs_clarification = False
        clarification_message = None

        if not actions:
            needs_clarification = True
            clarification_message = interpreted.clarification_message or self._clarification_message(
                interpreted=interpreted,
                active_task=active_task,
            )

        return BrainResult(
            actions=actions,
            summary=interpreted.summary,
            source=source,
            response_mode="clarify" if needs_clarification else "action",
            needs_clarification=needs_clarification,
            clarification_message=clarification_message,
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

        if interpreted.target_scope == "multiple":
            matched_tasks = self._select_multiple_tasks(
                interpreted=interpreted,
                text=text,
                today_tasks=today_tasks,
                now=now,
            )
            if matched_tasks:
                return [
                    ActionProposal(
                        kind=interpreted.kind,
                        target_scope="multiple",
                        target_task_id=task.id,
                        target_task_title=task.title,
                        summary=f"Mark '{task.title}' as missed.",
                        confidence=interpreted.confidence,
                        feedback_type=interpreted.feedback_type,
                    )
                    for task in matched_tasks
                ]

        resolved_task = self._resolve_single_task(
            interpreted=interpreted,
            active_task=active_task,
            today_tasks=today_tasks,
        )
        if interpreted.target_scope == "active_task" and resolved_task is None:
            return []

        return [
            ActionProposal(
                kind=interpreted.kind,
                target_scope=interpreted.target_scope,
                target_task_id=getattr(resolved_task, "id", None),
                target_task_title=getattr(resolved_task, "title", None),
                summary=interpreted.summary,
                confidence=interpreted.confidence,
                reschedule_minutes=interpreted.reschedule_minutes,
                feedback_type=interpreted.feedback_type,
            )
        ]

    def _select_multiple_tasks(
        self,
        *,
        interpreted: InterpretedMessage,
        text: str,
        today_tasks,
        now: datetime,
    ) -> list[object]:
        normalized_text = self._normalize(text)
        candidates = [
            task
            for task in today_tasks
            if task.status not in FINAL_TASK_STATUSES and task.end_at <= now
        ]
        if not candidates:
            return []

        explicit_matches = self._match_tasks_by_hints(
            tasks=candidates,
            target_task_ids=interpreted.target_task_ids,
            mentioned_titles=interpreted.mentioned_task_titles,
        )
        if len(explicit_matches) >= 2:
            return explicit_matches

        text_matches = self._match_tasks_by_text(tasks=candidates, normalized_text=normalized_text)
        if len(text_matches) >= 2:
            return text_matches

        if explicit_matches:
            return explicit_matches

        if text_matches:
            return text_matches

        if self._has_multiple_marker(normalized_text):
            return candidates

        return []

    def _resolve_single_task(self, *, interpreted: InterpretedMessage, active_task, today_tasks) -> object | None:
        explicit_matches = self._match_tasks_by_hints(
            tasks=today_tasks,
            target_task_ids=interpreted.target_task_ids,
            mentioned_titles=interpreted.mentioned_task_titles,
        )
        if len(explicit_matches) == 1:
            return explicit_matches[0]

        if active_task is not None:
            return active_task

        return None

    def _match_tasks_by_hints(self, *, tasks, target_task_ids: list[str], mentioned_titles: list[str]) -> list[object]:
        if target_task_ids:
            matched_by_id = [task for task in tasks if getattr(task, "id", None) in target_task_ids]
            if matched_by_id:
                return matched_by_id

        normalized_titles = {self._normalize(title) for title in mentioned_titles if title}
        if not normalized_titles:
            return []

        matched_by_title = []
        for task in tasks:
            title = self._normalize(getattr(task, "title", "") or "")
            topic = self._normalize(getattr(task, "topic", "") or "")
            if title in normalized_titles or topic in normalized_titles:
                matched_by_title.append(task)
        return matched_by_title

    def _match_tasks_by_text(self, *, tasks, normalized_text: str) -> list[object]:
        matched = []
        for task in tasks:
            title = self._normalize(getattr(task, "title", "") or "")
            topic = self._normalize(getattr(task, "topic", "") or "")
            if title and title in normalized_text:
                matched.append(task)
                continue
            if topic and topic in normalized_text:
                matched.append(task)
        return matched

    def _has_multiple_marker(self, normalized_text: str) -> bool:
        return any(
            keyword in normalized_text
            for keyword in [
                "둘다",
                "둘다못",
                "둘다못했",
                "둘다못했네",
                "모두",
                "전부",
                "전체",
                "다못",
                "다못했",
                "다못했네",
            ]
        )

    def _normalize(self, value: str) -> str:
        return "".join(ch for ch in value.lower().strip() if not ch.isspace())

    def _clarification_message(self, *, interpreted: InterpretedMessage, active_task) -> str:
        if interpreted.kind == "status_update" and active_task is not None:
            return (
                f"'{active_task.title}' 일정 상태를 조금만 더 구체적으로 말해줄래요?\n"
                "예: 완료했어요, 못 했어요, 30분 뒤로 미뤄줘"
            )

        return (
            "말하려는 작업을 문장으로 조금만 더 구체적으로 말해줄래요?\n"
            "예: 오늘 6시로 옮겨줘, 오늘은 못 했어요, 추천 시간 보여줘"
        )
