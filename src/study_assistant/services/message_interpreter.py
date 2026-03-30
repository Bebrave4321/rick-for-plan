from __future__ import annotations

from datetime import datetime

from study_assistant.schemas.contracts import InterpretedMessage


class MessageInterpreterService:
    def __init__(self, openai_client):
        self.openai_client = openai_client

    async def interpret(
        self,
        text: str,
        user,
        daily_conversation,
        active_task,
        today_tasks,
        conversation_summary: str | None,
        recent_dialogue: list[dict[str, str]],
        now: datetime,
    ) -> tuple[InterpretedMessage, str]:
        if self.openai_client.enabled:
            interpreted = await self.openai_client.interpret_message(
                text=text,
                user=user,
                daily_conversation=daily_conversation,
                active_task=active_task,
                today_tasks=today_tasks,
                conversation_summary=conversation_summary,
                recent_dialogue=recent_dialogue,
                now=now,
            )
            if interpreted is not None and interpreted.confidence >= 0.45:
                return interpreted, "openai"

        return self._rule_based_interpretation(text, active_task, today_tasks, now), "rule"

    def _rule_based_interpretation(self, text: str, active_task, today_tasks, now: datetime) -> InterpretedMessage:
        normalized = self._normalize(text)

        if normalized in {"/plan", "주간계획", "이번주계획"}:
            return InterpretedMessage(
                kind="weekly_plan_request",
                target_scope="none",
                summary="Weekly planning requested.",
                confidence=1.0,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if "이번주" in normalized and any(keyword in normalized for keyword in ["목표", "마감", "시험", "계획"]):
            return InterpretedMessage(
                kind="weekly_plan_input",
                target_scope="none",
                summary="Likely weekly planning details.",
                confidence=0.7,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if "오늘" in normalized and any(
            keyword in normalized for keyword in ["정리", "재배치", "다시", "망했", "쉬고싶어", "안하겠어", "그냥쉬"]
        ):
            return InterpretedMessage(
                kind="replan_today",
                target_scope="today",
                summary="User wants to reorganize today's remaining tasks.",
                confidence=0.95,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["오늘저녁", "오늘밤"]):
            return InterpretedMessage(
                kind="reschedule_tonight",
                target_scope="active_task" if active_task else "none",
                summary="Reschedule to tonight.",
                confidence=0.97,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["내일저녁", "내일밤"]):
            return InterpretedMessage(
                kind="reschedule_tomorrow",
                target_scope="active_task" if active_task else "none",
                summary="Reschedule to tomorrow evening.",
                confidence=0.97,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["완료", "끝냈", "끝남", "다했", "다함"]):
            return InterpretedMessage(
                kind="mark_completed",
                target_scope="active_task" if active_task else "none",
                summary="Task completed.",
                confidence=0.95,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["못했", "못함", "못했네", "못하겠", "못할것같"]):
            scope = "multiple" if self._mentions_multiple(normalized, today_tasks) else "active_task"
            return InterpretedMessage(
                kind="mark_missed",
                target_scope=scope if today_tasks else "none",
                summary="Task missed.",
                confidence=0.94,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["일부", "조금", "반만", "조금했", "덜했"]):
            return InterpretedMessage(
                kind="mark_partial",
                target_scope="active_task" if active_task else "none",
                summary="Task partially completed.",
                confidence=0.92,
                feedback_type="did_not_finish",
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["취소", "그만", "안할래"]):
            return InterpretedMessage(
                kind="cancel_task",
                target_scope="active_task" if active_task else "none",
                summary="Cancel current task.",
                confidence=0.9,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if "10분" in normalized and any(keyword in normalized for keyword in ["미뤄", "늦춰", "밀어"]):
            return InterpretedMessage(
                kind="postpone_10",
                target_scope="active_task" if active_task else "none",
                summary="Delay by 10 minutes.",
                confidence=0.9,
                reschedule_minutes=10,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if any(keyword in normalized for keyword in ["미뤄", "늦춰", "밀어"]):
            return InterpretedMessage(
                kind="postpone_custom",
                target_scope="active_task" if active_task else "none",
                summary="Reschedule current task.",
                confidence=0.65,
                reschedule_minutes=30,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        if active_task and active_task.end_at < now:
            return InterpretedMessage(
                kind="status_update",
                target_scope="active_task",
                summary="General update for the latest active task.",
                confidence=0.35,
                target_task_ids=[],
                mentioned_task_titles=[],
            )

        return InterpretedMessage(
            kind="unknown",
            target_scope="none",
            summary="No reliable interpretation.",
            confidence=0.1,
            target_task_ids=[],
            mentioned_task_titles=[],
        )

    def _mentions_multiple(self, normalized: str, today_tasks) -> bool:
        if any(keyword in normalized for keyword in ["둘다", "둘", "전부", "모두", "다못"]):
            return True

        matched_titles = 0
        for task in today_tasks:
            task_tokens = [
                self._normalize(getattr(task, "title", "")),
                self._normalize(getattr(task, "topic", "") or ""),
            ]
            if any(token and token in normalized for token in task_tokens):
                matched_titles += 1
        return matched_titles >= 2

    def _normalize(self, text: str) -> str:
        return "".join(ch for ch in text.strip().lower() if not ch.isspace())
