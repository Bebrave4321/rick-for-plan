from datetime import date, time
from zoneinfo import ZoneInfo

from study_assistant.schemas.contracts import StudyGoalInput, UnavailableBlockInput, WeeklyPlanningRequest
from study_assistant.services.planning import HeuristicPlanningService


class DummyUser:
    timezone = "Asia/Seoul"
    default_study_window_start = time(7, 0)
    default_study_window_end = time(23, 0)


def test_heuristic_planner_respects_unavailable_blocks():
    planner = HeuristicPlanningService(ZoneInfo("Asia/Seoul"))
    request = WeeklyPlanningRequest(
        week_start_date=date(2026, 3, 30),
        unavailable_blocks=[
            UnavailableBlockInput(
                day_of_week="monday",
                start_time=time(7, 0),
                end_time=time(12, 0),
                label="morning class",
            )
        ],
        goals=[
            StudyGoalInput(
                title="English reading",
                target_hours=2,
                priority=3,
                preferred_session_minutes=60,
            )
        ],
    )

    draft = planner.generate(request, DummyUser())

    assert draft.sessions
    assert all(session.start_at.hour >= 12 for session in draft.sessions if session.start_at.date() == date(2026, 3, 30))


def test_heuristic_planner_generates_overflow_when_time_is_tight():
    planner = HeuristicPlanningService(ZoneInfo("Asia/Seoul"))
    request = WeeklyPlanningRequest(
        week_start_date=date(2026, 3, 30),
        goals=[
            StudyGoalInput(
                title="Huge backlog",
                target_hours=120,
                priority=5,
                preferred_session_minutes=120,
            )
        ],
    )

    draft = planner.generate(request, DummyUser())

    assert draft.sessions
    assert draft.overflow_notes
