from __future__ import annotations

from datetime import datetime

from study_assistant.models.entities import FeedbackType, ResponseSource, TaskStatus


class ButtonActionHandler:
    def __init__(
        self,
        *,
        telegram_client,
        response_composer,
        task_executor,
        text_action_handler,
        decision_engine,
    ):
        self.telegram_client = telegram_client
        self.response_composer = response_composer
        self.task_executor = task_executor
        self.text_action_handler = text_action_handler
        self.decision_engine = decision_engine

    def parse_callback_data(self, callback_data: str | None) -> tuple[str, str] | None:
        try:
            _, task_id, action = (callback_data or "").split(":", 2)
        except ValueError:
            return None
        return task_id, action

    async def handle(
        self,
        *,
        repo,
        user,
        task,
        action: str,
        chat_id: int | None,
        now: datetime,
    ) -> None:
        target_chat_id = chat_id or user.telegram_chat_id

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
            await self.telegram_client.send_message(target_chat_id, f"좋아요. '{task.title}' 시작으로 기록할게요.")
            return

        if action == "delay10":
            await self.text_action_handler.shift_task(
                repo,
                task,
                minutes=10,
                reason="User requested 10 minute delay.",
                reference_now=now,
            )
            await repo.record_task_response(
                task,
                source=ResponseSource.BUTTON,
                raw_text="delay10",
                interpreted_kind="postpone_10",
                interpreted_payload={"action": action},
                result_status=TaskStatus.RESCHEDULED,
            )
            await self.telegram_client.send_message(target_chat_id, f"'{task.title}' 일정을 10분 뒤로 옮겼어요.")
            return

        if action == "skip":
            await self.text_action_handler.mark_task_for_reschedule(
                repo,
                task,
                source=ResponseSource.BUTTON,
                raw_text="skip",
                interpreted_kind="mark_missed",
                interpreted_payload={"action": action},
                result_status=TaskStatus.MISSED,
                feedback_type=None,
                lead_text=f"괜찮아요. '{task.title}'은 못 한 것으로 기록했어요. 다시 잡을까요?",
                chat_id=target_chat_id,
            )
            return

        if action == "progress_ok":
            task.pending_prompt_type = None
            await repo.record_task_response(
                task,
                source=ResponseSource.BUTTON,
                raw_text="progress_ok",
                interpreted_kind="progress_ok",
                interpreted_payload={"action": action},
            )
            await self.telegram_client.send_message(target_chat_id, "좋아요. 그대로 이어가면 돼요.")
            return

        if action == "progress_help":
            task.pending_prompt_type = None
            await repo.record_task_response(
                task,
                source=ResponseSource.BUTTON,
                raw_text="progress_help",
                interpreted_kind="progress_help",
                interpreted_payload={"action": action},
            )
            await self.telegram_client.send_message(target_chat_id, "괜찮아요. 끝난 뒤 남은 분량만 알려주면 다시 정리할게요.")
            return

        if action == "done":
            await self.text_action_handler.mark_task_completed(
                repo,
                task,
                source=ResponseSource.BUTTON,
                raw_text="done",
                completed_at=now,
            )
            await self.telegram_client.send_message(target_chat_id, f"좋아요. '{task.title}' 완료로 기록했어요.")
            return

        if action == "partial":
            await self.text_action_handler.mark_task_for_reschedule(
                repo,
                task,
                source=ResponseSource.BUTTON,
                raw_text="partial",
                interpreted_kind="mark_partial",
                interpreted_payload={"action": action},
                result_status=TaskStatus.PARTIAL,
                feedback_type=FeedbackType.DID_NOT_FINISH,
                lead_text=f"'{task.title}'은 일부 완료로 기록했어요. 남은 분량을 다시 잡을까요?",
                chat_id=target_chat_id,
            )
            return

        if action == "missed":
            await self.text_action_handler.mark_task_for_reschedule(
                repo,
                task,
                source=ResponseSource.BUTTON,
                raw_text="missed",
                interpreted_kind="mark_missed",
                interpreted_payload={"action": action},
                result_status=TaskStatus.MISSED,
                feedback_type=None,
                lead_text=f"알겠어요. '{task.title}'은 못 한 일정으로 기록했어요. 다시 잡을까요?",
                chat_id=target_chat_id,
            )
            return

        if action == "reschedTonight":
            await self.text_action_handler.reschedule_to_tonight(repo, task, now=now)
            await self.telegram_client.send_message(
                target_chat_id,
                self.response_composer.reschedule_confirmation(task, "오늘 저녁"),
            )
            return

        if action == "reschedTomorrow":
            await self.text_action_handler.reschedule_to_tomorrow(repo, task, now=now)
            await self.telegram_client.send_message(
                target_chat_id,
                self.response_composer.reschedule_confirmation(task, "내일 저녁"),
            )
            return

        if action == "suggest":
            suggestions = self.decision_engine.build_reschedule_suggestions(now)
            await self.telegram_client.send_message(
                target_chat_id,
                self.decision_engine.suggestion_text(suggestions, task.end_at - task.start_at),
            )
            return

        if action == "cancel":
            await self.text_action_handler.cancel_task(repo, task, reason="User cancelled the task.")
            await self.telegram_client.send_message(target_chat_id, f"'{task.title}' 일정은 취소로 처리했어요.")
            return

        await self.telegram_client.send_message(target_chat_id, "아직 지원하지 않는 버튼이에요.")
