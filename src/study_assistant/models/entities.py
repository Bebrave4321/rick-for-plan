from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Date, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from study_assistant.db.session import Base


def make_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class WeeklyPlanStatus(str, Enum):
    DRAFT = "draft"
    NEEDS_REVISION = "needs_revision"
    CONFIRMED = "confirmed"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    CHECKIN_PENDING = "checkin_pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"
    MISSED = "missed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"


class PendingPromptType(str, Enum):
    CHECKIN = "checkin"
    RECHECK = "recheck"
    PROGRESS = "progress"
    COMPLETION = "completion"
    FEEDBACK = "feedback"
    RESCHEDULE = "reschedule"


class ResponseSource(str, Enum):
    BUTTON = "button"
    FREE_TEXT = "free_text"
    SYSTEM = "system"


class ChangeType(str, Enum):
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    SPLIT = "split"


class TaskSource(str, Enum):
    AI = "ai"
    HEURISTIC = "heuristic"
    MANUAL = "manual"


class FeedbackType(str, Enum):
    DID_NOT_FINISH = "did_not_finish"
    TOOK_LONGER = "took_longer"
    SLEEPY = "sleepy"
    DISTRACTED = "distracted"
    INTERRUPTED = "interrupted"
    FINISHED_EARLY = "finished_early"
    OTHER = "other"


class DailyConversationStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul", nullable=False)
    default_study_window_start: Mapped[time] = mapped_column(Time, default=time(7, 0), nullable=False)
    default_study_window_end: Mapped[time] = mapped_column(Time, default=time(23, 0), nullable=False)
    morning_summary_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    progress_checks_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    quiet_hours: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    last_daily_summary_sent_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_weekly_prompt_sent_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    weekly_plans: Mapped[list["WeeklyPlan"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tasks: Mapped[list["StudyTask"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    daily_conversations: Mapped[list["DailyConversation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class DailyConversation(Base):
    __tablename__ = "daily_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[DailyConversationStatus] = mapped_column(
        SqlEnum(DailyConversationStatus),
        default=DailyConversationStatus.ACTIVE,
        nullable=False,
    )
    openai_conversation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_response_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by_morning_summary: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="daily_conversations")


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[WeeklyPlanStatus] = mapped_column(
        SqlEnum(WeeklyPlanStatus),
        default=WeeklyPlanStatus.DRAFT,
        nullable=False,
    )
    plan_origin: Mapped[TaskSource] = mapped_column(SqlEnum(TaskSource), default=TaskSource.HEURISTIC, nullable=False)
    unavailable_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    goal_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    deadline_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    busy_days: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    draft_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    overflow_notes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="weekly_plans")
    tasks: Mapped[list["StudyTask"]] = relationship(back_populates="weekly_plan")


class StudyTask(Base):
    __tablename__ = "study_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    weekly_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    source: Mapped[TaskSource] = mapped_column(SqlEnum(TaskSource), default=TaskSource.HEURISTIC, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(SqlEnum(TaskStatus), default=TaskStatus.PLANNED, nullable=False)
    pending_prompt_type: Mapped[PendingPromptType | None] = mapped_column(SqlEnum(PendingPromptType), nullable=True)
    latest_prompt_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prep_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkin_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recheck_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_progress_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_prompt_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="tasks")
    weekly_plan: Mapped["WeeklyPlan | None"] = relationship(back_populates="tasks")
    responses: Mapped[list["TaskResponse"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    change_logs: Mapped[list["TaskChangeLog"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskResponse(Base):
    __tablename__ = "task_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("study_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[ResponseSource] = mapped_column(SqlEnum(ResponseSource), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    interpreted_kind: Mapped[str | None] = mapped_column(String(80), nullable=True)
    interpreted_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_status: Mapped[TaskStatus | None] = mapped_column(SqlEnum(TaskStatus), nullable=True)
    feedback_type: Mapped[FeedbackType | None] = mapped_column(SqlEnum(FeedbackType), nullable=True)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    task: Mapped["StudyTask"] = relationship(back_populates="responses")


class TaskChangeLog(Base):
    __tablename__ = "task_change_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("study_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    old_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    old_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_type: Mapped[ChangeType] = mapped_column(SqlEnum(ChangeType), nullable=False)
    approved: Mapped[bool] = mapped_column(default=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    task: Mapped["StudyTask"] = relationship(back_populates="change_logs")
