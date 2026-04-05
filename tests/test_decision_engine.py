from datetime import datetime
from zoneinfo import ZoneInfo

from study_assistant.services.decision_engine import DecisionEngine


def build_engine() -> DecisionEngine:
    return DecisionEngine(ZoneInfo("Asia/Seoul"))


def test_decide_reschedule_returns_no_match_for_non_time_replan_message():
    engine = build_engine()

    decision = engine.decide_reschedule("오늘 일정 다시 짜줘", datetime(2026, 4, 6, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")))

    assert decision.decision_type == "no_match"


def test_decide_reschedule_clarifies_vague_reschedule_request():
    engine = build_engine()

    decision = engine.decide_reschedule("좀 늦춰줘", datetime(2026, 4, 6, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")))

    assert decision.decision_type == "clarify"
    assert decision.clarification_message is not None


def test_decide_reschedule_resolves_specific_time_request():
    engine = build_engine()

    decision = engine.decide_reschedule("내일 저녁 6시로 옮겨줘", datetime(2026, 4, 6, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")))

    assert decision.decision_type == "reschedule"
    assert decision.parsed_time is not None
    assert decision.parsed_time.start_at.hour == 18
