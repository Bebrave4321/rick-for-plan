from __future__ import annotations

from datetime import datetime, timedelta

from study_assistant.models.entities import FeedbackType, PendingPromptType, ResponseSource, TaskStatus
from study_assistant.repositories.assistant_repository import FINAL_TASK_STATUSES


class TextActionHandler:
    def __init__(self, *, telegram_client, response_composer, task_executor, decision_engine):
        self.telegram_client = telegram_client
        self.response_composer = response_composer
        self.task_executor = task_executor
        self.decision_engine = decision_engine

    def requires_active_task(self, kind: str) -> bool:
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

    async def apply_interpreted_message(
        self,
        *,
        repo,
        user,
        active_task,
        today_tasks,
        interpreted,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
    ) -> None:
        if interpreted.kind == "weekly_plan_request":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text=(
                    "주간 계획 입력은 아직 API 경로가 가장 안정적이에요. "
                    "README의 `/api/plans/weekly` 예시를 참고해서 비가용 시간과 목표를 정리해 보내주세요."
                ),
                now=now,
            )
            return

        if interpreted.kind == "weekly_plan_input":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text="주간 계획 입력으로 보이지만, 현재 구현에서는 `/api/plans/weekly`가 가장 안정적이에요.",
                now=now,
            )
            return

        if self.requires_active_task(interpreted.kind) and active_task is None and interpreted.target_scope != "multiple":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text="지금 연결된 일정이 없어요. 일정 이름을 함께 보내주거나 오늘 일정을 먼저 확인해볼게요.",
                now=now,
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
                daily_conversation=daily_conversation,
            )
            return

        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text="메시지를 확실히 이해하지 못했어요. 예를 들면 '완료했어', '10분 미뤄줘', '오늘은 못 해'처럼 보내주면 바로 반영할게요.",
            now=now,
        )

    async def handle_reschedule_followup(
        self,
        *,
        repo,
        user,
        task,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
    ) -> bool:
        decision = self.decision_engine.decide_reschedule(raw_text, now)

        if decision.decision_type == "clarify":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text=decision.clarification_message or self.response_composer.freeform_reschedule_help(),
                now=now,
            )
            return True

        if decision.decision_type == "suggest":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text=self.decision_engine.suggestion_text(decision.suggestions, task.end_at - task.start_at),
                now=now,
            )
            return True

        if decision.decision_type == "cancel":
            await self.cancel_task(repo, task, reason="User cancelled during reschedule follow-up.")
            await repo.record_task_response(
                task,
                source=ResponseSource.FREE_TEXT,
                raw_text=raw_text,
                interpreted_kind="cancel_task",
                interpreted_payload={"decision_type": decision.decision_type},
                result_status=TaskStatus.CANCELLED,
            )
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text=f"'{task.title}' 일정은 취소로 처리했어요.",
                now=now,
            )
            return True

        if decision.decision_type == "reschedule" and decision.parsed_time is not None:
            await self.reschedule_to_datetime(
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
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text=self.response_composer.precise_reschedule_confirmation(task),
                now=now,
            )
            return True

        return False

    async def mark_task_completed(self, repo, task, *, source, raw_text: str, completed_at: datetime) -> None:
        await self.task_executor.mark_task_completed(repo, task, completed_at=completed_at)
        await repo.record_task_response(
            task,
            source=source,
            raw_text=raw_text,
            interpreted_kind="mark_completed",
            interpreted_payload={"raw_text": raw_text},
            result_status=TaskStatus.COMPLETED,
        )

    async def mark_task_for_reschedule(
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
        daily_conversation=None,
        now: datetime | None = None,
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
        send_time = now or datetime.now(task.start_at.tzinfo)
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=chat_id,
            text=self.response_composer.reschedule_prompt(lead_text),
            reply_markup=self.response_composer.reschedule_keyboard(task.id),
            now=send_time,
        )

    async def shift_task(
        self,
        repo,
        task,
        *,
        minutes: int,
        reason: str,
        reference_now: datetime,
    ) -> None:
        await self.task_executor.shift_task(
            repo,
            task,
            minutes=minutes,
            reason=reason,
            reference_now=reference_now,
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
        await self.task_executor.reschedule_to_datetime(
            repo,
            task,
            new_start_at=new_start_at,
            reason=reason,
            reference_now=reference_now,
        )

    async def cancel_task(self, repo, task, *, reason: str) -> None:
        await self.task_executor.cancel_task(repo, task, reason=reason)

    async def reschedule_to_tonight(self, repo, task, *, now: datetime) -> None:
        await self.task_executor.reschedule_to_tonight(repo, task, now=now)

    async def reschedule_to_tomorrow(self, repo, task, *, now: datetime) -> None:
        await self.task_executor.reschedule_to_tomorrow(repo, task, now=now)

    async def replan_multiple_tasks(self, repo, tasks, *, now: datetime) -> None:
        await self.task_executor.replan_multiple_tasks(repo, tasks, now=now)

    async def _handle_completed_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
        await self.mark_task_completed(
            repo,
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=kwargs["raw_text"],
            completed_at=now,
        )
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text=f"좋아요. '{active_task.title}' 완료로 기록했어요.",
            now=now,
        )

    async def _handle_partial_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        interpreted,
        raw_text: str,
        daily_conversation=None,
        now: datetime,
        **kwargs,
    ) -> None:
        await self.mark_task_for_reschedule(
            repo,
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.PARTIAL,
            feedback_type=FeedbackType.DID_NOT_FINISH,
            lead_text=f"'{active_task.title}'은 일부 완료로 기록했어요. 남은 분량을 다시 잡을까요?",
            chat_id=user.telegram_chat_id,
            daily_conversation=daily_conversation,
            now=now,
        )

    async def _handle_missed_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        today_tasks,
        interpreted,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
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
                    task
                    for task in today_tasks
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

            await self.replan_multiple_tasks(repo, pending_tasks, now=now)
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=user.telegram_chat_id,
                text=self.response_composer.multiple_missed_replan_summary(pending_tasks),
                now=now,
            )
            return

        await self.mark_task_for_reschedule(
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
            daily_conversation=daily_conversation,
            now=now,
        )

    async def _handle_tonight_reschedule_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        interpreted,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
        await self.reschedule_to_tonight(repo, active_task, now=now)
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.RESCHEDULED,
        )
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text=self.response_composer.reschedule_confirmation(active_task, "오늘 저녁"),
            now=now,
        )

    async def _handle_tomorrow_reschedule_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        interpreted,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
        await self.reschedule_to_tomorrow(repo, active_task, now=now)
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.RESCHEDULED,
        )
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text=self.response_composer.reschedule_confirmation(active_task, "내일 저녁"),
            now=now,
        )

    async def _handle_postpone_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        interpreted,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
        minutes = interpreted.reschedule_minutes or 10
        await self.shift_task(
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
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text=f"좋아요. '{active_task.title}' 일정을 {minutes}분 뒤로 옮겼어요.",
            now=now,
        )

    async def _handle_cancel_text_action(
        self,
        *,
        repo,
        user,
        active_task,
        interpreted,
        raw_text: str,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
        await self.cancel_task(repo, active_task, reason="User cancelled through text message.")
        await repo.record_task_response(
            active_task,
            source=ResponseSource.FREE_TEXT,
            raw_text=raw_text,
            interpreted_kind=interpreted.kind,
            interpreted_payload=interpreted.model_dump(mode="json"),
            result_status=TaskStatus.CANCELLED,
        )
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text=f"'{active_task.title}' 일정은 취소로 처리했어요.",
            now=now,
        )

    async def _handle_replan_today_text_action(
        self,
        *,
        repo,
        user,
        today_tasks,
        now: datetime,
        daily_conversation=None,
        **kwargs,
    ) -> None:
        unfinished = [
            task
            for task in today_tasks
            if task.status not in FINAL_TASK_STATUSES and task.end_at >= now - timedelta(hours=2)
        ]
        await self.replan_multiple_tasks(repo, unfinished, now=now)
        await self._send_and_log(
            repo,
            daily_conversation=daily_conversation,
            chat_id=user.telegram_chat_id,
            text="오늘 남은 일정을 다시 정리했어요. 너무 빡세지 않게 새로 배치해둘게요.",
            now=now,
        )

    async def _send_and_log(self, repo, *, daily_conversation, chat_id: int, text: str, now, reply_markup=None) -> None:
        await self.telegram_client.send_message(chat_id, text, reply_markup=reply_markup)
        await repo.append_conversation_turn(
            daily_conversation,
            role="assistant",
            text=text,
            occurred_at=now,
        )
