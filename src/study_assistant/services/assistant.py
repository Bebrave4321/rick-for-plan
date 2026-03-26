from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from study_assistant.core.config import Settings
from study_assistant.models.entities import (
    ChangeType,
    FeedbackType,
    PendingPromptType,
    ResponseSource,
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
from study_assistant.services.telegram import inline_keyboard


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
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.planning_service = planning_service
        self.message_interpreter = message_interpreter
        self.telegram_client = telegram_client
        self.openai_client = openai_client

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
            self._render_weekly_plan_message(plan_result.draft),
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

    async def run_due_scan(self) -> dict:
        now = self.now()
        sent_count = 0
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
                    await self.telegram_client.send_message(
                        user.telegram_chat_id,
                        f"10분 뒤 '{task.title}' 시작이에요. 준비해볼까요?",
                    )
                    task.prep_reminder_sent_at = now
                    sent_count += 1

                if task.checkin_sent_at is None and now >= task.start_at:
                    await self.telegram_client.send_message(
                        user.telegram_chat_id,
                        f"지금 '{task.title}' 시작했나요?",
                        reply_markup=self._build_checkin_keyboard(task.id),
                    )
                    task.checkin_sent_at = now
                    task.latest_prompt_sent_at = now
                    task.pending_prompt_type = PendingPromptType.CHECKIN
                    task.status = TaskStatus.CHECKIN_PENDING
                    sent_count += 1

                if (
                    task.checkin_sent_at is not None
                    and task.recheck_sent_at is None
                    and task.status == TaskStatus.CHECKIN_PENDING
                    and now >= task.checkin_sent_at + timedelta(minutes=10)
                ):
                    await self.telegram_client.send_message(
                        user.telegram_chat_id,
                        f"'{task.title}' 확인 응답이 없어서 다시 물어요. 지금 시작 가능할까요?",
                        reply_markup=self._build_checkin_keyboard(task.id),
                    )
                    task.recheck_sent_at = now
                    task.latest_prompt_sent_at = now
                    task.pending_prompt_type = PendingPromptType.RECHECK
                    sent_count += 1

                if duration >= timedelta(hours=1) and task.status == TaskStatus.IN_PROGRESS and self._needs_progress_check(task, now):
                    await self.telegram_client.send_message(
                        user.telegram_chat_id,
                        f"'{task.title}' 지금까지 괜찮게 진행 중인가요?",
                        reply_markup=self._build_progress_keyboard(task.id),
                    )
                    task.last_progress_check_at = now
                    task.latest_prompt_sent_at = now
                    task.pending_prompt_type = PendingPromptType.PROGRESS
                    sent_count += 1

                if task.completion_prompt_sent_at is None and now >= task.end_at:
                    await self.telegram_client.send_message(
                        user.telegram_chat_id,
                        f"'{task.title}' 마무리됐나요?",
                        reply_markup=self._build_completion_keyboard(task.id),
                    )
                    task.completion_prompt_sent_at = now
                    task.latest_prompt_sent_at = now
                    task.pending_prompt_type = PendingPromptType.COMPLETION
                    sent_count += 1

            await session.commit()

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
                    self._render_daily_summary(yesterday_tasks, today_tasks),
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
        if "callback_query" in payload:
            callback = payload["callback_query"]
            await self.process_callback_query(
                telegram_user_id=callback["from"]["id"],
                chat_id=callback["message"]["chat"]["id"],
                callback_data=callback["data"],
            )
            await self.telegram_client.answer_callback_query(callback["id"])
            return {"ok": True}

        message = payload.get("message") or payload.get("edited_message")
        if message and message.get("text"):
            await self.process_text_message(
                telegram_user_id=message["from"]["id"],
                chat_id=message["chat"]["id"],
                display_name=message["from"].get("first_name"),
                text=message["text"],
            )
        return {"ok": True}

    async def process_text_message(self, telegram_user_id: int, chat_id: int, display_name: str | None, text: str) -> None:
        now = self.now()
        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_or_create_user(
                CreateUserRequest(
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=chat_id,
                    display_name=display_name,
                ),
                timezone=self.settings.default_timezone,
            )
            daily_conversation = await repo.get_or_create_daily_conversation(user.id, now.date())
            active_task = await repo.get_active_message_task(user.id, now)
            today_tasks = await repo.list_tasks_for_day(user.id, now.date(), self.settings.timezone)
            if active_task is not None:
                self._localize_task_datetimes(active_task)
            for task in today_tasks:
                self._localize_task_datetimes(task)

            if text.strip() == "/start":
                await session.commit()
                await self.telegram_client.send_message(chat_id, self._render_start_message())
                return

            interpreted = await self.message_interpreter.interpret(
                text=text,
                user=user,
                daily_conversation=daily_conversation,
                active_task=active_task,
                today_tasks=today_tasks,
                now=now,
            )

            await self._apply_interpreted_message(repo, user, active_task, today_tasks, interpreted, text, now)
            await session.commit()

    async def process_callback_query(self, telegram_user_id: int, chat_id: int, callback_data: str) -> None:
        try:
            _, task_id, action = callback_data.split(":", 2)
        except ValueError:
            await self.telegram_client.send_message(chat_id, "버튼 정보를 이해하지 못했어요.")
            return

        async with self.session_factory() as session:
            repo = AssistantRepository(session)
            user = await repo.get_user_by_telegram_user_id(telegram_user_id)
            task = await repo.get_task(task_id)
            if user is None or task is None:
                await self.telegram_client.send_message(chat_id, "대상 일정을 찾지 못했어요.")
                return

            now = self.now()
            self._localize_task_datetimes(task)
            if action == "start":
                task.status = TaskStatus.IN_PROGRESS
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="started",
                    interpreted_kind="mark_started",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.IN_PROGRESS,
                )
                await self.telegram_client.send_message(chat_id, f"좋아요. '{task.title}' 시작으로 기록할게요.")
            elif action == "delay10":
                await self._shift_task(repo, task, minutes=10, reason="User requested 10 minute delay.")
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="delay10",
                    interpreted_kind="postpone_10",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.RESCHEDULED,
                )
                await self.telegram_client.send_message(chat_id, f"'{task.title}' 일정을 10분 뒤로 옮겼어요.")
            elif action == "skip":
                task.status = TaskStatus.MISSED
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="skip",
                    interpreted_kind="mark_missed",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.MISSED,
                )
                await self.telegram_client.send_message(
                    chat_id,
                    f"괜찮아요. '{task.title}'은 못 한 것으로 기록했어요. 다시 잡을까요?",
                    reply_markup=self._build_reschedule_keyboard(task.id),
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
                await self.telegram_client.send_message(chat_id, "좋아요. 그대로 이어가면 돼요.")
            elif action == "progress_help":
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="progress_help",
                    interpreted_kind="progress_help",
                    interpreted_payload={"action": action},
                )
                await self.telegram_client.send_message(chat_id, "괜찮아요. 끝난 뒤 남은 분량만 알려주면 다시 정리할게요.")
            elif action == "done":
                await self._mark_task_completed(repo, task, ResponseSource.BUTTON, "done")
                await self.telegram_client.send_message(chat_id, f"좋아요. '{task.title}' 완료로 기록했어요.")
            elif action == "partial":
                task.status = TaskStatus.PARTIAL
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="partial",
                    interpreted_kind="mark_partial",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.PARTIAL,
                    feedback_type=FeedbackType.DID_NOT_FINISH,
                )
                await self.telegram_client.send_message(
                    chat_id,
                    f"'{task.title}'은 일부 완료로 기록했어요. 남은 분량을 다시 잡을까요?",
                    reply_markup=self._build_reschedule_keyboard(task.id),
                )
            elif action == "missed":
                task.status = TaskStatus.MISSED
                task.pending_prompt_type = None
                await repo.record_task_response(
                    task,
                    source=ResponseSource.BUTTON,
                    raw_text="missed",
                    interpreted_kind="mark_missed",
                    interpreted_payload={"action": action},
                    result_status=TaskStatus.MISSED,
                )
                await self.telegram_client.send_message(
                    chat_id,
                    f"알겠어요. '{task.title}'은 못 한 일정으로 기록했어요. 다시 잡을까요?",
                    reply_markup=self._build_reschedule_keyboard(task.id),
                )
            elif action == "reschedTonight":
                await self._reschedule_to_tonight(repo, task, now)
                await self.telegram_client.send_message(chat_id, f"'{task.title}'을 오늘 저녁으로 옮겼어요.")
            elif action == "reschedTomorrow":
                await self._reschedule_to_tomorrow(repo, task, now)
                await self.telegram_client.send_message(chat_id, f"'{task.title}'을 내일 저녁으로 옮겼어요.")
            elif action == "cancel":
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
                    reason="User cancelled the task.",
                )
                await self.telegram_client.send_message(chat_id, f"'{task.title}' 일정은 취소로 처리했어요.")
            else:
                await self.telegram_client.send_message(chat_id, "아직 지원하지 않는 버튼이에요.")

            await session.commit()

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

        if interpreted.kind in {"mark_completed", "mark_partial", "mark_missed", "postpone_10", "postpone_custom", "cancel_task"} and active_task is None:
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                "지금 연결할 일정이 없어요. 일정 제목을 같이 보내주거나 오늘 일정을 먼저 확인해볼게요.",
            )
            return

        if interpreted.kind == "mark_completed":
            await self._mark_task_completed(repo, active_task, ResponseSource.FREE_TEXT, raw_text)
            await self.telegram_client.send_message(user.telegram_chat_id, f"좋아요. '{active_task.title}' 완료로 기록했어요.")
            return

        if interpreted.kind == "mark_partial":
            active_task.status = TaskStatus.PARTIAL
            active_task.pending_prompt_type = None
            await repo.record_task_response(
                active_task,
                source=ResponseSource.FREE_TEXT,
                raw_text=raw_text,
                interpreted_kind=interpreted.kind,
                interpreted_payload=interpreted.model_dump(mode="json"),
                result_status=TaskStatus.PARTIAL,
                feedback_type=FeedbackType.DID_NOT_FINISH,
            )
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                f"'{active_task.title}'은 일부 완료로 기록했어요. 다시 잡을까요?",
                reply_markup=self._build_reschedule_keyboard(active_task.id),
            )
            return

        if interpreted.kind == "mark_missed":
            if interpreted.target_scope == "multiple":
                pending_tasks = [
                    task for task in today_tasks
                    if task.status not in FINAL_TASK_STATUSES and task.end_at <= now
                ]
                await self._replan_multiple_tasks(repo, pending_tasks, now)
                await self.telegram_client.send_message(
                    user.telegram_chat_id,
                    "놓친 일정들을 현재 시점 기준으로 다시 이어 붙였어요. 오늘 남은 시간에 맞춰 재정리했습니다.",
                )
                return

            active_task.status = TaskStatus.MISSED
            active_task.pending_prompt_type = None
            await repo.record_task_response(
                active_task,
                source=ResponseSource.FREE_TEXT,
                raw_text=raw_text,
                interpreted_kind=interpreted.kind,
                interpreted_payload=interpreted.model_dump(mode="json"),
                result_status=TaskStatus.MISSED,
            )
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                f"알겠어요. '{active_task.title}'은 못 한 일정으로 기록했어요. 다시 잡을까요?",
                reply_markup=self._build_reschedule_keyboard(active_task.id),
            )
            return

        if interpreted.kind in {"postpone_10", "postpone_custom"}:
            minutes = interpreted.reschedule_minutes or 10
            await self._shift_task(repo, active_task, minutes=minutes, reason=f"User postponed by {minutes} minutes.")
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
            return

        if interpreted.kind == "cancel_task":
            old_start = active_task.start_at
            old_end = active_task.end_at
            active_task.status = TaskStatus.CANCELLED
            active_task.pending_prompt_type = None
            await repo.add_change_log(
                active_task,
                old_start_at=old_start,
                old_end_at=old_end,
                new_start_at=None,
                new_end_at=None,
                change_type=ChangeType.CANCELLED,
                reason="User cancelled through text message.",
            )
            await repo.record_task_response(
                active_task,
                source=ResponseSource.FREE_TEXT,
                raw_text=raw_text,
                interpreted_kind=interpreted.kind,
                interpreted_payload=interpreted.model_dump(mode="json"),
                result_status=TaskStatus.CANCELLED,
            )
            await self.telegram_client.send_message(user.telegram_chat_id, f"'{active_task.title}' 일정은 취소로 처리했어요.")
            return

        if interpreted.kind == "replan_today":
            unfinished = [
                task for task in today_tasks
                if task.status not in FINAL_TASK_STATUSES and task.end_at >= now - timedelta(hours=2)
            ]
            await self._replan_multiple_tasks(repo, unfinished, now)
            await self.telegram_client.send_message(
                user.telegram_chat_id,
                "오늘 남은 일정을 다시 정리했어요. 너무 빡빡하지 않게 뒤로 재배치했습니다.",
            )
            return

        await self.telegram_client.send_message(
            user.telegram_chat_id,
            "메시지 뜻을 확실히 못 잡았어요. '완료했어', '10분 미뤄줘', '오늘은 쉬고 싶어'처럼 보내주면 바로 반영할게요.",
        )

    async def _mark_task_completed(self, repo, task, source, raw_text: str) -> None:
        task.status = TaskStatus.COMPLETED
        task.completed_at = self.now()
        task.pending_prompt_type = None
        await repo.record_task_response(
            task,
            source=source,
            raw_text=raw_text,
            interpreted_kind="mark_completed",
            interpreted_payload={"raw_text": raw_text},
            result_status=TaskStatus.COMPLETED,
        )

    async def _shift_task(self, repo, task, minutes: int, reason: str) -> None:
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

    async def _reschedule_to_tonight(self, repo, task, now: datetime) -> None:
        anchor = self._today_evening_anchor(now)
        duration = task.end_at - task.start_at
        old_start = task.start_at
        old_end = task.end_at
        task.start_at = anchor
        task.end_at = anchor + duration
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
            reason="Rescheduled to tonight.",
        )

    async def _reschedule_to_tomorrow(self, repo, task, now: datetime) -> None:
        anchor = datetime.combine(now.date() + timedelta(days=1), time(19, 0), tzinfo=self.settings.timezone)
        duration = task.end_at - task.start_at
        old_start = task.start_at
        old_end = task.end_at
        task.start_at = anchor
        task.end_at = anchor + duration
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
            reason="Rescheduled to tomorrow evening.",
        )

    async def _replan_multiple_tasks(self, repo, tasks, now: datetime) -> None:
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
            return datetime.combine(now.date(), time(19, 0), tzinfo=self.settings.timezone)
        if proposed.hour >= 22:
            return datetime.combine(now.date() + timedelta(days=1), time(19, 0), tzinfo=self.settings.timezone)
        if proposed.minute == 0:
            return proposed.replace(second=0, microsecond=0)
        if proposed.minute <= 30:
            return proposed.replace(minute=30, second=0, microsecond=0)
        return (proposed + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

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

    def _render_start_message(self) -> str:
        return (
            "공부 일정 비서예요.\n"
            "- /plan 으로 주간 계획 안내를 볼 수 있어요.\n"
            "- 시작 전 알림, 시작 확인, 종료 확인, 재배치를 도와드릴게요."
        )

    def _render_weekly_plan_message(self, draft) -> str:
        lines = ["이번 주 계획 초안을 만들었어요.", draft.summary, ""]
        for session in draft.sessions[:10]:
            lines.append(f"- {session.start_at:%m/%d %H:%M} {session.title}")
        if draft.overflow_notes:
            lines.append("")
            lines.append("추가로 시간이 더 필요한 항목:")
            lines.extend(f"- {note}" for note in draft.overflow_notes)
        return "\n".join(lines)

    def _render_daily_summary(self, yesterday_tasks, today_tasks) -> str:
        completed = [task.title for task in yesterday_tasks if task.status == TaskStatus.COMPLETED]
        unfinished = [
            task.title for task in yesterday_tasks
            if task.status in {TaskStatus.MISSED, TaskStatus.PARTIAL, TaskStatus.RESCHEDULED}
        ]
        lines = []
        if completed:
            lines.append(f"어제 완료: {', '.join(completed[:3])}")
        if unfinished:
            lines.append(f"어제 미완료/변경: {', '.join(unfinished[:3])}")
        if today_tasks:
            schedule = ", ".join(f"{task.start_at:%H:%M} {task.title}" for task in today_tasks[:5])
            lines.append(f"오늘 일정: {schedule}")
        if not lines:
            lines.append("어제 기록된 일정은 없었어요. 오늘 일정부터 같이 정리해볼까요?")
        return "\n".join(lines)

    def _build_checkin_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("시작했어요", f"task:{task_id}:start"), ("10분만 미룰게요", f"task:{task_id}:delay10")],
                [("이번 건 못 해요", f"task:{task_id}:skip")],
            ]
        )

    def _build_progress_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("잘 진행 중", f"task:{task_id}:progress_ok"), ("조금 밀려요", f"task:{task_id}:progress_help")],
            ]
        )

    def _build_completion_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("완료했어요", f"task:{task_id}:done"), ("일부만 했어요", f"task:{task_id}:partial")],
                [("못 했어요", f"task:{task_id}:missed")],
            ]
        )

    def _build_reschedule_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("오늘 저녁으로", f"task:{task_id}:reschedTonight"), ("내일 저녁으로", f"task:{task_id}:reschedTomorrow")],
                [("취소할게요", f"task:{task_id}:cancel")],
            ]
        )
