from __future__ import annotations

from datetime import datetime, timedelta

from study_assistant.models.entities import PendingPromptType, StudyTask, TaskSource, TaskStatus


class CommandHandler:
    def __init__(self, *, telegram_client, response_composer, weekly_report_service):
        self.telegram_client = telegram_client
        self.response_composer = response_composer
        self.weekly_report_service = weekly_report_service

    async def handle(
        self,
        *,
        repo,
        user,
        daily_conversation,
        chat_id: int,
        command: str,
        now,
    ) -> bool:
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
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=chat_id,
                text=f"빠른 테스트예요. 지금 '{task.title}' 시작했나요?",
                reply_markup=self.response_composer.checkin_keyboard(task.id),
                now=now,
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
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=chat_id,
                text=f"빠른 테스트예요. '{task.title}' 마무리됐어요?",
                reply_markup=self.response_composer.completion_keyboard(task.id),
                now=now,
            )
            return True

        if command == "/start":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=chat_id,
                text=self.response_composer.start_message(),
                now=now,
            )
            return True

        if command == "/plan":
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=chat_id,
                text=self.response_composer.plan_help_message(),
                now=now,
            )
            return True

        if command in {"/id", "/me"}:
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=chat_id,
                text=(
                    f"telegram_user_id: {user.telegram_user_id}\n"
                    f"telegram_chat_id: {user.telegram_chat_id}"
                ),
                now=now,
            )
            return True

        if command in {"/weeklyreport", "/report"}:
            report = await self.weekly_report_service.build_weekly_report(
                repo,
                user=user,
                reference_date=now.date(),
            )
            await self._send_and_log(
                repo,
                daily_conversation=daily_conversation,
                chat_id=chat_id,
                text=self.response_composer.weekly_report(report),
                now=now,
            )
            return True

        return False

    async def _send_and_log(self, repo, *, daily_conversation, chat_id: int, text: str, now, reply_markup=None) -> None:
        await self.telegram_client.send_message(chat_id, text, reply_markup=reply_markup)
        await repo.append_conversation_turn(
            daily_conversation,
            role="assistant",
            text=text,
            occurred_at=now,
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
