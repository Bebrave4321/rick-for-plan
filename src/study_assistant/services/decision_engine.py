from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from study_assistant.services.time_parser import ParsedTimeExpression, TimeParser


DecisionType = Literal["reschedule", "clarify", "suggest", "cancel"]


@dataclass(slots=True)
class RescheduleDecision:
    decision_type: DecisionType
    parsed_time: ParsedTimeExpression | None = None
    clarification_message: str | None = None
    suggestions: list[ParsedTimeExpression] = field(default_factory=list)


class DecisionEngine:
    def __init__(self, timezone, time_parser: TimeParser | None = None):
        self.timezone = timezone
        self.time_parser = time_parser or TimeParser(timezone)

    def decide_reschedule(self, text: str, now: datetime) -> RescheduleDecision:
        normalized = text.strip().lower()

        if any(keyword in normalized for keyword in ["취소", "그만", "안 할래", "안할래", "안 할게", "안할게"]):
            return RescheduleDecision(decision_type="cancel")

        if any(keyword in normalized for keyword in ["추천", "골라", "제안"]):
            return RescheduleDecision(
                decision_type="suggest",
                suggestions=self.time_parser.build_reschedule_suggestions(now),
            )

        parsed_time = self.time_parser.parse_reschedule_time(text, now)
        if parsed_time is not None:
            if parsed_time.start_at <= now:
                return RescheduleDecision(
                    decision_type="clarify",
                    clarification_message=self._clarification_message(
                        "지나간 시간처럼 보여서 다시 한 번만 확인할게요.",
                    ),
                )
            return RescheduleDecision(decision_type="reschedule", parsed_time=parsed_time)

        return RescheduleDecision(
            decision_type="clarify",
            clarification_message=self._clarification_message(),
        )

    def suggestion_text(self, suggestions: list[ParsedTimeExpression], duration: timedelta) -> str:
        if not suggestions:
            return (
                "지금은 바로 제안할 만한 시간이 마땅치 않아요. "
                "그래도 '오늘 6시', '내일 7시 반', '30분 뒤'처럼 말해주면 바로 반영할게요."
            )

        lines = ["이렇게 옮겨볼 수 있어요."]
        for item in suggestions:
            end_at = item.start_at + duration
            lines.append(f"- {item.label}: {item.start_at:%m/%d %H:%M} - {end_at:%H:%M}")
        lines.append("원하는 시간이 있으면 말로 답장해도 바로 반영할게요.")
        return "\n".join(lines)

    def build_reschedule_suggestions(self, now: datetime) -> list[ParsedTimeExpression]:
        return self.time_parser.build_reschedule_suggestions(now)

    def _clarification_message(self, lead_text: str | None = None) -> str:
        lines = []
        if lead_text:
            lines.append(lead_text)
        lines.append("언제로 다시 잡을까요?")
        lines.append("예: 오늘 6시, 내일 7시 반, 30분 뒤")
        return "\n".join(lines)
