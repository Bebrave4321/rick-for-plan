from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from study_assistant.core.config import Settings
from study_assistant.models.entities import (
    PendingPromptType,
    TaskStatus,
    WeeklyPlanStatus,
)
from study_assistant.repositories.assistant_repository import AssistantRepository
from study_assistant.schemas.contracts import (
    CreateUserRequest,
    DashboardResponse,
    PlanConfirmationResponse,
    PlanSubmissionRequest,
    TaskView,
    UserSummary,
)
from study_assistant.services.context_assembler import ContextAssembler
from study_assistant.services.decision_engine import DecisionEngine
from study_assistant.services.button_action_handler import ButtonActionHandler
from study_assistant.services.command_handler import CommandHandler
from study_assistant.services.input_handler import InputHandler
from study_assistant.services.internal_events import InternalEvent
from study_assistant.services.response_composer import ResponseComposer
from study_assistant.services.task_executor import TaskExecutor
from study_assistant.services.text_action_handler import TextActionHandler
from study_assistant.services.assistant_brain import AssistantBrain
from study_assistant.services.weekly_report_service import WeeklyReportService


logger = logging.getLogger(__name__)


class StudyAssistantService:
    def __init__(
        self,
        settings: Settings,
        session_factory,
        planning_service,
        message_interpreter,
        telegram_client,
        openai_client,
        decision_engine: DecisionEngine | None = None,
        input_handler: InputHandler | None = None,
        context_assembler: ContextAssembler | None = None,
        assistant_brain: AssistantBrain | None = None,
        response_composer: ResponseComposer | None = None,
        task_executor: TaskExecutor | None = None,
        text_action_handler: TextActionHandler | None = None,
        button_action_handler: ButtonActionHandler | None = None,
        weekly_report_service: WeeklyReportService | None = None,
        command_handler: CommandHandler | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.planning_service = planning_service
        self.telegram_client = telegram_client
        self.openai_client = openai_client
        self.decision_engine = decision_engine or DecisionEngine(settings.timezone)
        self.input_handler = input_handler or InputHandler()
        self.context_assembler = context_assembler or ContextAssembler(settings.timezone)
        self.assistant_brain = assistant_brain or AssistantBrain(message_interpreter)
        self.response_composer = response_composer or ResponseComposer()
        self.task_executor = task_executor or TaskExecutor(settings.timezone)
        self.text_action_handler = text_action_handler or TextActionHandler(
            telegram_client=telegram_client,
            response_composer=self.response_composer,
            task_executor=self.task_executor,
            decision_engine=self.decision_engine,
        )
        self.button_action_handler = button_action_handler or ButtonActionHandler(
            telegram_client=telegram_client,
            response_composer=self.response_composer,
            task_executor=self.task_executor,
            text_action_handler=self.text_action_handler,
            decision_engine=self.decision_engine,
        )
        self.weekly_report_service = weekly_report_service or WeeklyReportService(settings.timezone)
        self.command_handler = command_handler or CommandHandler(
            telegram_client=telegram_client,
            response_composer=self.response_composer,
            weekly_report_service=self.weekly_report_service,
        )

    async def close(self) -> None:
        await self.telegram_client.close()
        await self.openai_client.close()

    async def ensure_integrations_ready(self) -> None:
        if not self.settings.telegram_bot_token:
            return
        if self.settings.base_url.startswith("http://localhost") or self.settings.base_url.startswith("http://127.0.0.1"):
            return
        try:
            await self.telegram_client.set_webhook()
        except Exception:  # noqa: BLE001
            logger.exception("Telegram webhook registration failed")

    def now(self) -> datetime:
        return datetime.now(self.settings.timezone)

    async def bootstrap_user(self, payload: CreateUserRequest) -> UserSummary:
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_or_create_user(payload, timezone=self.settings.default_timezone)
            await session.commit()
            return self._to_user_summary(user)

    async def submit_weekly_plan(self, payload: PlanSubmissionRequest) -> dict:
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_user_by_telegram_user_id(payload.telegram_user_id)
            if user is None:
                user = await repo.get_or_create_user(
                    CreateUserRequest(
                        telegram_user_id=payload.telegram_user_id,
                        telegram_chat_id=payload.telegram_user_id,
                    ),
                    timezone=self.settings.default_timezone,
                )

            daily_conversation = await repo.get_or_create_daily_conversation(user.id, self.now().date())
            plan_result = await self.planning_service.generate(
                request=payload.planning_request,
                user=user,
                daily_conversation=daily_conversation,
            )
            weekly_plan, tasks = await repo.upsert_weekly_plan(
                user=user,
                request=payload.planning_request,
                draft=plan_result.draft,
                source=plan_result.source,
            )
            await session.commit()

        await self.telegram_client.send_message(
            user.telegram_chat_id,
            self.response_composer.weekly_plan_message(plan_result.draft),
        )

        return {
            "plan_id": weekly_plan.id,
            "status": weekly_plan.status.value,
            "source": plan_result.source.value,
            "task_count": len(tasks),
            "summary": plan_result.draft.summary,
        }

    async def confirm_weekly_plan(self, plan_id: str) -> PlanConfirmationResponse:
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            plan = await repo.get_weekly_plan(plan_id)
            if plan is None:
                raise ValueError("Weekly plan not found.")
            plan.status = WeeklyPlanStatus.CONFIRMED
            await session.commit()
        return PlanConfirmationResponse(plan_id=plan_id, status=WeeklyPlanStatus.CONFIRMED.value)

    async def get_dashboard(self, telegram_user_id: int) -> DashboardResponse:
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_user_by_telegram_user_id(telegram_user_id)
            if user is None:
                raise ValueError("User not found.")

            today = self.now().date()
            yesterday = today - timedelta(days=1)
            today_tasks = await repo.list_tasks_for_day(user.id, today, self.settings.timezone)
            yesterday_tasks = await repo.list_tasks_for_day(user.id, yesterday, self.settings.timezone)
            latest_plan = await repo.get_latest_weekly_plan(user.id)

            return DashboardResponse(
                user=self._to_user_summary(user),
                latest_plan_id=latest_plan.id if latest_plan else None,
                latest_plan_status=latest_plan.status.value if latest_plan else None,
                draft_summary=latest_plan.draft_summary if latest_plan else None,
                today_tasks=[self._to_task_view(task) for task in today_tasks],
                yesterday_tasks=[self._to_task_view(task) for task in yesterday_tasks],
            )

    async def get_weekly_report(self, telegram_user_id: int):
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_user_by_telegram_user_id(telegram_user_id)
            if user is None:
                raise ValueError("User not found.")

            return await self.weekly_report_service.build_weekly_report(
                repo,
                user=user,
                reference_date=self.now().date(),
            )

    async def run_due_scan(self) -> dict:
        now = self.now()
        due_events: list[InternalEvent] = []
        async with self.session_factory() as session:
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
            if await self._handle_internal_event(event):
                sent_count += 1

        return {"sent_count": sent_count, "checked_at": now.isoformat()}

    async def send_daily_summaries(self) -> dict:
        today = self.now().date()
        sent = 0
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            users = await repo.list_users()
            for user in users:
                if not user.morning_summary_enabled or user.last_daily_summary_sent_for == today:
                    continue

                yesterday_tasks = await repo.list_tasks_for_day(user.id, today - timedelta(days=1), self.settings.timezone)
                today_tasks = await repo.list_tasks_for_day(user.id, today, self.settings.timezone)
                await self.telegram_client.send_message(
                    user.telegram_chat_id,
                    self.response_composer.daily_summary(yesterday_tasks, today_tasks),
                )
                user.last_daily_summary_sent_for = today
                await repo.get_or_create_daily_conversation(
                    user.id,
                    conversation_date=today,
                    started_by_morning_summary=True,
                )
                sent += 1
            await session.commit()
        return {"sent_count": sent, "date": today.isoformat()}

    async def send_weekly_planning_prompts(self) -> dict:
        today = self.now().date()
        sent = 0
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            users = await repo.list_users()
            for user in users:
                if user.last_weekly_prompt_sent_for == today:
                    continue
                await self.telegram_client.send_message(
                    user.telegram_chat_id,
                    "이번 주 비가용 시간과 공부 목표를 보내주세요. /plan 을 보내면 입력 형식을 안내할게요.",
                )
                user.last_weekly_prompt_sent_for = today
                sent += 1
            await session.commit()
        return {"sent_count": sent, "date": today.isoformat()}

    async def prune_historical_data(self) -> dict:
        now = self.now()
        cutoff_date = self._retention_week_start(now.date())
        cutoff_datetime = datetime.combine(cutoff_date, time.min, tzinfo=self.settings.timezone)

        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            deleted = await repo.prune_historical_data(
                task_cutoff=cutoff_datetime,
                conversation_cutoff=cutoff_date,
                plan_cutoff=cutoff_date,
            )
            await session.commit()

        return {
            **deleted,
            "kept_from_week_start": cutoff_date.isoformat(),
            "checked_at": now.isoformat(),
        }

    async def process_telegram_update(self, payload: dict) -> dict:
        event = self.input_handler.from_telegram_update(payload)
        if event is None:
            return {"ok": True}

        await self._handle_internal_event(event)
        if event.event_type == "button_action" and event.callback_query_id:
            await self.telegram_client.answer_callback_query(event.callback_query_id)
        return {"ok": True}

    async def process_text_message(self, telegram_user_id: int, chat_id: int, display_name: str | None, text: str) -> None:
        event = self.input_handler.from_text_message(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            display_name=display_name,
            text=text,
        )
        await self._handle_internal_event(event)

    async def process_callback_query(self, telegram_user_id: int, chat_id: int, callback_data: str) -> None:
        event = self.input_handler.from_callback_query(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            callback_data=callback_data,
        )
        await self._handle_internal_event(event)

    async def _handle_internal_event(self, event: InternalEvent) -> bool:
        if event.event_type == "user_message":
            await self._handle_user_message_event(event)
            return True
        if event.event_type == "button_action":
            await self._handle_button_action_event(event)
            return True
        if event.event_type == "scheduler_event":
            return await self._handle_scheduler_event(event)
        return False

    async def _handle_user_message_event(self, event: InternalEvent) -> None:
        now = self.now()
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            context = await self.context_assembler.build_message_context(
                repo,
                telegram_user_id=event.telegram_user_id,
                chat_id=event.chat_id,
                display_name=event.display_name,
                default_timezone=self.settings.default_timezone,
                now=now,
            )
            user = context.user
            daily_conversation = context.daily_conversation
            active_task = context.active_task
            today_tasks = context.today_tasks

            command = (event.text or "").strip().lower()
            if await self.command_handler.handle(
                repo=repo,
                user=user,
                chat_id=event.chat_id,
                command=command,
                now=now,
            ):
                await session.commit()
                return

            if (
                active_task is not None
                and active_task.pending_prompt_type == PendingPromptType.RESCHEDULE
                and not command.startswith("/")
            ):
                handled = await self.text_action_handler.handle_reschedule_followup(
                    repo=repo,
                    user=user,
                    task=active_task,
                    raw_text=event.text or "",
                    now=now,
                )
                if handled:
                    await session.commit()
                    return

            brain_result = await self.assistant_brain.interpret_message(
                text=event.text or "",
                user=user,
                daily_conversation=daily_conversation,
                active_task=active_task,
                today_tasks=today_tasks,
                now=now,
            )

            await self.text_action_handler.apply_interpreted_message(
                repo=repo,
                user=user,
                active_task=active_task,
                today_tasks=today_tasks,
                interpreted=brain_result,
                raw_text=event.text or "",
                now=now,
            )
            await session.commit()

    async def _handle_button_action_event(self, event: InternalEvent) -> None:
        parsed = self.button_action_handler.parse_callback_data(event.callback_data)
        if parsed is None:
            await self.telegram_client.send_message(event.chat_id, "버튼 정보를 이해하지 못했어요.")
            return
        task_id, action = parsed

        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            context = await self.context_assembler.build_button_context(
                repo,
                telegram_user_id=event.telegram_user_id,
                task_id=task_id,
                now=self.now(),
            )
            user = context.user
            task = context.active_task
            if user is None or task is None:
                await self.telegram_client.send_message(event.chat_id, "대상 일정을 찾지 못했어요.")
                return

            await self.button_action_handler.handle(
                repo=repo,
                user=user,
                task=task,
                action=action,
                chat_id=event.chat_id,
                now=context.now,
            )
            await session.commit()

    async def _handle_scheduler_event(self, event: InternalEvent) -> bool:
        if event.task_id is None or event.chat_id is None or event.prompt_kind is None:
            return False

        now = event.occurred_at or self.now()
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            context = await self.context_assembler.build_task_context(
                repo,
                telegram_user_id=event.telegram_user_id,
                task_id=event.task_id,
                now=now,
            )
            user = context.user
            task = context.active_task
            if user is None or task is None:
                return False

            if not self.task_executor.apply_due_prompt_state(task, prompt_kind=event.prompt_kind, occurred_at=now):
                return False

            await self.telegram_client.send_message(
                event.chat_id,
                self.response_composer.prompt_text(task, event.prompt_kind),
                reply_markup=self.response_composer.prompt_keyboard(task.id, event.prompt_kind),
            )

            await session.commit()
            return True

    def _needs_progress_check(self, task, now: datetime) -> bool:
        self._localize_task_datetimes(task)
        if task.last_progress_check_at is None:
            return now >= task.start_at + timedelta(hours=1)
        return now >= task.last_progress_check_at + timedelta(hours=1) and now < task.end_at

    def _retention_week_start(self, today: date) -> date:
        current_week_start = today - timedelta(days=today.weekday())
        weeks_to_keep = max(self.settings.data_retention_weeks - 1, 0)
        return current_week_start - timedelta(weeks=weeks_to_keep)

    def _localize_task_datetimes(self, task) -> None:
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

    def _to_user_summary(self, user) -> UserSummary:
        return UserSummary(
            id=user.id,
            telegram_user_id=user.telegram_user_id,
            telegram_chat_id=user.telegram_chat_id,
            display_name=user.display_name,
            timezone=user.timezone,
        )

    def _to_task_view(self, task) -> TaskView:
        self._localize_task_datetimes(task)
        return TaskView(
            id=task.id,
            title=task.title,
            topic=task.topic,
            start_at=task.start_at,
            end_at=task.end_at,
            status=task.status.value,
            importance=task.importance,
            pending_prompt_type=task.pending_prompt_type.value if task.pending_prompt_type else None,
        )
