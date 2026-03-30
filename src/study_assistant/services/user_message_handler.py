from __future__ import annotations

from datetime import datetime

from study_assistant.models.entities import PendingPromptType


class UserMessageHandler:
    def __init__(
        self,
        *,
        settings,
        context_assembler,
        command_handler,
        text_action_handler,
        assistant_brain,
        telegram_client,
    ):
        self.settings = settings
        self.context_assembler = context_assembler
        self.command_handler = command_handler
        self.text_action_handler = text_action_handler
        self.assistant_brain = assistant_brain
        self.telegram_client = telegram_client

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

        if (
            active_task is not None
            and active_task.pending_prompt_type == PendingPromptType.RESCHEDULE
            and not command.startswith("/")
        ):
            handled = await self.text_action_handler.handle_reschedule_followup(
                repo=repo,
                user=user,
                task=active_task,
                raw_text=event.text or "",
                now=now,
                daily_conversation=daily_conversation,
            )
            if handled:
                return

        brain_result = await self.assistant_brain.interpret_message(
            text=event.text or "",
            user=user,
            daily_conversation=daily_conversation,
            active_task=active_task,
            today_tasks=today_tasks,
            conversation_summary=context.conversation_summary,
            recent_dialogue=context.recent_dialogue,
            now=now,
        )

        if brain_result.needs_clarification and not brain_result.actions:
            clarification_text = brain_result.clarification_message or (
                "말하려는 작업을 조금만 더 구체적으로 말해줄래요?"
            )
            await self.telegram_client.send_message(event.chat_id, clarification_text)
            await repo.append_conversation_turn(
                daily_conversation,
                role="assistant",
                text=clarification_text,
                occurred_at=now,
            )
            return

        await self.text_action_handler.apply_interpreted_message(
            repo=repo,
            user=user,
            active_task=active_task,
            today_tasks=today_tasks,
            interpreted=brain_result,
            raw_text=event.text or "",
            now=now,
            daily_conversation=daily_conversation,
        )
