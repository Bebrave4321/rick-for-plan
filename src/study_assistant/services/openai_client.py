from __future__ import annotations

import json
import logging
from datetime import date

from openai import AsyncOpenAI

from study_assistant.core.config import Settings
from study_assistant.schemas.contracts import InterpretedMessage, WeeklyPlanDraft, WeeklyPlanningRequest

logger = logging.getLogger(__name__)


class OpenAIAssistantClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def close(self) -> None:
        if self.client is not None and hasattr(self.client, "close"):
            await self.client.close()

    async def ensure_conversation_id(self, existing_conversation_id: str | None) -> str | None:
        if not self.client:
            return existing_conversation_id
        if existing_conversation_id:
            return existing_conversation_id
        try:
            conversation = await self.client.conversations.create()
        except Exception:  # noqa: BLE001
            logger.exception("OpenAI conversation creation failed")
            return existing_conversation_id
        return self._safe_lookup(conversation, "id")

    async def generate_weekly_plan(self, request: WeeklyPlanningRequest, user, daily_conversation) -> WeeklyPlanDraft | None:
        if not self.client:
            return None

        conversation_id = await self.ensure_conversation_id(daily_conversation.openai_conversation_id)
        daily_conversation.openai_conversation_id = conversation_id

        prompt = {
            "user_timezone": user.timezone,
            "week_start_date": request.week_start_date.isoformat(),
            "unavailable_blocks": [item.model_dump(mode="json") for item in request.unavailable_blocks],
            "goals": [item.model_dump(mode="json") for item in request.goals],
            "deadlines": [item.model_dump(mode="json") for item in request.deadlines],
            "busy_days": [item.model_dump(mode="json") for item in request.busy_days],
            "default_study_window": {
                "start": user.default_study_window_start.isoformat(),
                "end": user.default_study_window_end.isoformat(),
            },
        }

        try:
            response = await self.client.responses.create(
                model=self.settings.openai_model,
                conversation=conversation_id,
                input=[
                    {
                        "role": "developer",
                        "content": (
                            "You are a study schedule planner. Return a realistic weekly plan. "
                            "Respect unavailable blocks, distribute work across the week, and avoid impossible schedules."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ],
                tools=[self._weekly_plan_tool()],
            )
        except Exception:  # noqa: BLE001
            logger.exception("OpenAI weekly planning failed")
            return None

        parsed = self._extract_tool_payload(response, "submit_weekly_plan")
        if parsed is None:
            return None

        daily_conversation.last_response_id = getattr(response, "id", None)
        try:
            return WeeklyPlanDraft.model_validate(parsed)
        except Exception:  # noqa: BLE001
            logger.exception("OpenAI weekly plan payload validation failed")
            return None

    async def interpret_message(self, text: str, user, daily_conversation, active_task, today_tasks) -> InterpretedMessage | None:
        if not self.client:
            return None

        conversation_id = await self.ensure_conversation_id(daily_conversation.openai_conversation_id)
        daily_conversation.openai_conversation_id = conversation_id

        prompt = {
            "current_date": date.today().isoformat(),
            "user_timezone": user.timezone,
            "message": text,
            "active_task": self._serialize_task(active_task),
            "today_tasks": [self._serialize_task(task) for task in today_tasks],
        }

        try:
            response = await self.client.responses.create(
                model=self.settings.openai_model,
                conversation=conversation_id,
                input=[
                    {
                        "role": "developer",
                        "content": (
                            "You interpret short Telegram messages for a study assistant. "
                            "Return the most actionable intent, favoring concise and practical interpretations."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ],
                tools=[self._interpret_message_tool()],
            )
        except Exception:  # noqa: BLE001
            logger.exception("OpenAI message interpretation failed")
            return None

        parsed = self._extract_tool_payload(response, "submit_interpretation")
        if parsed is None:
            return None

        daily_conversation.last_response_id = getattr(response, "id", None)
        try:
            return InterpretedMessage.model_validate(parsed)
        except Exception:  # noqa: BLE001
            logger.exception("OpenAI interpretation payload validation failed")
            return None

    def _extract_tool_payload(self, response, tool_name: str) -> dict | None:
        output = getattr(response, "output", None) or []
        for item in output:
            item_type = self._safe_lookup(item, "type")
            name = self._safe_lookup(item, "name")
            if item_type != "function_call" or name != tool_name:
                continue
            arguments = self._safe_lookup(item, "arguments")
            if isinstance(arguments, dict):
                return arguments
            if isinstance(arguments, str):
                return json.loads(arguments)

        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            try:
                return json.loads(output_text)
            except json.JSONDecodeError:
                return None
        return None

    def _serialize_task(self, task) -> dict | None:
        if task is None:
            return None
        return {
            "id": task.id,
            "title": task.title,
            "topic": task.topic,
            "start_at": task.start_at.isoformat(),
            "end_at": task.end_at.isoformat(),
            "status": task.status.value,
            "pending_prompt_type": task.pending_prompt_type.value if task.pending_prompt_type else None,
        }

    def _safe_lookup(self, value, key: str):
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    def _weekly_plan_tool(self) -> dict:
        return {
            "type": "function",
            "name": "submit_weekly_plan",
            "description": "Return the weekly study plan draft.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "sessions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "title": {"type": "string"},
                                "topic": {"type": ["string", "null"]},
                                "start_at": {"type": "string", "format": "date-time"},
                                "end_at": {"type": "string", "format": "date-time"},
                                "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                                "notes": {"type": ["string", "null"]},
                            },
                            "required": ["title", "topic", "start_at", "end_at", "importance", "notes"],
                        },
                    },
                    "overflow_notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["summary", "sessions", "overflow_notes"],
            },
        }

    def _interpret_message_tool(self) -> dict:
        return {
            "type": "function",
            "name": "submit_interpretation",
            "description": "Return the interpreted user intent for the study assistant.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "weekly_plan_request",
                            "weekly_plan_input",
                            "mark_completed",
                            "mark_partial",
                            "mark_missed",
                            "postpone_10",
                            "postpone_custom",
                            "cancel_task",
                            "replan_today",
                            "status_update",
                            "unknown",
                        ],
                    },
                    "target_scope": {
                        "type": "string",
                        "enum": ["active_task", "today", "multiple", "none"],
                    },
                    "summary": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reschedule_minutes": {"type": ["integer", "null"]},
                    "feedback_type": {
                        "type": ["string", "null"],
                        "enum": [
                            "did_not_finish",
                            "took_longer",
                            "sleepy",
                            "distracted",
                            "interrupted",
                            "finished_early",
                            "other",
                            None,
                        ],
                    },
                },
                "required": [
                    "kind",
                    "target_scope",
                    "summary",
                    "confidence",
                    "reschedule_minutes",
                    "feedback_type",
                ],
            },
        }
