from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from study_assistant.schemas.contracts import CreateUserRequest


@dataclass(slots=True)
class AssistantContext:
    now: datetime
    user: object
    daily_conversation: object | None = None
    active_task: object | None = None
    today_tasks: list[object] = field(default_factory=list)


class ContextAssembler:
    def __init__(self, timezone):
        self.timezone = timezone

    async def build_message_context(
        self,
        repo,
        *,
        telegram_user_id: int,
        chat_id: int,
        display_name: str | None,
        default_timezone: str,
        now: datetime,
    ) -> AssistantContext:
        user = await repo.get_or_create_user(
            CreateUserRequest(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=chat_id,
                display_name=display_name,
            ),
            timezone=default_timezone,
        )
        daily_conversation = await repo.get_or_create_daily_conversation(user.id, now.date())
        active_task = await repo.get_active_message_task(user.id, now)
        today_tasks = list(await repo.list_tasks_for_day(user.id, now.date(), self.timezone))
        self._localize_task_datetimes(active_task)
        for task in today_tasks:
            self._localize_task_datetimes(task)

        return AssistantContext(
            now=now,
            user=user,
            daily_conversation=daily_conversation,
            active_task=active_task,
            today_tasks=today_tasks,
        )

    async def build_button_context(
        self,
        repo,
        *,
        telegram_user_id: int,
        task_id: str,
        now: datetime,
    ) -> AssistantContext:
        user = await repo.get_user_by_telegram_user_id(telegram_user_id)
        task = await repo.get_task(task_id)
        self._localize_task_datetimes(task)
        return AssistantContext(
            now=now,
            user=user,
            active_task=task,
        )

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
                setattr(task, field_name, value.replace(tzinfo=self.timezone))
