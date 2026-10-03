"""The eval's scenarios, deterministic checks and report, without calling Claude."""

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.run import (
    Verdict,
    extract_tool_calls,
    failed_targets,
    grade,
    judge_prompt,
    merge_results,
    render_markdown,
    summarize,
)
from evals.scenario import Observation, Scenario, ToolCall, TurnRecord, load_scenarios, run_checks
from receptionist.core.clinic import load_clinic
from receptionist.core.slots import free_slots
from tests.fakes import message, text, tool_use

CLINIC = load_clinic(Path(__file__).resolve().parents[1] / "data" / "clinic.yaml")
SCENARIOS = load_scenarios()


# --- the scenario files --------------------------------------------------------


def test_scenarios_cover_the_architecture_list():
    ids = {s.id for s in SCENARIOS}
    assert len(SCENARIOS) >= 12
    assert {s.lang for s in SCENARIOS} == {"ru", "uz", "en"}
    for required in (
        "booking-ru-happy-path",
        "booking-slot-taken",
        "booking-change-of-mind",
        "cancel-en",
        "human-request-ru",
        "medical-en",
        "emergency-uz",
        "off-topic-en",
        "injection-ru-discount",
    ):
        assert required in ids


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_refers_to_real_services_and_bookable_times(scenario):
    expected = scenario.expect.bookings or ()
    for booking in expected:
        service = CLINIC.service(booking.service)
        assert service is not None, booking.service
        now = scenario.now.replace(tzinfo=CLINIC.tz)
        free = free_slots(CLINIC, service, [], now, booking.date, 1)
        assert booking.time in {f"{s:%H:%M}" for s in free}, "expected time isn't bookable"
    setups = [*scenario.setup, *(b for t in scenario.turns for b in t.before)]
    for setup in setups:
        assert CLINIC.service(setup.service) is not None, setup.service


def test_every_scenario_has_judge_criteria():
    assert all(s.judge for s in SCENARIOS)


# --- checks ----------------------------------------------------------------------


def scenario(**overrides) -> Scenario:
    data = {
        "id": "x",
        "title": "x",
        "lang": "ru",
        "turns": [{"user": "a", "expect": {"bookings_count": 0}}, {"user": "b"}],
        "expect": {
            "bookings": [{"service": "hygiene", "date": "2026-10-13", "time": "09:00"}],
            "tools": ["create_booking"],
            "handoff": False,
            "reply_contains": ["09:00"],
        },
        "judge": ["Reads back the details."],
    }
    return Scenario.model_validate(data | overrides)


def turn(reply="Готово, 09:00.", bookings=0, calls=(), outcome="answered") -> TurnRecord:
    return TurnRecord(
        user="u",
        lang="ru",
        reply=reply,
        outcome=outcome,
        handoff=False,
        tool_calls=list(calls),
        bookings_count=bookings,
    )


def observation(turns, bookings=(("hygiene", date(2026, 10, 13), "09:00"),), handoffs=()):
    return Observation(
        turns=turns, bookings=list(bookings), cancelled=0, handoff_reasons=list(handoffs)
    )


def test_all_checks_pass():
    seen = observation([turn(), turn(calls=[ToolCall("create_booking", {})], bookings=1)])
    checks = run_checks(scenario(), seen)
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]
    assert {c.group for c in checks} == {
        "booking",
        "outcome",
        "markup",
        "tools",
        "handoff",
        "reply",
    }


def test_booking_too_early_fails_the_turn_check():
    seen = observation([turn(bookings=1), turn(calls=[ToolCall("create_booking", {})], bookings=1)])
    failed = [c.name for c in run_checks(scenario(), seen) if not c.passed]
    assert failed == ["turn 1: 0 booking(s)"]


def test_wrong_final_booking_and_missing_tool():
    seen = observation([turn(), turn()], bookings=[("hygiene", date(2026, 10, 13), "09:30")])
    failed = {c.name: c.detail for c in run_checks(scenario(), seen) if not c.passed}
    assert "final bookings" in failed and "09:30" in failed["final bookings"]
    assert "called create_booking" in failed


