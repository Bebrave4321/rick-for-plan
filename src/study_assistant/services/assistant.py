from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from study_assistant.core.config import Settings
from study_assistant.models.entities import (
    FeedbackType,
    PendingPromptType,
    ResponseSource,
    StudyTask,
    TaskSource,
    TaskStatus,
    WeeklyPlanStatus,
)
from study_assistant.repositories.assistant_repository import AssistantRepository, FINAL_TASK_STATUSES
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
from study_assistant.services.command_handler import CommandHandler
from study_assistant.services.input_handler import InputHandler
from study_assistant.services.internal_events import InternalEvent
from study_assistant.services.response_composer import ResponseComposer
from study_assistant.services.task_executor import TaskExecutor
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
            if await self._handle_command(
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
                handled = await self._handle_reschedule_followup(
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

            await self._apply_interpreted_message(
                repo,
                user,
                active_task,
                today_tasks,
                brain_result,
                event.text or "",
                now,
            )
            await session.commit()

    async def _handle_command(self, repo, user, chat_id: int, command: str, now: datetime) -> bool:
        if command == "/testcheckin":
            task = await self._create_manual_test_task(
                repo,
                user=user,
                title="빠른 체크인 테스트",
                start_at=now,
                end_at=now + timedelta(minutes=25),
                status=TaskStatus.CHECKIN_PENDING,
                pending_prompt_type=PendingPromptType.CHECKIN,
                prompt_sent_at=now,
            )
            await self.telegram_client.send_message(
                chat_id,
                f"빠른 테스트예요. 지금 '{task.title}' 시작했나요?",
                reply_markup=self.response_composer.checkin_keyboard(task.id),
            )
            return True

        if command == "/testcomplete":
            task = await self._create_manual_test_task(
                repo,
                user=user,
                title="빠른 종료 테스트",
                start_at=now - timedelta(minutes=25),
                end_at=now - timedelta(minutes=5),
                status=TaskStatus.IN_PROGRESS,
                pending_prompt_type=PendingPromptType.COMPLETION,
                prompt_sent_at=now,
            )
            await self.telegram_client.send_message(
                chat_id,
                f"빠른 테스트예요. '{task.title}' 마무리됐어요?",
                reply_markup=self.response_composer.completion_keyboard(task.id),
            )
            return True

        return await self.command_handler.handle(
            repo=repo,
            user=user,
            chat_id=chat_id,
            command=command,
            now=now,
        )

    async def _handle_button_action_event(self, event: InternalEvent) -> None:
        try:
            _, task_id, action = (event.callback_data or "").split(":", 2)
        except ValueError:
            await self.telegram_client.send_message(event.chat_id, "버튼 정보를 이해하지 못했어요.")
            return

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

            now = context.now
            if action == "start":
                self.task_executor.mark_task_started(task)
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="started",
                    interpreted_kind="mark_started",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.IN_PROGRESS,
                )
                await self.telegram_client.send_message(event.chat_id, f"좋아요. '{task.title}' 시작으로 기록할게요.")
            elif action == "delay10":
                await self._shift_task(repo, task, minutes=10, reason="User requested 10 minute delay.", reference_now=now)
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="delay10",
                    interpreted_kind="postpone_10",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.RESCHEDULED,
                )
                await self.telegram_client.send_message(event.chat_id, f"'{task.title}' 일정을 10분 뒤로 옮겼어요.")
            elif action == "skip":
                await self._mark_task_for_reschedule(
                    repo,
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="skip",
                    interpreted_kind="mark_missed",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.MISSED,
                    feedback_type=None,
                    lead_text=f"괜찮아요. '{task.title}'은 못 한 것으로 기록했어요. 다시 잡을까요?",
                    chat_id=event.chat_id,
                )
            elif action == "progress_ok":
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="progress_ok",
                    interpreted_kind="progress_ok",
                    interpreted_payload={"action": action},
                )
                await self.telegram_client.send_message(event.chat_id, "좋아요. 그대로 이어가면 돼요.")
            elif action == "progress_help":
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="progress_help",
                    interpreted_kind="progress_help",
                    interpreted_payload={"action": action},
                )
                await self.telegram_client.send_message(event.chat_id, "괜찮아요. 끝난 뒤 남은 분량만 알려주면 다시 정리할게요.")
            elif action == "done":
                await self._mark_task_completed(repo, task, ResponseSource.BUTTON, "done")
                await self.telegram_client.send_message(event.chat_id, f"좋아요. '{task.title}' 완료로 기록했어요.")
            elif action == "partial":
                await self._mark_task_for_reschedule(
                    repo,
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="partial",
                    interpreted_kind="mark_partial",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.PARTIAL,
                    feedback_type=FeedbackType.DID_NOT_FINISH,
                    lead_text=f"'{task.title}'은 일부 완료로 기록했어요. 남은 분량을 다시 잡을까요?",
                    chat_id=event.chat_id,
                )
            elif action == "missed":
                await self._mark_task_for_reschedule(
                    repo,
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="missed",
                    interpreted_kind="mark_missed",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.MISSED,
                    feedback_type=None,
                    lead_text=f"알겠어요. '{task.title}'은 못 한 일정으로 기록했어요. 다시 잡을까요?",
                    chat_id=event.chat_id,
                )
            elif action == "reschedTonight":
                await self._reschedule_to_tonight(repo, task, now)
                await self.telegram_client.send_message(event.chat_id, self.response_composer.reschedule_confirmation(task, "오늘 저녁"))
            elif action == "reschedTomorrow":
                await self._reschedule_to_tomorrow(repo, task, now)
                await self.telegram_client.send_message(event.chat_id, self.response_composer.reschedule_confirmation(task, "내일 저녁"))
            elif action == "suggest":
                suggestions = self.decision_engine.build_reschedule_suggestions(now)
                await self.telegram_client.send_message(
                    event.chat_id,
                    self.decision_engine.suggestion_text(suggestions, task.end_at - task.start_at),
                )
            elif action == "cancel":
                await self._cancel_task(repo, task, reason="User cancelled the task.")
                await self.telegram_client.send_message(event.chat_id, f"'{task.title}' 일정은 취소로 처리했어요.")
            else:
                await self.telegram_client.send_message(event.chat_id, "아직 지원하지 않는 버튼이에요.")

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

    async def _apply_interpreted_message(self, repo, user, active_task, today_tasks, interpreted, raw_text: str, now: datetime) -> None:
        if interpreted.kind == "weekly_plan_request":
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                (
                    "주간 계획은 현재 구조화된 입력이 가장 안정적이에요. "
                    "README의 `/api/plans/weekly` 예시를 쓰거나, 비가용 시간과 목표를 정리해서 보내주세요."
                ),
            )
            return

        if interpreted.kind == "weekly_plan_input":
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                "주간 입력으로 보이지만, 현재 구현에서는 `/api/plans/weekly`가 가장 안정적이에요.",
            )
            return

        if self._requires_active_task(interpreted.kind) and active_task is None and interpreted.target_scope != "multiple":
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                "지금 연결할 일정이 없어요. 일정 제목을 같이 보내주거나 오늘 일정을 먼저 확인해볼게요.",
            )
            return

        handlers = {
            "mark_completed": self._handle_completed_text_action,
            "mark_partial": self._handle_partial_text_action,
            "mark_missed": self._handle_missed_text_action,
            "reschedule_tonight": self._handle_tonight_reschedule_text_action,
            "reschedule_tomorrow": self._handle_tomorrow_reschedule_text_action,
            "postpone_10": self._handle_postpone_text_action,
            "postpone_custom": self._handle_postpone_text_action,
            "cancel_task": self._handle_cancel_text_action,
            "replan_today": self._handle_replan_today_text_action,
        }
        handler = handlers.get(interpreted.kind)
        if handler is not None:
            await handler(
                repo=repo,
                user=user,
                active_task=active_task,
                today_tasks=today_tasks,
                interpreted=interpreted,
                raw_text=raw_text,
                now=now,
            )
            return

        await self.telegram_client.send_message(
            user.telegram_chat_id,
            "메시지 뜻을 확실히 못 잡았어요. '완료했어', '10분 미뤄줘', '오늘은 쉬고 싶어'처럼 보내주면 바로 반영할게요.",
        )

    def _requires_active_task(self, kind: str) -> bool:
        return kind in {
            "mark_completed",
            "mark_partial",
            "mark_missed",
            "reschedule_tonight",
            "reschedule_tomorrow",
            "postpone_10",
            "postpone_custom",
            "cancel_task",
        }

    async def _handle_completed_text_action(self, *, repo, user, active_task, **kwargs) -> None:
        await self._mark_task_completed(repo, active_task, ResponseSource.FREE_TEXT, kwargs["raw_text"])
        await self.telegram_client.send_message(user.telegram_chat_id, f"좋아요. '{active_task.title}' 완료로 기록했어요.")

    async def _handle_partial_text_action(self, *, repo, user, active_task, interpreted, raw_text: str, **kwargs) -> None:
        await self._mark_task_for_reschedule(
            repo,
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.PARTIAL,
            feedback_type=FeedbackType.DID_NOT_FINISH,
            lead_text=f"'{active_task.title}'은 일부 완료로 기록했어요. 다시 잡을까요?",
            chat_id=user.telegram_chat_id,
        )

    async def _handle_missed_text_action(self, *, repo, user, active_task, today_tasks, interpreted, raw_text: str, now: datetime, **kwargs) -> None:
        if interpreted.target_scope == "multiple":
            target_task_ids = {
                action.target_task_id
                for action in getattr(interpreted, "actions", [])
                if getattr(action, "target_task_id", None)
            }
            if target_task_ids:
                pending_tasks = [task for task in today_tasks if task.id in target_task_ids]
            else:
                pending_tasks = [
                    task for task in today_tasks
                    if task.status not in FINAL_TASK_STATUSES and task.end_at <= now
                ]
            for task in pending_tasks:
                await repo.record_task_response(
                    task,
                    source=ResponseSource.FREE_TEXT,
                    raw_text=raw_text,
                    interpreted_kind="mark_missed",
                    interpreted_payload={
                        "multi_action": True,
                        "target_task_ids": list(target_task_ids),
                    },
                    result_status=TaskStatus.MISSED,
                )
            await self._replan_multiple_tasks(repo, pending_tasks, now)
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                self.response_composer.multiple_missed_replan_summary(pending_tasks),
            )
            return

        await self._mark_task_for_reschedule(
            repo,
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.MISSED,
            feedback_type=None,
            lead_text=f"알겠어요. '{active_task.title}'은 못 한 일정으로 기록했어요. 다시 잡을까요?",
            chat_id=user.telegram_chat_id,
        )

    async def _handle_tonight_reschedule_text_action(self, *, repo, user, active_task, interpreted, raw_text: str, now: datetime, **kwargs) -> None:
        await self._reschedule_to_tonight(repo, active_task, now)
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.RESCHEDULED,
        )
        await self.telegram_client.send_message(
            user.telegram_chat_id,
            self.response_composer.reschedule_confirmation(active_task, "오늘 저녁"),
        )

    async def _handle_tomorrow_reschedule_text_action(self, *, repo, user, active_task, interpreted, raw_text: str, now: datetime, **kwargs) -> None:
        await self._reschedule_to_tomorrow(repo, active_task, now)
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.RESCHEDULED,
        )
        await self.telegram_client.send_message(
            user.telegram_chat_id,
            self.response_composer.reschedule_confirmation(active_task, "내일 저녁"),
        )

    async def _handle_postpone_text_action(self, *, repo, user, active_task, interpreted, raw_text: str, now: datetime, **kwargs) -> None:
        minutes = interpreted.reschedule_minutes or 10
        await self._shift_task(
            repo,
            active_task,
            minutes=minutes,
            reason=f"User postponed by {minutes} minutes.",
            reference_now=now,
        )
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.RESCHEDULED,
        )
        await self.telegram_client.send_message(
            user.telegram_chat_id,
            f"좋아요. '{active_task.title}' 일정을 {minutes}분 뒤로 옮겼어요.",
        )

    async def _handle_cancel_text_action(self, *, repo, user, active_task, interpreted, raw_text: str, **kwargs) -> None:
        await self._cancel_task(repo, active_task, reason="User cancelled through text message.")
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.CANCELLED,
        )
        await self.telegram_client.send_message(user.telegram_chat_id, f"'{active_task.title}' 일정은 취소로 처리했어요.")

    async def _handle_replan_today_text_action(self, *, repo, user, today_tasks, now: datetime, **kwargs) -> None:
        unfinished = [
            task for task in today_tasks
            if task.status not in FINAL_TASK_STATUSES and task.end_at >= now - timedelta(hours=2)
        ]
        await self._replan_multiple_tasks(repo, unfinished, now)
        await self.telegram_client.send_message(
            user.telegram_chat_id,
            "오늘 남은 일정을 다시 정리했어요. 너무 빡빡하지 않게 뒤로 재배치했습니다.",
        )

    async def _handle_reschedule_followup(self, repo, user, task, raw_text: str, now: datetime) -> bool:
        decision = self.decision_engine.decide_reschedule(raw_text, now)

        if decision.decision_type == "clarify":
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                decision.clarification_message or self.response_composer.freeform_reschedule_help(),
            )
            return True

        if decision.decision_type == "suggest":
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                self.decision_engine.suggestion_text(decision.suggestions, task.end_at - task.start_at),
            )
            return True

        if decision.decision_type == "cancel":
            await self._cancel_task(repo, task, reason="User cancelled during reschedule follow-up.")
            await repo.record_task_response(
                task,
                source=ResponseSource.FREE_TEXT,
                raw_text=raw_text,
                interpreted_kind="cancel_task",
                interpreted_payload={"decision_type": decision.decision_type},
                result_status=TaskStatus.CANCELLED,
            )
            await self.telegram_client.send_message(user.telegram_chat_id, f"'{task.title}' 일정은 취소로 처리했어요.")
            return True

        if decision.decision_type == "reschedule" and decision.parsed_time is not None:
            await self._reschedule_to_datetime(
                repo,
                task,
                new_start_at=decision.parsed_time.start_at,
                reason=f"Rescheduled from natural-language follow-up: {raw_text}",
                reference_now=now,
            )
            await repo.record_task_response(
                task,
                source=ResponseSource.FREE_TEXT,
                raw_text=raw_text,
                interpreted_kind="reschedule_specific_time",
                interpreted_payload={
                    "decision_type": decision.decision_type,
                    "label": decision.parsed_time.label,
                    "start_at": decision.parsed_time.start_at.isoformat(),
                },
                result_status=TaskStatus.RESCHEDULED,
            )
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                self.response_composer.precise_reschedule_confirmation(task),
            )
            return True

        return False

    async def _mark_task_completed(self, repo, task, source, raw_text: str) -> None:
        await self.task_executor.mark_task_completed(repo, task, completed_at=self.now())
        await repo.record_task_response(
            task,
            source=source,
            raw_text=raw_text,
            interpreted_kind="mark_completed",
            interpreted_payload={"raw_text": raw_text},
            result_status=TaskStatus.COMPLETED,
        )

    async def _mark_task_for_reschedule(
        self,
        repo,
        task,
        *,
        source,
        raw_text: str,
        interpreted_kind: str,
        interpreted_payload: dict,
        result_status: TaskStatus,
        feedback_type,
        lead_text: str,
        chat_id: int,
    ) -> None:
        self.task_executor.mark_task_for_reschedule(task, result_status=result_status)
        await repo.record_task_response(
            task,
            source=source,
            raw_text=raw_text,
            interpreted_kind=interpreted_kind,
            interpreted_payload=interpreted_payload,
            result_status=result_status,
            feedback_type=feedback_type,
        )
        await self.telegram_client.send_message(
            chat_id,
            self.response_composer.reschedule_prompt(lead_text),
            reply_markup=self.response_composer.reschedule_keyboard(task.id),
        )

    async def _create_manual_test_task(
        self,
        repo,
        *,
        user,
        title: str,
        start_at: datetime,
        end_at: datetime,
        status: TaskStatus,
        pending_prompt_type: PendingPromptType,
        prompt_sent_at: datetime,
    ) -> StudyTask:
        task = StudyTask(
            user_id=user.id,
            title=title,
            topic="test",
            notes="Created from Telegram fast-test command.",
            start_at=start_at,
            end_at=end_at,
            importance=1,
            source=TaskSource.MANUAL,
            status=status,
            pending_prompt_type=pending_prompt_type,
            latest_prompt_sent_at=prompt_sent_at,
            prep_reminder_sent_at=prompt_sent_at,
        )
        if pending_prompt_type == PendingPromptType.CHECKIN:
            task.checkin_sent_at = prompt_sent_at
        if pending_prompt_type == PendingPromptType.COMPLETION:
            task.checkin_sent_at = start_at
            task.completion_prompt_sent_at = prompt_sent_at

        repo.session.add(task)
        await repo.session.flush()
        return task

    async def _shift_task(
        self,
        repo,
        task,
        minutes: int,
        reason: str,
        reference_now: datetime | None = None,
    ) -> None:
        await self.task_executor.shift_task(
            repo,
            task,
            minutes=minutes,
            reason=reason,
            reference_now=reference_now or self.now(),
        )

    async def _reschedule_to_datetime(
        self,
        repo,
        task,
        new_start_at: datetime,
        reason: str,
        reference_now: datetime | None = None,
    ) -> None:
        await self.task_executor.reschedule_to_datetime(
            repo,
            task,
            new_start_at=new_start_at,
            reason=reason,
            reference_now=reference_now or self.now(),
        )

    async def _cancel_task(self, repo, task, reason: str) -> None:
        await self.task_executor.cancel_task(repo, task, reason=reason)

    async def _reschedule_to_tonight(self, repo, task, now: datetime) -> None:
        await self.task_executor.reschedule_to_tonight(repo, task, now=now)

    async def _reschedule_to_tomorrow(self, repo, task, now: datetime) -> None:
        await self.task_executor.reschedule_to_tomorrow(repo, task, now=now)

    async def _replan_multiple_tasks(self, repo, tasks, now: datetime) -> None:
        await self.task_executor.replan_multiple_tasks(repo, tasks, now=now)

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
