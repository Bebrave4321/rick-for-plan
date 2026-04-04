from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(slots=True)
class ParsedTimeExpression:
    start_at: datetime
    label: str


class TimeParser:
    def __init__(self, timezone):
        self.timezone = timezone

    def parse_reschedule_time(self, text: str, now: datetime) -> ParsedTimeExpression | None:
        normalized = self._normalize(text)

        relative_match = re.search(r"(\d{1,3})\s*분\s*(?:뒤|후)", normalized)
        if relative_match:
            minutes = int(relative_match.group(1))
            start_at = (now + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
            return ParsedTimeExpression(start_at=start_at, label=f"{minutes}분 뒤")

        explicit_match = re.search(
            r"(?P<hour>\d{1,2})\s*시(?:\s*(?P<minute>\d{1,2})\s*분|\s*(?P<half>반))?",
            normalized,
        )
        if explicit_match:
            resolved = self._resolve_explicit_time(normalized, now, explicit_match)
            if resolved is not None:
                return ParsedTimeExpression(
                    start_at=resolved,
                    label=self._build_explicit_label(normalized, resolved),
                )

        if "오늘 저녁" in normalized or "오늘 밤" in normalized:
            start_at = self._evening_anchor(now, day_offset=0)
            return ParsedTimeExpression(start_at=start_at, label="오늘 저녁")

        if "내일 저녁" in normalized or "내일 밤" in normalized:
            start_at = self._evening_anchor(now, day_offset=1)
            return ParsedTimeExpression(start_at=start_at, label="내일 저녁")

        if "내일" in normalized:
            start_at = datetime.combine(now.date() + timedelta(days=1), time(19, 0), tzinfo=self.timezone)
            return ParsedTimeExpression(start_at=start_at, label="내일 19:00")

        return None

    def build_reschedule_suggestions(self, now: datetime) -> list[ParsedTimeExpression]:
        suggestions: list[ParsedTimeExpression] = []
        seen: set[datetime] = set()

        candidates = [
            ("오늘 저녁", self._evening_anchor(now, day_offset=0)),
            ("오늘 조금 늦게", self._later_today_anchor(now)),
            ("내일 저녁", self._evening_anchor(now, day_offset=1)),
        ]

        for label, start_at in candidates:
            if start_at <= now or start_at in seen:
                continue
            suggestions.append(ParsedTimeExpression(start_at=start_at, label=label))
            seen.add(start_at)

        return suggestions

    def _resolve_explicit_time(self, normalized: str, now: datetime, match: re.Match[str]) -> datetime | None:
        hour = int(match.group("hour"))
        minute = 30 if match.group("half") else int(match.group("minute") or 0)
        meridiem = self._resolve_meridiem(normalized)
        day_offset = self._resolve_day_offset(normalized)

        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0

        if hour > 23 or minute > 59:
            return None

        target_date = now.date() + timedelta(days=day_offset or 0)
        candidate = datetime.combine(target_date, time(hour, minute), tzinfo=self.timezone)

        if day_offset is None and candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate

    def _resolve_day_offset(self, normalized: str) -> int | None:
        if "내일" in normalized:
            return 1
        if "오늘" in normalized:
            return 0
        return None

    def _resolve_meridiem(self, normalized: str) -> str | None:
        if any(keyword in normalized for keyword in ["오후", "저녁", "밤"]):
            return "pm"
        if any(keyword in normalized for keyword in ["오전", "새벽", "아침"]):
            return "am"
        return None

    def _build_explicit_label(self, normalized: str, start_at: datetime) -> str:
        if "내일" in normalized:
            prefix = "내일"
        elif "오늘" in normalized:
            prefix = "오늘"
        else:
            prefix = "다음 가능한 시간"
        return f"{prefix} {start_at:%H:%M}"

    def _evening_anchor(self, now: datetime, *, day_offset: int) -> datetime:
        target_date = now.date() + timedelta(days=day_offset)
        anchor = datetime.combine(target_date, time(19, 0), tzinfo=self.timezone)
        if day_offset == 0 and anchor <= now:
            return datetime.combine(now.date() + timedelta(days=1), time(19, 0), tzinfo=self.timezone)
        return anchor

    def _later_today_anchor(self, now: datetime) -> datetime:
        candidate = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
        if candidate.minute == 0:
            return candidate
        if candidate.minute <= 30:
            return candidate.replace(minute=30)
        return (candidate + timedelta(hours=1)).replace(minute=0)

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())
