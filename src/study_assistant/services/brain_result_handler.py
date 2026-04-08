from __future__ import annotations

from datetime import datetime


class BrainResultHandler:
    def __init__(self, *, telegram_client, text_action_handler):
        self.telegram_client = telegram_client
        self.text_action_handler = text_action_handler

    async def handle(
        self,
        *,
        repo,
        user,
        chat_id: int,
        active_task,
        today_tasks,
        brain_result,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
    ) -> None:
        if brain_result.needs_clarification and not brain_result.actions:
            clarification_text = (
                brain_result.clarification_message
                or "말하려는 작업을 조금만 더 구체적으로 말해줄래요?"
            )
            await self.telegram_client.send_message(chat_id, clarification_text)
            await repo.append_conversation_turn(
                daily_conversation,
                role="assistant",
                text=clarification_text,
                occurred_at=now,
            )
            return

        await self.text_action_handler.apply_brain_result(
            repo=repo,
            user=user,
            active_task=active_task,
            today_tasks=today_tasks,
            brain_result=brain_result,
            raw_text=raw_text,
            now=now,
            daily_conversation=daily_conversation,
        )
