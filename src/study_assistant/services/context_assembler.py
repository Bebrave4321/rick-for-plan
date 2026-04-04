from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from study_assistant.repositories.assistant_repository import FINAL_TASK_STATUSES
from study_assistant.schemas.contracts import CreateUserRequest


@dataclass(slots=True)
class AssistantContext:
    now: datetime
    user: object
    daily_conversation: object | None = None
    active_task: object | None = None
    today_tasks: list[object] = field(default_factory=list)
    conversation_summary: str | None = None
    recent_dialogue: list[dict[str, str]] = field(default_factory=list)
    last_user_turn: dict[str, str] | None = None
    last_assistant_turn: dict[str, str] | None = None
    active_prompt_kind: str | None = None


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
        conversation_summary, recent_dialogue = repo.get_conversation_context(daily_conversation)
        active_task = await repo.get_active_message_task(user.id, now)
        today_tasks = list(await repo.list_tasks_for_day(user.id, now.date(), self.timezone))
        self._localize_task_datetimes(active_task)
        for task in today_tasks:
            self._localize_task_datetimes(task)
        today_tasks = await self._merge_recent_overdue_tasks(repo, user_id=user.id, now=now, today_tasks=today_tasks)
        today_tasks = await self._merge_nearby_upcoming_tasks(repo, user_id=user.id, now=now, today_tasks=today_tasks)

        return AssistantContext(
            now=now,
            user=user,
            daily_conversation=daily_conversation,
            active_task=active_task,
            today_tasks=today_tasks,
            conversation_summary=conversation_summary,
            recent_dialogue=recent_dialogue,
            last_user_turn=self._last_turn_for_role(recent_dialogue, "user"),
            last_assistant_turn=self._last_turn_for_role(recent_dialogue, "assistant"),
            active_prompt_kind=self._active_prompt_kind(active_task),
        )

    async def build_button_context(
        self,
        repo,
        *,
        telegram_user_id: int,
        task_id: str,
        now: datetime,
    ) -> AssistantContext:
        return await self.build_task_context(
            repo,
            telegram_user_id=telegram_user_id,
            task_id=task_id,
            now=now,
        )

    async def build_task_context(
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
        daily_conversation = None
        conversation_summary = None
        recent_dialogue: list[dict[str, str]] = []
        if user is not None:
            daily_conversation = await repo.get_or_create_daily_conversation(user.id, now.date())
            conversation_summary, recent_dialogue = repo.get_conversation_context(daily_conversation)
        return AssistantContext(
            now=now,
            user=user,
            daily_conversation=daily_conversation,
            active_task=task,
            conversation_summary=conversation_summary,
            recent_dialogue=recent_dialogue,
            last_user_turn=self._last_turn_for_role(recent_dialogue, "user"),
            last_assistant_turn=self._last_turn_for_role(recent_dialogue, "assistant"),
            active_prompt_kind=self._active_prompt_kind(task),
        )

    async def _merge_recent_overdue_tasks(self, repo, *, user_id: str, now: datetime, today_tasks: list[object]) -> list[object]:
        day_start = datetime.combine(now.date(), time.min, tzinfo=self.timezone)
        recent_window_start = day_start - timedelta(hours=6)
        carryover_tasks = await repo.list_tasks_between(
            user_id,
            start_at=recent_window_start,
            end_at=day_start,
        )

        merged_by_id = {task.id: task for task in today_tasks}
        for task in carryover_tasks:
            self._localize_task_datetimes(task)
            if task.status in FINAL_TASK_STATUSES:
                continue
            if task.end_at > now:
                continue
            merged_by_id.setdefault(task.id, task)

        return sorted(merged_by_id.values(), key=lambda task: task.start_at)

    async def _merge_nearby_upcoming_tasks(self, repo, *, user_id: str, now: datetime, today_tasks: list[object]) -> list[object]:
        next_day_start = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=self.timezone)
        nearby_window_end = next_day_start + timedelta(hours=6)
        nearby_tasks = await repo.list_tasks_between(
            user_id,
            start_at=next_day_start,
            end_at=nearby_window_end,
        )

        merged_by_id = {task.id: task for task in today_tasks}
        for task in nearby_tasks:
            self._localize_task_datetimes(task)
            if task.status in FINAL_TASK_STATUSES:
                continue
            merged_by_id.setdefault(task.id, task)

        return sorted(merged_by_id.values(), key=lambda task: task.start_at)

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

    def _last_turn_for_role(self, recent_dialogue: list[dict[str, str]], role: str) -> dict[str, str] | None:
        for turn in reversed(recent_dialogue):
            if turn.get("role") == role:
                return turn
        return None

    def _active_prompt_kind(self, task) -> str | None:
        if task is None or getattr(task, "pending_prompt_type", None) is None:
            return None
        return task.pending_prompt_type.value
