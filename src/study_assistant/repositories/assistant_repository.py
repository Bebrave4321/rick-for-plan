from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Sequence

from sqlalchemy import delete, desc, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_assistant.models.entities import (
    ChangeType,
    DailyConversation,
    StudyTask,
    TaskChangeLog,
    TaskResponse,
    TaskSource,
    TaskStatus,
    User,
    WeeklyPlan,
    WeeklyPlanStatus,
)
from study_assistant.schemas.contracts import CreateUserRequest, PlannedSession, WeeklyPlanDraft, WeeklyPlanningRequest


FINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.PARTIAL,
    TaskStatus.MISSED,
    TaskStatus.CANCELLED,
}


class AssistantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, payload: CreateUserRequest, timezone: str) -> User:
        result = await self.session.execute(
            select(User).where(User.telegram_user_id == payload.telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_user_id=payload.telegram_user_id,
                telegram_chat_id=payload.telegram_chat_id,
                display_name=payload.display_name,
                timezone=timezone,
            )
            if payload.study_window_start is not None:
                user.default_study_window_start = payload.study_window_start
            if payload.study_window_end is not None:
                user.default_study_window_end = payload.study_window_end
            self.session.add(user)
            await self.session.flush()
            return user

        user.telegram_chat_id = payload.telegram_chat_id
        if payload.display_name:
            user.display_name = payload.display_name
        if payload.study_window_start is not None:
            user.default_study_window_start = payload.study_window_start
        if payload.study_window_end is not None:
            user.default_study_window_end = payload.study_window_end
        return user

    async def get_user_by_telegram_user_id(self, telegram_user_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def list_users(self) -> Sequence[User]:
        result = await self.session.execute(select(User).order_by(User.created_at.asc()))
        return result.scalars().all()

    async def get_or_create_daily_conversation(
        self,
        user_id: str,
        conversation_date: date,
        started_by_morning_summary: bool = False,
    ) -> DailyConversation:
        result = await self.session.execute(
            select(DailyConversation).where(
                DailyConversation.user_id == user_id,
                DailyConversation.conversation_date == conversation_date,
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            conversation = DailyConversation(
                user_id=user_id,
                conversation_date=conversation_date,
                started_by_morning_summary=started_by_morning_summary,
            )
            self.session.add(conversation)
            await self.session.flush()
            return conversation

        if started_by_morning_summary:
            conversation.started_by_morning_summary = True
        return conversation

    async def get_latest_weekly_plan(self, user_id: str) -> WeeklyPlan | None:
        result = await self.session.execute(
            select(WeeklyPlan)
            .where(WeeklyPlan.user_id == user_id)
            .order_by(WeeklyPlan.week_start_date.desc(), WeeklyPlan.created_at.desc())
        )
        return result.scalars().first()

    async def get_weekly_plan(self, plan_id: str) -> WeeklyPlan | None:
        result = await self.session.execute(select(WeeklyPlan).where(WeeklyPlan.id == plan_id))
        return result.scalar_one_or_none()

    async def upsert_weekly_plan(
        self,
        user: User,
        request: WeeklyPlanningRequest,
        draft: WeeklyPlanDraft,
        source: TaskSource,
    ) -> tuple[WeeklyPlan, list[StudyTask]]:
        result = await self.session.execute(
            select(WeeklyPlan).where(
                WeeklyPlan.user_id == user.id,
                WeeklyPlan.week_start_date == request.week_start_date,
            )
        )
        weekly_plan = result.scalar_one_or_none()
        if weekly_plan is None:
            weekly_plan = WeeklyPlan(
                user_id=user.id,
                week_start_date=request.week_start_date,
            )
            self.session.add(weekly_plan)

        weekly_plan.status = WeeklyPlanStatus.DRAFT
        weekly_plan.plan_origin = source
        weekly_plan.unavailable_blocks = [item.model_dump(mode="json") for item in request.unavailable_blocks]
        weekly_plan.goal_items = [item.model_dump(mode="json") for item in request.goals]
        weekly_plan.deadline_items = [item.model_dump(mode="json") for item in request.deadlines]
        weekly_plan.busy_days = [item.model_dump(mode="json") for item in request.busy_days]
        weekly_plan.draft_summary = draft.summary
        weekly_plan.overflow_notes = draft.overflow_notes
        await self.session.flush()
        tasks = await self.replace_plan_tasks(user, weekly_plan, draft.sessions, source)
        return weekly_plan, tasks

    async def replace_plan_tasks(
        self,
        user: User,
        weekly_plan: WeeklyPlan,
        sessions: Sequence[PlannedSession],
        source: TaskSource,
    ) -> list[StudyTask]:
        await self.session.execute(delete(StudyTask).where(StudyTask.weekly_plan_id == weekly_plan.id))
        tasks: list[StudyTask] = []
        for session in sessions:
            task = StudyTask(
                user_id=user.id,
                weekly_plan_id=weekly_plan.id,
                title=session.title,
                topic=session.topic,
                notes=session.notes,
                start_at=session.start_at,
                end_at=session.end_at,
                importance=session.importance,
                source=source,
            )
            self.session.add(task)
            tasks.append(task)
        await self.session.flush()
        return tasks

    async def list_tasks_for_day(self, user_id: str, target_date: date, timezone) -> Sequence[StudyTask]:
        day_start = datetime.combine(target_date, time.min, tzinfo=timezone)
        day_end = day_start + timedelta(days=1)
        result = await self.session.execute(
            select(StudyTask)
            .where(
                StudyTask.user_id == user_id,
                StudyTask.start_at >= day_start,
                StudyTask.start_at < day_end,
            )
            .order_by(StudyTask.start_at.asc())
        )
        return result.scalars().all()

    async def get_active_message_task(self, user_id: str, now: datetime) -> StudyTask | None:
        result = await self.session.execute(
            select(StudyTask)
            .where(
                StudyTask.user_id == user_id,
                StudyTask.pending_prompt_type.is_not(None),
            )
            .order_by(desc(StudyTask.latest_prompt_sent_at))
        )
        task = result.scalars().first()
        if task is not None:
            return task

        result = await self.session.execute(
            select(StudyTask)
            .where(
                StudyTask.user_id == user_id,
                StudyTask.start_at <= now + timedelta(hours=1),
                StudyTask.end_at >= now - timedelta(hours=6),
                StudyTask.status.not_in(FINAL_TASK_STATUSES),
            )
            .order_by(StudyTask.start_at.asc())
        )
        return result.scalars().first()

    async def get_task(self, task_id: str) -> StudyTask | None:
        result = await self.session.execute(select(StudyTask).where(StudyTask.id == task_id))
        return result.scalar_one_or_none()

    async def list_due_tasks(self, now: datetime) -> Sequence[StudyTask]:
        horizon_start = now - timedelta(days=1)
        horizon_end = now + timedelta(days=1)
        result = await self.session.execute(
            select(StudyTask)
            .where(
                StudyTask.start_at <= horizon_end,
                StudyTask.end_at >= horizon_start,
                StudyTask.status.not_in(FINAL_TASK_STATUSES),
            )
            .order_by(StudyTask.start_at.asc())
        )
        return result.scalars().all()

    async def record_task_response(
        self,
        task: StudyTask,
        source,
        raw_text: str | None,
        interpreted_kind: str,
        interpreted_payload: dict,
        result_status=None,
        feedback_type=None,
        feedback_text: str | None = None,
    ) -> TaskResponse:
        response = TaskResponse(
            task_id=task.id,
            user_id=task.user_id,
            source=source,
            raw_text=raw_text,
            interpreted_kind=interpreted_kind,
            interpreted_payload=interpreted_payload,
            result_status=result_status,
            feedback_type=feedback_type,
            feedback_text=feedback_text,
        )
        self.session.add(response)
        await self.session.flush()
        return response

    async def add_change_log(
        self,
        task: StudyTask,
        old_start_at: datetime | None,
        old_end_at: datetime | None,
        new_start_at: datetime | None,
        new_end_at: datetime | None,
        change_type: ChangeType,
        reason: str,
        approved: bool = True,
    ) -> TaskChangeLog:
        log = TaskChangeLog(
            task_id=task.id,
            old_start_at=old_start_at,
            old_end_at=old_end_at,
            new_start_at=new_start_at,
            new_end_at=new_end_at,
            change_type=change_type,
            reason=reason,
            approved=approved,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def prune_historical_data(
        self,
        *,
        task_cutoff: datetime,
        conversation_cutoff: date,
        plan_cutoff: date,
    ) -> dict[str, int]:
        old_task_ids = select(StudyTask.id).where(StudyTask.end_at < task_cutoff)

        task_response_result = await self.session.execute(
            delete(TaskResponse).where(TaskResponse.task_id.in_(old_task_ids))
        )
        change_log_result = await self.session.execute(
            delete(TaskChangeLog).where(TaskChangeLog.task_id.in_(old_task_ids))
        )
        task_result = await self.session.execute(
            delete(StudyTask).where(StudyTask.end_at < task_cutoff)
        )
        conversation_result = await self.session.execute(
            delete(DailyConversation).where(DailyConversation.conversation_date < conversation_cutoff)
        )
        active_tasks_exist = exists(
            select(StudyTask.id).where(StudyTask.weekly_plan_id == WeeklyPlan.id)
        )
        weekly_plan_result = await self.session.execute(
            delete(WeeklyPlan).where(
                WeeklyPlan.week_start_date < plan_cutoff,
                ~active_tasks_exist,
            )
        )

        return {
            "deleted_task_responses": task_response_result.rowcount or 0,
            "deleted_change_logs": change_log_result.rowcount or 0,
            "deleted_tasks": task_result.rowcount or 0,
            "deleted_daily_conversations": conversation_result.rowcount or 0,
            "deleted_weekly_plans": weekly_plan_result.rowcount or 0,
        }
