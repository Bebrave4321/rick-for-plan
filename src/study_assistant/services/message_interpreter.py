from __future__ import annotations

import re
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
        dialogue_transcript: str | None,
        last_user_turn: dict[str, str] | None,
        last_assistant_turn: dict[str, str] | None,
        active_prompt_kind: str | None,
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
                dialogue_transcript=dialogue_transcript,
                last_user_turn=last_user_turn,
                last_assistant_turn=last_assistant_turn,
                active_prompt_kind=active_prompt_kind,
                now=now,
            )
            if interpreted is not None and interpreted.confidence >= 0.45:
                return interpreted, "openai"

        return self._rule_based_interpretation(text, active_task, today_tasks, now), "rule"

    def _rule_based_interpretation(self, text: str, active_task, today_tasks, now: datetime) -> InterpretedMessage:
        normalized = self._normalize(text)
        matched_titles = self._extract_mentioned_task_titles(normalized, today_tasks)

        if normalized in {"/plan", "주간계획", "이번주계획"}:
            return InterpretedMessage(
                kind="weekly_plan_request",
                target_scope="none",
                summary="Weekly planning requested.",
                confidence=1.0,
            )

        if "이번주" in normalized and any(keyword in normalized for keyword in ["목표", "마감", "시험", "계획"]):
            return InterpretedMessage(
                kind="weekly_plan_input",
                target_scope="none",
                summary="Likely weekly planning details.",
                confidence=0.7,
            )

        if self._looks_like_today_replan(normalized):
            return InterpretedMessage(
                kind="replan_today",
                target_scope="today",
                summary="User wants to reorganize today's remaining tasks.",
                confidence=0.95,
            )

        if self._looks_like_specific_reschedule(normalized):
            return InterpretedMessage(
                kind="reschedule_specific_time",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Reschedule current task to a specific requested time.",
                confidence=0.9,
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["오늘저녁", "오늘밤"]):
            return InterpretedMessage(
                kind="reschedule_tonight",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Reschedule to tonight.",
                confidence=0.97,
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["내일저녁", "내일밤"]):
            return InterpretedMessage(
                kind="reschedule_tomorrow",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Reschedule to tomorrow evening.",
                confidence=0.97,
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["완료", "끝냈", "끝났", "다했", "해냈"]):
            return InterpretedMessage(
                kind="mark_completed",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Task completed.",
                confidence=0.95,
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["못했", "못함", "못하겠", "못할것같", "실패"]):
            scope = "multiple" if self._mentions_multiple(normalized, matched_titles) else "active_task"
            return InterpretedMessage(
                kind="mark_missed",
                target_scope=scope if (today_tasks or active_task) else "none",
                summary="Task missed.",
                confidence=0.94,
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["일부", "조금", "반만", "조금만", "절반", "조금했"]):
            return InterpretedMessage(
                kind="mark_partial",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Task partially completed.",
                confidence=0.92,
                feedback_type="did_not_finish",
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["취소", "그만", "안할래", "안할게"]):
            return InterpretedMessage(
                kind="cancel_task",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Cancel current task.",
                confidence=0.9,
                mentioned_task_titles=matched_titles,
            )

        if "10분" in normalized and any(keyword in normalized for keyword in ["미뤄", "미루", "옮겨", "바꿔", "변경", "늦춰"]):
            return InterpretedMessage(
                kind="postpone_10",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Delay by 10 minutes.",
                confidence=0.9,
                reschedule_minutes=10,
                mentioned_task_titles=matched_titles,
            )

        if any(keyword in normalized for keyword in ["미뤄", "미루", "옮겨", "바꿔", "변경", "늦춰", "늦추"]):
            return InterpretedMessage(
                kind="postpone_custom",
                target_scope="active_task" if active_task or matched_titles else "none",
                summary="Reschedule current task.",
                confidence=0.65,
                reschedule_minutes=30,
                mentioned_task_titles=matched_titles,
            )

        if active_task and active_task.end_at < now:
            return InterpretedMessage(
                kind="status_update",
                target_scope="active_task",
                summary="General update for the latest active task.",
                confidence=0.35,
            )

        return InterpretedMessage(
            kind="unknown",
            target_scope="none",
            summary="No reliable interpretation.",
            confidence=0.1,
        )

    def _looks_like_specific_reschedule(self, normalized: str) -> bool:
        move_keywords = ["옮겨", "바꿔", "바꾸", "미뤄", "미루", "변경", "늦춰", "늦추"]
        time_keywords = ["오늘", "내일", "저녁", "오전", "오후", "밤", "새벽", "분뒤", "시", "반"]
        has_move = any(keyword in normalized for keyword in move_keywords)
        has_time = bool(re.search(r"\d{1,2}(시|분)?", normalized)) or any(keyword in normalized for keyword in time_keywords)
        return has_move and has_time

    def _looks_like_today_replan(self, normalized: str) -> bool:
        if "오늘" not in normalized:
            return False
        return any(
            keyword in normalized
            for keyword in ["정리", "재배치", "다시짜", "다시정리", "망했", "꼬였", "못하겠", "그냥"]
        )

    def _mentions_multiple(self, normalized: str, matched_titles: list[str]) -> bool:
        if len(matched_titles) >= 2:
            return True
        return any(keyword in normalized for keyword in ["둘다", "모두", "전부", "다못", "둘다못했"])

    def _extract_mentioned_task_titles(self, normalized: str, today_tasks) -> list[str]:
        matched_titles: list[str] = []
        seen_normalized_titles: set[str] = set()

        for task in today_tasks:
            title = getattr(task, "title", "") or ""
            topic = getattr(task, "topic", "") or ""
            normalized_title = self._normalize(title)
            normalized_topic = self._normalize(topic)
            if not normalized_title:
                continue

            if normalized_title in normalized or (normalized_topic and normalized_topic in normalized):
                if normalized_title not in seen_normalized_titles:
                    matched_titles.append(title)
                    seen_normalized_titles.add(normalized_title)

        return matched_titles

    def _normalize(self, text: str) -> str:
        return "".join(ch for ch in text.strip().lower() if not ch.isspace())
