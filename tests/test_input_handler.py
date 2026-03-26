from datetime import datetime
from zoneinfo import ZoneInfo

from study_assistant.services.input_handler import InputHandler


def test_input_handler_creates_user_message_event_from_update():
    handler = InputHandler()
    payload = {
        "message": {
            "text": "오늘 6시로 옮겨줘",
            "from": {"id": 123, "first_name": "LG"},
            "chat": {"id": 456},
        }
    }

    event = handler.from_telegram_update(payload)

    assert event is not None
    assert event.event_type == "user_message"
    assert event.telegram_user_id == 123
    assert event.chat_id == 456
    assert event.display_name == "LG"
    assert event.text == "오늘 6시로 옮겨줘"


def test_input_handler_creates_button_action_event_from_update():
    handler = InputHandler()
    payload = {
        "callback_query": {
            "id": "cb-1",
            "data": "task:abc:done",
            "from": {"id": 123},
            "message": {"chat": {"id": 456}},
        }
    }

    event = handler.from_telegram_update(payload)

    assert event is not None
    assert event.event_type == "button_action"
    assert event.callback_query_id == "cb-1"
    assert event.callback_data == "task:abc:done"
    assert event.telegram_user_id == 123
    assert event.chat_id == 456


def test_input_handler_creates_scheduler_event():
    handler = InputHandler()
    occurred_at = datetime(2026, 3, 27, 19, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    event = handler.from_scheduler_trigger(
        telegram_user_id=123,
        chat_id=456,
        task_id="task-1",
        prompt_kind="checkin",
        occurred_at=occurred_at,
    )

    assert event.event_type == "scheduler_event"
    assert event.telegram_user_id == 123
    assert event.chat_id == 456
    assert event.task_id == "task-1"
    assert event.prompt_kind == "checkin"
    assert event.occurred_at == occurred_at
