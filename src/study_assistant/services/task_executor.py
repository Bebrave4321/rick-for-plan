from __future__ import annotations

from datetime import datetime, time, timedelta

from study_assistant.models.entities import ChangeType, TaskStatus


class TaskExecutor:
    def __init__(self, timezone):
        self.timezone = timezone

    async def mark_task_completed(self, repo, task, completed_at: datetime) -> None:
        task.status = TaskStatus.COMPLETED
        task.completed_at = completed_at
        task.pending_prompt_type = None

    async def shift_task(
        self,
        repo,
        task,
        *,
        minutes: int,
        reason: str,
        reference_now: datetime,
    ) -> None:
        old_start = task.start_at
        old_end = task.end_at
        delta = timedelta(minutes=minutes)
        task.start_at = task.start_at + delta
        task.end_at = task.end_at + delta
        task.status = TaskStatus.RESCHEDULED
        task.pending_prompt_type = None
        task.checkin_sent_at = None
        task.recheck_sent_at = None
        task.completion_prompt_sent_at = None
        task.last_progress_check_at = None
        if task.start_at <= reference_now + timedelta(minutes=10):
            task.prep_reminder_sent_at = reference_now
        else:
            task.prep_reminder_sent_at = None
        task.latest_prompt_sent_at = None
        await repo.add_change_log(
            task,
            old_start_at=old_start,
            old_end_at=old_end,
            new_start_at=task.start_at,
            new_end_at=task.end_at,
            change_type=ChangeType.RESCHEDULED,
            reason=reason,
        )

    async def reschedule_to_datetime(
        self,
        repo,
        task,
        *,
        new_start_at: datetime,
        reason: str,
        reference_now: datetime,
    ) -> None:
        old_start = task.start_at
        old_end = task.end_at
        duration = task.end_at - task.start_at
        task.start_at = new_start_at
        task.end_at = new_start_at + duration
        task.status = TaskStatus.RESCHEDULED
        task.pending_prompt_type = None
        task.checkin_sent_at = None
        task.recheck_sent_at = None
        task.completion_prompt_sent_at = None
        task.last_progress_check_at = None
        if task.start_at <= reference_now + timedelta(minutes=10):
            task.prep_reminder_sent_at = reference_now
        else:
            task.prep_reminder_sent_at = None
        task.latest_prompt_sent_at = None
        await repo.add_change_log(
            task,
            old_start_at=old_start,
            old_end_at=old_end,
            new_start_at=task.start_at,
            new_end_at=task.end_at,
            change_type=ChangeType.RESCHEDULED,
            reason=reason,
        )

    async def cancel_task(self, repo, task, *, reason: str) -> None:
        old_start = task.start_at
        old_end = task.end_at
        task.status = TaskStatus.CANCELLED
        task.pending_prompt_type = None
        await repo.add_change_log(
            task,
            old_start_at=old_start,
            old_end_at=old_end,
            new_start_at=None,
            new_end_at=None,
            change_type=ChangeType.CANCELLED,
            reason=reason,
        )

    async def reschedule_to_tonight(self, repo, task, *, now: datetime) -> None:
        anchor = self._today_evening_anchor(now)
        await self.reschedule_to_datetime(
            repo,
            task,
            new_start_at=anchor,
            reason="Rescheduled to tonight.",
            reference_now=now,
        )

    async def reschedule_to_tomorrow(self, repo, task, *, now: datetime) -> None:
        anchor = datetime.combine(now.date() + timedelta(days=1), time(19, 0), tzinfo=self.timezone)
        await self.reschedule_to_datetime(
            repo,
            task,
            new_start_at=anchor,
            reason="Rescheduled to tomorrow evening.",
            reference_now=now,
        )

    async def replan_multiple_tasks(self, repo, tasks, *, now: datetime) -> None:
        if not tasks:
            return
        current_start = self._today_evening_anchor(now)
        for task in sorted(tasks, key=lambda item: item.start_at):
            duration = task.end_at - task.start_at
            old_start = task.start_at
            old_end = task.end_at
            task.start_at = current_start
            task.end_at = current_start + duration
            task.status = TaskStatus.RESCHEDULED
            task.pending_prompt_type = None
            task.checkin_sent_at = None
            task.recheck_sent_at = None
            task.prep_reminder_sent_at = None
            task.completion_prompt_sent_at = None
            await repo.add_change_log(
                task,
                old_start_at=old_start,
                old_end_at=old_end,
                new_start_at=task.start_at,
                new_end_at=task.end_at,
                change_type=ChangeType.RESCHEDULED,
                reason="Bulk replan from current time.",
            )
            current_start = task.end_at + timedelta(minutes=15)

    def _today_evening_anchor(self, now: datetime) -> datetime:
        proposed = now + timedelta(minutes=30)
        if proposed.hour < 19:
            return datetime.combine(now.date(), time(19, 0), tzinfo=self.timezone)
        if proposed.hour >= 22:
            return datetime.combine(now.date() + timedelta(days=1), time(19, 0), tzinfo=self.timezone)
        if proposed.minute == 0:
            return proposed.replace(second=0, microsecond=0)
        if proposed.minute <= 30:
            return proposed.replace(minute=30, second=0, microsecond=0)
        return (proposed + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
