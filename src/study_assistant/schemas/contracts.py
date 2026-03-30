from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field


Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class UnavailableBlockInput(BaseModel):
    day_of_week: Weekday
    start_time: time
    end_time: time
    label: str | None = None


class DeadlineInput(BaseModel):
    title: str
    due_at: datetime
    notes: str | None = None


class BusyDayInput(BaseModel):
    date: date
    note: str | None = None
    max_study_minutes: int | None = Field(default=None, ge=0, le=720)


class StudyGoalInput(BaseModel):
    title: str
    topic: str | None = None
    target_hours: float = Field(gt=0)
    priority: int = Field(default=3, ge=1, le=5)
    deadline: date | None = None
    preferred_session_minutes: int = Field(default=90, ge=30, le=240)
    notes: str | None = None


class WeeklyPlanningRequest(BaseModel):
    week_start_date: date
    unavailable_blocks: list[UnavailableBlockInput] = Field(default_factory=list)
    goals: list[StudyGoalInput] = Field(default_factory=list)
    deadlines: list[DeadlineInput] = Field(default_factory=list)
    busy_days: list[BusyDayInput] = Field(default_factory=list)


class PlannedSession(BaseModel):
    title: str
    topic: str | None = None
    start_at: datetime
    end_at: datetime
    importance: int = Field(default=3, ge=1, le=5)
    notes: str | None = None


class WeeklyPlanDraft(BaseModel):
    summary: str
    sessions: list[PlannedSession] = Field(default_factory=list)
    overflow_notes: list[str] = Field(default_factory=list)


class CreateUserRequest(BaseModel):
    telegram_user_id: int
    telegram_chat_id: int
    display_name: str | None = None
    study_window_start: time | None = None
    study_window_end: time | None = None


class PlanSubmissionRequest(BaseModel):
    telegram_user_id: int
    planning_request: WeeklyPlanningRequest


class TaskView(BaseModel):
    id: str
    title: str
    topic: str | None
    start_at: datetime
    end_at: datetime
    status: str
    importance: int
    pending_prompt_type: str | None


class UserSummary(BaseModel):
    id: str
    telegram_user_id: int
    telegram_chat_id: int
    display_name: str | None
    timezone: str


class DashboardResponse(BaseModel):
    user: UserSummary
    latest_plan_id: str | None
    latest_plan_status: str | None
    draft_summary: str | None
    today_tasks: list[TaskView]
    yesterday_tasks: list[TaskView]


class PlanConfirmationResponse(BaseModel):
    plan_id: str
    status: str


IntentKind = Literal[
    "weekly_plan_request",
    "weekly_plan_input",
    "mark_completed",
    "mark_partial",
    "mark_missed",
    "reschedule_specific_time",
    "reschedule_tonight",
    "reschedule_tomorrow",
    "postpone_10",
    "postpone_custom",
    "cancel_task",
    "replan_today",
    "status_update",
    "unknown",
]

FeedbackKind = Literal[
    "did_not_finish",
    "took_longer",
    "sleepy",
    "distracted",
    "interrupted",
    "finished_early",
    "other",
    None,
]

InterpretationSource = Literal["openai", "rule"]
ResponseMode = Literal["action", "clarify"]


class InterpretedMessage(BaseModel):
    kind: IntentKind
    target_scope: Literal["active_task", "today", "multiple", "none"] = "none"
    summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_message: str | None = None
    reschedule_minutes: int | None = None
    feedback_type: FeedbackKind = None
    target_task_ids: list[str] = Field(default_factory=list)
    mentioned_task_titles: list[str] = Field(default_factory=list)


class ActionProposal(BaseModel):
    kind: IntentKind
    target_scope: Literal["active_task", "today", "multiple", "none"] = "none"
    target_task_id: str | None = None
    target_task_title: str | None = None
    summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reschedule_minutes: int | None = None
    feedback_type: FeedbackKind = None


class BrainResult(BaseModel):
    actions: list[ActionProposal] = Field(default_factory=list)
    summary: str = ""
    source: InterpretationSource = "rule"
    response_mode: ResponseMode = "action"
    needs_clarification: bool = False
    clarification_message: str | None = None

    @property
    def primary_action(self) -> ActionProposal | None:
        return self.actions[0] if self.actions else None

    @property
    def kind(self) -> IntentKind:
        if self.primary_action is None:
            return "unknown"
        return self.primary_action.kind

    @property
    def target_scope(self) -> Literal["active_task", "today", "multiple", "none"]:
        if len(self.actions) > 1:
            return "multiple"
        if self.primary_action is None:
            return "none"
        return self.primary_action.target_scope

    @property
    def confidence(self) -> float:
        if self.primary_action is None:
            return 0.0
        return self.primary_action.confidence

    @property
    def reschedule_minutes(self) -> int | None:
        if self.primary_action is None:
            return None
        return self.primary_action.reschedule_minutes

    @property
    def feedback_type(self) -> FeedbackKind:
        if self.primary_action is None:
            return None
        return self.primary_action.feedback_type


class WeeklyReportResponse(BaseModel):
    week_start_date: date
    week_end_date: date
    total_tasks: int
    completed_tasks: int
    completion_rate: float = Field(ge=0.0, le=1.0)
    rescheduled_count: int = Field(ge=0)
    best_time_window: str | None = None
