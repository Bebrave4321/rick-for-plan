from __future__ import annotations

from datetime import datetime


class UserMessageHandler:
    def __init__(
        self,
        *,
        settings,
        context_assembler,
        command_handler,
        reschedule_followup_handler,
        assistant_brain,
        brain_result_handler,
    ):
        self.settings = settings
        self.context_assembler = context_assembler
        self.command_handler = command_handler
        self.reschedule_followup_handler = reschedule_followup_handler
        self.assistant_brain = assistant_brain
        self.brain_result_handler = brain_result_handler

    async def handle(self, *, repo, event, now: datetime) -> None:
        context = await self.context_assembler.build_message_context(
            repo,
            telegram_user_id=event.telegram_user_id,
            chat_id=event.chat_id,
            display_name=event.display_name,
            default_timezone=self.settings.default_timezone,
            now=now,
        )
        user = context.user
        daily_conversation = context.daily_conversation
        active_task = context.active_task
        today_tasks = context.today_tasks

        command = (event.text or "").strip().lower()
        await repo.append_conversation_turn(
            daily_conversation,
            role="user",
            text=event.text or "",
            occurred_at=now,
        )
        if await self.command_handler.handle(
            repo=repo,
            user=user,
            daily_conversation=daily_conversation,
            chat_id=event.chat_id,
            command=command,
            now=now,
        ):
            return

        if await self.reschedule_followup_handler.handle(
            repo=repo,
            user=user,
            active_task=active_task,
            raw_text=event.text or "",
            now=now,
            daily_conversation=daily_conversation,
        ):
            return

        brain_result = await self.assistant_brain.interpret_message(
            text=event.text or "",
            user=user,
            daily_conversation=daily_conversation,
            active_task=active_task,
            today_tasks=today_tasks,
            conversation_summary=context.conversation_summary,
            recent_dialogue=context.recent_dialogue,
            dialogue_transcript=context.dialogue_transcript,
            last_user_turn=context.last_user_turn,
            last_assistant_turn=context.last_assistant_turn,
            active_prompt_kind=context.active_prompt_kind,
            now=now,
        )

        await self.brain_result_handler.handle(
            repo=repo,
            user=user,
            chat_id=event.chat_id,
            active_task=active_task,
            today_tasks=today_tasks,
            brain_result=brain_result,
            raw_text=event.text or "",
            now=now,
            daily_conversation=daily_conversation,
        )
