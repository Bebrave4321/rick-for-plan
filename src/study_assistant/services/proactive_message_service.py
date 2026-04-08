from __future__ import annotations

from datetime import date, datetime, timedelta


class ProactiveMessageService:
    def __init__(self, *, settings, session_factory, response_composer, telegram_client, now_provider):
        self.settings = settings
        self.session_factory = session_factory
        self.response_composer = response_composer
        self.telegram_client = telegram_client
        self.now_provider = now_provider

    async def send_daily_summaries(self) -> dict:
        today = self.now_provider().date()
        sent = 0
        async with self.session_factory() as session:
            from study_assistant.repositories.assistant_repository import AssistantRepository

            repo = AssistantRepository(session)
            users = await repo.list_users()
            for user in users:
                if not user.morning_summary_enabled or user.last_daily_summary_sent_for == today:
                    continue

                yesterday_tasks = await repo.list_tasks_for_day(user.id, today - timedelta(days=1), self.settings.timezone)
                today_tasks = await repo.list_tasks_for_day(user.id, today, self.settings.timezone)
                summary_text = self.response_composer.daily_summary(yesterday_tasks, today_tasks)
                await self.telegram_client.send_message(user.telegram_chat_id, summary_text)
                user.last_daily_summary_sent_for = today
                conversation = await repo.get_or_create_daily_conversation(
                    user.id,
                    conversation_date=today,
                    started_by_morning_summary=True,
                )
                await repo.append_conversation_turn(
                    conversation,
                    role="assistant",
                    text=summary_text,
                    occurred_at=self.now_provider(),
                )
                sent += 1
            await session.commit()
        return {"sent_count": sent, "date": today.isoformat()}

    async def send_weekly_planning_prompts(self) -> dict:
        today = self.now_provider().date()
        sent = 0
        async with self.session_factory() as session:
            from study_assistant.repositories.assistant_repository import AssistantRepository

            repo = AssistantRepository(session)
            users = await repo.list_users()
            for user in users:
                if user.last_weekly_prompt_sent_for == today:
                    continue
                planning_prompt = self.response_composer.weekly_planning_prompt()
                await self.telegram_client.send_message(user.telegram_chat_id, planning_prompt)
                user.last_weekly_prompt_sent_for = today
                conversation = await repo.get_or_create_daily_conversation(user.id, today)
                await repo.append_conversation_turn(
                    conversation,
                    role="assistant",
                    text=planning_prompt,
                    occurred_at=self.now_provider(),
                )
                sent += 1
            await session.commit()
        return {"sent_count": sent, "date": today.isoformat()}
