from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from study_assistant.models.entities import ChangeType, TaskStatus
from study_assistant.schemas.contracts import WeeklyReportResponse


@dataclass(slots=True)
class WeeklyReportWindow:
    start_date: date
    end_date: date
    start_at: datetime
    end_at: datetime


class WeeklyReportService:
    def __init__(self, timezone):
        self.timezone = timezone

    async def build_weekly_report(self, repo, *, user, reference_date: date) -> WeeklyReportResponse:
        window = self._build_window(reference_date)
        tasks = list(
            await repo.list_tasks_between(
                user.id,
                start_at=window.start_at,
                end_at=window.end_at,
            )
        )
        reschedule_logs = list(
            await repo.list_change_logs_between(
                user.id,
                start_at=window.start_at,
                end_at=window.end_at,
                change_type=ChangeType.RESCHEDULED,
            )
        )

        total_tasks = len(tasks)
        completed_tasks = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
        completion_rate = completed_tasks / total_tasks if total_tasks else 0.0

        return WeeklyReportResponse(
            week_start_date=window.start_date,
            week_end_date=window.end_date,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            completion_rate=completion_rate,
            rescheduled_count=len(reschedule_logs),
            best_time_window=self._best_time_window(tasks),
        )

    def _build_window(self, reference_date: date) -> WeeklyReportWindow:
        week_start = reference_date - timedelta(days=reference_date.weekday())
        week_end = week_start + timedelta(days=6)
        start_at = datetime.combine(week_start, time.min, tzinfo=self.timezone)
        end_at = datetime.combine(week_end + timedelta(days=1), time.min, tzinfo=self.timezone)
        return WeeklyReportWindow(
            start_date=week_start,
            end_date=week_end,
            start_at=start_at,
            end_at=end_at,
        )

    def _best_time_window(self, tasks: list[object]) -> str | None:
        completed_tasks = [task for task in tasks if task.status == TaskStatus.COMPLETED]
        if not completed_tasks:
            return None

        buckets = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
        for task in completed_tasks:
            hour = task.start_at.hour
            if 5 <= hour < 12:
                buckets["morning"] += 1
            elif 12 <= hour < 18:
                buckets["afternoon"] += 1
            elif 18 <= hour < 22:
                buckets["evening"] += 1
            else:
                buckets["night"] += 1

        bucket, count = max(buckets.items(), key=lambda item: item[1])
        if count == 0:
            return None

        labels = {
            "morning": "오전",
            "afternoon": "오후",
            "evening": "저녁",
            "night": "밤",
        }
        return labels[bucket]