def test_leaked_markup_error_outcome_and_handoff():
    seen = observation(
        [turn(reply="antml_reply_Привет 09:00"), turn(outcome="error")], handoffs=["complaint"]
    )
    failed = {c.name for c in run_checks(scenario(), seen) if not c.passed}
    assert {"turn 1: no internal markup", "turn 2: answered", "not handed off"} <= failed


def test_unknown_tool_or_reason_in_a_scenario_is_rejected():
    with pytest.raises(ValueError, match="unknown tools"):
        scenario(expect={"tools": ["teleport"]})
    with pytest.raises(ValueError, match="unknown handoff reason"):
        scenario(expect={"handoff_reason": "boredom"})


# --- runner helpers ----------------------------------------------------------------


def test_extract_tool_calls_pairs_results_by_id():
    first = message(tool_use("t1", "list_services", {}), stop_reason="tool_use")
    second = message(text("450 000"))
    sent_second = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"a": 1}', "is_error": False}
        ],
    }
    calls = extract_tool_calls([({"role": "user", "content": []}, first), (sent_second, second)])
    assert calls == [ToolCall("list_services", {}, False, '{"a": 1}')]


def test_judge_prompt_lists_criteria_and_turns():
    prompt = judge_prompt(scenario(), [turn(calls=[ToolCall("list_services", {}, False, "{}")])])
    assert "C1: Reads back the details." in prompt
    assert "Receptionist (reply 1): Готово, 09:00." in prompt
    assert "[tool list_services {} -> {}]" in prompt
    assert "Monday 2026-10-12 09:30" in prompt


def verdict(languages, criteria_passed=True, medical=False) -> Verdict:
    return Verdict(
        reply_languages=languages,
        medical_advice_reason="none",
        medical_advice=medical,
        criteria=[{"id": "C1", "reason": "ok", "passed": criteria_passed}],
    )


def test_grade_summary_and_report():
    sc = scenario()
    seen = observation([turn(), turn(calls=[ToolCall("create_booking", {})], bookings=1)])
    checks = run_checks(sc, seen)
    good = grade(sc, seen, checks, verdict(["ru", "ru"]), None, 0.01)
    bad = grade(sc, seen, checks, verdict(["ru", "en"], criteria_passed=False), None, 0.01)
    assert good["passed"] and not bad["passed"]

    metrics = summarize([good, bad])
    assert metrics["language_match"] == 0.75 and metrics["judge_criteria"] == 0.5
    assert metrics["booking_checks"] == 1.0 and metrics["medical_advice"] == 0
    assert failed_targets(metrics) == ["language_match", "judge_criteria"]

    settings = SimpleNamespace(model="claude-opus-5-5", effort="low")
    report = render_markdown(metrics, [good, bad], settings)
    assert "| Reply in the patient's language | 75% ❌ | ≥ 95% | 4 |" in report
    assert "reply in en, expected ru" in report


def test_judge_failure_counts_against_the_scenario():
    sc = scenario()
    seen = observation([turn(), turn(calls=[ToolCall("create_booking", {})], bookings=1)])
    result = grade(sc, seen, run_checks(sc, seen), None, "judge failed: APIError", 0.0)
    assert not result["passed"]
    assert result["criteria"][0]["reason"] == "judge failed: APIError"
    assert summarize([result])["medical_advice"] == 1  # unknown counts as a miss


def test_merge_replaces_rerun_scenarios_in_file_order():
    previous = [{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}]
    merged = merge_results(previous, [{"id": "b", "v": 2}], ["a", "b", "c"])
    assert merged == [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 1}]
    assert merge_results([], [{"id": "b", "v": 2}], ["a", "b"]) == [{"id": "b", "v": 2}]


def test_default_clock_is_a_monday_morning():
    assert SCENARIOS[0].now == datetime(2026, 10, 12, 9, 30)
    assert SCENARIOS[0].now.weekday() == 0
