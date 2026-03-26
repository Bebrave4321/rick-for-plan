from __future__ import annotations

import logging
from typing import Any

import httpx

from study_assistant.core.config import Settings

logger = logging.getLogger(__name__)


class TelegramBotClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http_client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{settings.telegram_bot_token}",
            timeout=10.0,
        ) if settings.telegram_bot_token else None

    async def close(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()

    async def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        if self.http_client is None:
            logger.info("Telegram disabled. chat_id=%s message=%s", chat_id, text)
            return

        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = await self.http_client.post("/sendMessage", json=payload)
        response.raise_for_status()

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        if self.http_client is None:
            return
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        response = await self.http_client.post("/answerCallbackQuery", json=payload)
        response.raise_for_status()

    async def set_webhook(self) -> None:
        if self.http_client is None:
            return
        payload: dict[str, Any] = {"url": self.settings.telegram_webhook_url}
        if self.settings.telegram_webhook_secret:
            payload["secret_token"] = self.settings.telegram_webhook_secret
        response = await self.http_client.post("/setWebhook", json=payload)
        response.raise_for_status()


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": callback_data} for text, callback_data in row]
            for row in rows
        ]
    }
