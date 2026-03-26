from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


InternalEventType = Literal["user_message", "button_action", "scheduler_event"]


@dataclass(slots=True)
class InternalEvent:
    event_type: InternalEventType
    telegram_user_id: int
    chat_id: int | None
    task_id: str | None = None
    prompt_kind: str | None = None
    display_name: str | None = None
    text: str | None = None
    callback_data: str | None = None
    callback_query_id: str | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
