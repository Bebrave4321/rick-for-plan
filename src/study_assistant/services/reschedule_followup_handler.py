from __future__ import annotations

from datetime import datetime

from study_assistant.models.entities import PendingPromptType


class RescheduleFollowupHandler:
    def __init__(self, *, text_action_handler):
        self.text_action_handler = text_action_handler

    async def handle(
        self,
        *,
        repo,
        user,
        active_task,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
    ) -> bool:
        if (
            active_task is None
            or active_task.pending_prompt_type != PendingPromptType.RESCHEDULE
            or raw_text.strip().startswith("/")
        ):
            return False

        return await self.text_action_handler.handle_reschedule_followup(
            repo=repo,
            user=user,
            task=active_task,
            raw_text=raw_text,
            now=now,
            daily_conversation=daily_conversation,
        )
