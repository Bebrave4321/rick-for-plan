from datetime import datetime
from zoneinfo import ZoneInfo

from study_assistant.services.time_parser import TimeParser


def test_time_parser_resolves_korean_evening_time():
    parser = TimeParser(ZoneInfo("Asia/Seoul"))
    now = datetime(2026, 3, 27, 0, 50, tzinfo=ZoneInfo("Asia/Seoul"))

    parsed = parser.parse_reschedule_time("오늘 저녁 6시로 옮겨줘", now)

    assert parsed is not None
    assert parsed.start_at.hour == 18
    assert parsed.start_at.minute == 0
    assert parsed.start_at.date() == now.date()


def test_time_parser_resolves_relative_minutes():
    parser = TimeParser(ZoneInfo("Asia/Seoul"))
    now = datetime(2026, 3, 27, 15, 5, tzinfo=ZoneInfo("Asia/Seoul"))

    parsed = parser.parse_reschedule_time("30분 뒤로 미뤄줘", now)

    assert parsed is not None
    assert parsed.start_at.hour == 15
    assert parsed.start_at.minute == 35


def test_time_parser_resolves_tomorrow_half_hour_time():
    parser = TimeParser(ZoneInfo("Asia/Seoul"))
    now = datetime(2026, 3, 27, 15, 5, tzinfo=ZoneInfo("Asia/Seoul"))

    parsed = parser.parse_reschedule_time("내일 저녁 7시 반으로 옮겨줘", now)

    assert parsed is not None
    assert parsed.start_at.date() == datetime(2026, 3, 28, 19, 30, tzinfo=ZoneInfo("Asia/Seoul")).date()
    assert parsed.start_at.hour == 19
    assert parsed.start_at.minute == 30
