from __future__ import annotations


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
        chat_id: int,
        command: str,
        now,
    ) -> bool:
        if command == "/start":
            await self.telegram_client.send_message(chat_id, self.response_composer.start_message())
            return True

        if command == "/plan":
            await self.telegram_client.send_message(chat_id, self.response_composer.plan_help_message())
            return True

        if command in {"/id", "/me"}:
            await self.telegram_client.send_message(
                chat_id,
                (
                    f"telegram_user_id: {user.telegram_user_id}\n"
                    f"telegram_chat_id: {user.telegram_chat_id}"
                ),
            )
            return True

        if command in {"/weeklyreport", "/report"}:
            report = await self.weekly_report_service.build_weekly_report(
                repo,
                user=user,
                reference_date=now.date(),
            )
            await self.telegram_client.send_message(
                chat_id,
                self.response_composer.weekly_report(report),
            )
            return True

        return False
