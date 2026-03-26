from __future__ import annotations

from datetime import datetime

from study_assistant.services.internal_events import InternalEvent


class InputHandler:
    def from_telegram_update(self, payload: dict) -> InternalEvent | None:
        if "callback_query" in payload:
            callback = payload["callback_query"]
            return InternalEvent(
                event_type="button_action",
                telegram_user_id=callback["from"]["id"],
                chat_id=callback["message"]["chat"]["id"],
                callback_data=callback["data"],
                callback_query_id=callback["id"],
                metadata={"raw_payload": payload},
            )

        message = payload.get("message") or payload.get("edited_message")
        if message and message.get("text"):
            return InternalEvent(
                event_type="user_message",
                telegram_user_id=message["from"]["id"],
                chat_id=message["chat"]["id"],
                display_name=message["from"].get("first_name"),
                text=message["text"],
                metadata={"raw_payload": payload},
            )

        return None

    def from_text_message(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        display_name: str | None,
        text: str,
    ) -> InternalEvent:
        return InternalEvent(
            event_type="user_message",
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            display_name=display_name,
            text=text,
        )

    def from_callback_query(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        callback_data: str,
        callback_query_id: str | None = None,
    ) -> InternalEvent:
        return InternalEvent(
            event_type="button_action",
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            callback_data=callback_data,
            callback_query_id=callback_query_id,
        )

    def from_scheduler_trigger(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        task_id: str,
        prompt_kind: str,
        occurred_at: datetime | None = None,
    ) -> InternalEvent:
        return InternalEvent(
            event_type="scheduler_event",
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            task_id=task_id,
            prompt_kind=prompt_kind,
            occurred_at=occurred_at,
        )
