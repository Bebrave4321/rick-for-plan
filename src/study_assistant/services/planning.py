from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from study_assistant.models.entities import TaskSource, User
from study_assistant.schemas.contracts import PlannedSession, WeeklyPlanDraft, WeeklyPlanningRequest


WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(slots=True)
class PlanningResult:
    draft: WeeklyPlanDraft
    source: TaskSource


class HeuristicPlanningService:
    def __init__(self, timezone):
        self.timezone = timezone

    def generate(self, request: WeeklyPlanningRequest, user: User) -> WeeklyPlanDraft:
        free_slots = self._build_free_slots(request, user)
        sessions: list[PlannedSession] = []
        overflow_notes: list[str] = []
        goals = sorted(
            request.goals,
            key=lambda goal: (
                goal.deadline or date.max,
                -goal.priority,
                goal.title.lower(),
            ),
        )

        for goal in goals:
            remaining_minutes = int(goal.target_hours * 60)
            session_length = goal.preferred_session_minutes

            while remaining_minutes > 0:
                slot_index = self._find_next_slot(free_slots, goal.deadline)
                if slot_index is None:
                    overflow_notes.append(
                        f"{goal.title}: not enough free time in this week. Remaining {remaining_minutes} minutes."
                    )
                    break

                slot_start, slot_end = free_slots[slot_index]
                slot_minutes = int((slot_end - slot_start).total_seconds() // 60)
                planned_minutes = min(session_length, remaining_minutes, slot_minutes)
                if planned_minutes < 45 and remaining_minutes > 45:
                    free_slots.pop(slot_index)
                    continue

                session_end = slot_start + timedelta(minutes=planned_minutes)
                sessions.append(
                    PlannedSession(
                        title=goal.title,
                        topic=goal.topic,
                        start_at=slot_start,
                        end_at=session_end,
                        importance=goal.priority,
                        notes=goal.notes,
                    )
                )
                remaining_minutes -= planned_minutes

                if session_end >= slot_end:
                    free_slots.pop(slot_index)
                else:
                    free_slots[slot_index] = (session_end, slot_end)

        sessions.sort(key=lambda item: item.start_at)
        return WeeklyPlanDraft(
            summary=self._build_summary(sessions, overflow_notes),
            sessions=sessions,
            overflow_notes=overflow_notes,
        )

    def _build_free_slots(self, request: WeeklyPlanningRequest, user: User) -> list[tuple[datetime, datetime]]:
        week_start = request.week_start_date
        windows: list[tuple[datetime, datetime]] = []

        for day_offset in range(7):
            current_date = week_start + timedelta(days=day_offset)
            day_start = datetime.combine(current_date, user.default_study_window_start, tzinfo=self.timezone)
            day_end = datetime.combine(current_date, user.default_study_window_end, tzinfo=self.timezone)
            day_slots = [(day_start, day_end)]

            for block in request.unavailable_blocks:
                if WEEKDAY_INDEX[block.day_of_week] != day_offset:
                    continue
                block_start = datetime.combine(current_date, block.start_time, tzinfo=self.timezone)
                block_end = datetime.combine(current_date, block.end_time, tzinfo=self.timezone)
                day_slots = self._subtract_interval(day_slots, (block_start, block_end))

            busy_day = next((item for item in request.busy_days if item.date == current_date), None)
            if busy_day and busy_day.max_study_minutes is not None:
                day_slots = self._cap_total_minutes(day_slots, busy_day.max_study_minutes)

            windows.extend(slot for slot in day_slots if slot[0] < slot[1])

        windows.sort(key=lambda item: item[0])
        return windows

    def _subtract_interval(
        self,
        slots: list[tuple[datetime, datetime]],
        block: tuple[datetime, datetime],
    ) -> list[tuple[datetime, datetime]]:
        result: list[tuple[datetime, datetime]] = []
        block_start, block_end = block
        for slot_start, slot_end in slots:
            if block_end <= slot_start or block_start >= slot_end:
                result.append((slot_start, slot_end))
                continue
            if block_start > slot_start:
                result.append((slot_start, block_start))
            if block_end < slot_end:
                result.append((block_end, slot_end))
        return result

    def _cap_total_minutes(
        self,
        slots: list[tuple[datetime, datetime]],
        max_minutes: int,
    ) -> list[tuple[datetime, datetime]]:
        remaining = max_minutes
        capped: list[tuple[datetime, datetime]] = []
        for slot_start, slot_end in slots:
            if remaining <= 0:
                break
            slot_minutes = int((slot_end - slot_start).total_seconds() // 60)
            if slot_minutes <= remaining:
                capped.append((slot_start, slot_end))
                remaining -= slot_minutes
                continue
            capped.append((slot_start, slot_start + timedelta(minutes=remaining)))
            remaining = 0
        return capped

    def _find_next_slot(
        self,
        free_slots: list[tuple[datetime, datetime]],
        deadline: date | None,
    ) -> int | None:
        for index, (slot_start, _) in enumerate(free_slots):
            if deadline is not None and slot_start.date() > deadline:
                continue
            return index
        return None

    def _build_summary(self, sessions: list[PlannedSession], overflow_notes: list[str]) -> str:
        if not sessions:
            return "No sessions could be placed. Please provide more free time for this week."
        first_day = sessions[0].start_at.strftime("%Y-%m-%d")
        last_day = sessions[-1].start_at.strftime("%Y-%m-%d")
        summary = f"Drafted {len(sessions)} study sessions between {first_day} and {last_day}."
        if overflow_notes:
            summary += f" {len(overflow_notes)} goal(s) still need extra time."
        return summary


class PlanningService:
    def __init__(self, heuristic: HeuristicPlanningService, openai_client):
        self.heuristic = heuristic
        self.openai_client = openai_client

    async def generate(self, request: WeeklyPlanningRequest, user: User, daily_conversation) -> PlanningResult:
        if self.openai_client.enabled:
            draft = await self.openai_client.generate_weekly_plan(request, user, daily_conversation)
            if draft is not None:
                return PlanningResult(draft=draft, source=TaskSource.AI)

        return PlanningResult(
            draft=self.heuristic.generate(request, user),
            source=TaskSource.HEURISTIC,
        )
