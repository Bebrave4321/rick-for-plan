from __future__ import annotations

from datetime import datetime, timedelta

from study_assistant.models.entities import TaskStatus


class DueScanService:
    def __init__(self, *, settings, session_factory, input_handler, event_processor):
        self.settings = settings
        self.session_factory = session_factory
        self.input_handler = input_handler
        self.event_processor = event_processor

    async def run(self, *, now: datetime) -> dict:
        due_events = []
        async with self.session_factory() as session:
            from study_assistant.repositories.assistant_repository import AssistantRepository

            repo = AssistantRepository(session)
            tasks = await repo.list_due_tasks(now)
            users = {user.id: user for user in await repo.list_users()}

            for task in tasks:
                self._localize_task_datetimes(task)
                user = users.get(task.user_id)
                if user is None:
                    continue
                duration = task.end_at - task.start_at

                if task.prep_reminder_sent_at is None and now >= task.start_at - timedelta(minutes=10):
                    due_events.append(
                        self.input_handler.from_scheduler_trigger(
                            telegram_user_id=user.telegram_user_id,
                            chat_id=user.telegram_chat_id,
                            task_id=task.id,
                            prompt_kind="prep",
                            occurred_at=now,
                        )
                    )

                if task.checkin_sent_at is None and now >= task.start_at:
                    due_events.append(
                        self.input_handler.from_scheduler_trigger(
                            telegram_user_id=user.telegram_user_id,
                            chat_id=user.telegram_chat_id,
                            task_id=task.id,
                            prompt_kind="checkin",
                            occurred_at=now,
                        )
                    )

                if (
                    task.checkin_sent_at is not None
                    and task.recheck_sent_at is None
                    and task.status == TaskStatus.CHECKIN_PENDING
                    and now >= task.checkin_sent_at + timedelta(minutes=10)
                ):
                    due_events.append(
                        self.input_handler.from_scheduler_trigger(
                            telegram_user_id=user.telegram_user_id,
                            chat_id=user.telegram_chat_id,
                            task_id=task.id,
                            prompt_kind="recheck",
                            occurred_at=now,
                        )
                    )

                if duration >= timedelta(hours=1) and task.status == TaskStatus.IN_PROGRESS and self._needs_progress_check(task, now):
                    due_events.append(
                        self.input_handler.from_scheduler_trigger(
                            telegram_user_id=user.telegram_user_id,
                            chat_id=user.telegram_chat_id,
                            task_id=task.id,
                            prompt_kind="progress",
                            occurred_at=now,
                        )
                    )

                if task.completion_prompt_sent_at is None and now >= task.end_at:
                    due_events.append(
                        self.input_handler.from_scheduler_trigger(
                            telegram_user_id=user.telegram_user_id,
                            chat_id=user.telegram_chat_id,
                            task_id=task.id,
                            prompt_kind="completion",
                            occurred_at=now,
                        )
                    )

        sent_count = 0
        for event in due_events:
            if await self.event_processor(event):
                sent_count += 1

        return {"sent_count": sent_count, "checked_at": now.isoformat()}

    def _needs_progress_check(self, task, now: datetime) -> bool:
        self._localize_task_datetimes(task)
        if task.last_progress_check_at is None:
            return now >= task.start_at + timedelta(hours=1)
        return now >= task.last_progress_check_at + timedelta(hours=1) and now < task.end_at

    def _localize_task_datetimes(self, task) -> None:
        if task is None:
            return

        datetime_fields = [
            "start_at",
            "end_at",
            "latest_prompt_sent_at",
            "prep_reminder_sent_at",
            "checkin_sent_at",
            "recheck_sent_at",
            "last_progress_check_at",
            "completion_prompt_sent_at",
            "completed_at",
        ]
        for field_name in datetime_fields:
            value = getattr(task, field_name, None)
            if value is not None and value.tzinfo is None:
                setattr(task, field_name, value.replace(tzinfo=self.settings.timezone))
