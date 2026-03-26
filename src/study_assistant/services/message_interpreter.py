from __future__ import annotations

from datetime import datetime

from study_assistant.schemas.contracts import InterpretedMessage


class MessageInterpreterService:
    def __init__(self, openai_client):
        self.openai_client = openai_client

    async def interpret(self, text: str, user, daily_conversation, active_task, today_tasks, now: datetime) -> InterpretedMessage:
        if self.openai_client.enabled:
            interpreted = await self.openai_client.interpret_message(
                text=text,
                user=user,
                daily_conversation=daily_conversation,
                active_task=active_task,
                today_tasks=today_tasks,
            )
            if interpreted is not None and interpreted.confidence >= 0.45:
                return interpreted

        return self._rule_based_interpretation(text, active_task, today_tasks, now)

    def _rule_based_interpretation(self, text: str, active_task, today_tasks, now: datetime) -> InterpretedMessage:
        normalized = text.strip().lower()

        if normalized == "/plan" or text in {"이번 주 계획 짜줘", "이번 주 계획"}:
            return InterpretedMessage(
                kind="weekly_plan_request",
                target_scope="none",
                summary="Weekly planning requested.",
                confidence=1.0,
            )

        if "오늘" in text and ("쉬고" in text or "쉴래" in text or "쉬고 싶" in text):
            return InterpretedMessage(
                kind="replan_today",
                target_scope="today",
                summary="User wants to reorganize or stop today's remaining tasks.",
                confidence=0.95,
            )

        if "이번 주" in text and ("비가용" in text or "목표" in text):
            return InterpretedMessage(
                kind="weekly_plan_input",
                target_scope="none",
                summary="Likely weekly planning details.",
                confidence=0.7,
            )

        if any(keyword in text for keyword in ["다 했", "끝냈", "완료", "끝남"]):
            return InterpretedMessage(
                kind="mark_completed",
                target_scope="active_task" if active_task else "none",
                summary="Task completed.",
                confidence=0.95,
            )

        if any(keyword in text for keyword in ["못 했", "못했", "못 함", "못함"]):
            scope = "multiple" if any(keyword in text for keyword in ["둘 다", "전부", "전체"]) else "active_task"
            return InterpretedMessage(
                kind="mark_missed",
                target_scope=scope if today_tasks else "none",
                summary="Task missed.",
                confidence=0.94,
            )

        if any(keyword in text for keyword in ["일부", "조금 했", "반만", "다 못 끝", "다 못끝"]):
            return InterpretedMessage(
                kind="mark_partial",
                target_scope="active_task" if active_task else "none",
                summary="Task partially completed.",
                confidence=0.92,
                feedback_type="did_not_finish",
            )

        if any(keyword in text for keyword in ["취소", "안 할래", "안할래"]):
            return InterpretedMessage(
                kind="cancel_task",
                target_scope="active_task" if active_task else "none",
                summary="Cancel current task.",
                confidence=0.9,
            )

        if "10분" in text and any(keyword in text for keyword in ["미뤄", "미뤄줘", "옮겨", "늦춰"]):
            return InterpretedMessage(
                kind="postpone_10",
                target_scope="active_task" if active_task else "none",
                summary="Delay by 10 minutes.",
                confidence=0.9,
                reschedule_minutes=10,
            )

        if any(keyword in text for keyword in ["미뤄", "옮겨", "늦춰"]):
            return InterpretedMessage(
                kind="postpone_custom",
                target_scope="active_task" if active_task else "none",
                summary="Reschedule current task.",
                confidence=0.65,
                reschedule_minutes=30,
            )

        if active_task and active_task.end_at < now:
            return InterpretedMessage(
                kind="status_update",
                target_scope="active_task",
                summary="General update for the latest active task.",
                confidence=0.35,
            )

        return InterpretedMessage(kind="unknown", target_scope="none", summary="No reliable interpretation.", confidence=0.1)
