from __future__ import annotations

from datetime import datetime


class SchedulerEventHandler:
    def __init__(self, *, context_assembler, task_executor, response_composer, telegram_client):
        self.context_assembler = context_assembler
        self.task_executor = task_executor
        self.response_composer = response_composer
        self.telegram_client = telegram_client

    async def handle(self, *, repo, event, now: datetime) -> bool:
        if event.task_id is None or event.chat_id is None or event.prompt_kind is None:
            return False

        context = await self.context_assembler.build_task_context(
            repo,
            telegram_user_id=event.telegram_user_id,
            task_id=event.task_id,
            now=now,
        )
        user = context.user
        task = context.active_task
        if user is None or task is None:
            return False

        if not self.task_executor.apply_due_prompt_state(task, prompt_kind=event.prompt_kind, occurred_at=now):
            return False

        prompt_text = self.response_composer.prompt_text(task, event.prompt_kind)
        await self.telegram_client.send_message(
            event.chat_id,
            prompt_text,
            reply_markup=self.response_composer.prompt_keyboard(task.id, event.prompt_kind),
        )
        await repo.append_conversation_turn(
            context.daily_conversation,
            role="assistant",
            text=prompt_text,
            occurred_at=now,
        )
        return True
