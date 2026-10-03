"""Scripted conversations for the end-to-end eval, and their deterministic checks.

A scenario is a list of patient messages plus expectations: which bookings must exist
at the end (and after given turns), whether the chat was handed to staff, which tools
ran, and what the final reply must contain. Criteria that need judgment (did it read
the details back, did it avoid medical advice) go to an LLM judge in evals/run.py.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from receptionist.core.tools import HANDLERS, HANDOFF_REASONS

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
Language = Literal["ru", "uz", "en"]
# Monday 12 October 2026, 09:30 in the clinic's time zone, unless a scenario says otherwise.
DEFAULT_NOW = datetime(2026, 10, 12, 9, 30)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetupBooking(_Model):
    """A booking made outside the conversation, before it or between turns."""

    service: str
    start: datetime  # clinic local time, no zone
    patient: bool = False  # the patient's own booking, or another patient's


class TurnExpect(_Model):
    bookings_count: int | None = None  # the patient's confirmed bookings after the turn
    cancelled_count: int | None = None


class Turn(_Model):
    user: str
    lang: Language | None = None  # defaults to the scenario's language
    before: tuple[SetupBooking, ...] = ()
    expect: TurnExpect = TurnExpect()


class ExpectedBooking(_Model):
    service: str
    date: date
    time: str = Field(pattern=r"^\d{2}:\d{2}$")


class Expect(_Model):
    bookings: tuple[ExpectedBooking, ...] | None = None  # exactly these, in time order
    cancelled: int | None = None
    tools: tuple[str, ...] = ()  # each must be called at least once
    tools_any: tuple[str, ...] = ()  # at least one of these must be called
    handoff: bool | None = None
    handoff_reason: str | None = None
    reply_contains: tuple[str, ...] = ()  # substrings of the last reply

    @field_validator("tools", "tools_any")
    @classmethod
    def _known_tools(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(names) - set(HANDLERS)
        if unknown:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        return names

    @field_validator("handoff_reason")
    @classmethod
    def _known_reason(cls, reason: str | None) -> str | None:
        if reason is not None and reason not in HANDOFF_REASONS:
            raise ValueError(f"unknown handoff reason {reason!r}")
        return reason


class Scenario(_Model):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    lang: Language
    now: datetime = DEFAULT_NOW
    setup: tuple[SetupBooking, ...] = ()
    turns: tuple[Turn, ...] = Field(min_length=1)
    expect: Expect = Expect()
    judge: tuple[str, ...] = ()  # criteria for the LLM judge, in English

    def turn_language(self, turn: Turn) -> Language:
        return turn.lang or self.lang


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            scenarios.extend(Scenario.model_validate(item) for item in yaml.safe_load(f))
    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario ids: {sorted(duplicates)}")
    return scenarios


# --- what a run observed -------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    input: dict
    is_error: bool | None = None  # None when the result wasn't sent (loop ended)
    result: str = ""


@dataclass
class TurnRecord:
    user: str
    lang: Language
    reply: str
    outcome: str  # AgentReply.outcome
    handoff: bool
    tool_calls: list[ToolCall] = field(default_factory=list)
    bookings_count: int = 0
    cancelled_count: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0


@dataclass
class Observation:
    turns: list[TurnRecord]
    bookings: list[tuple[str, date, str]]  # the patient's confirmed: service, date, HH:MM
    cancelled: int
    handoff_reasons: list[str]  # handoffs opened in the patient's chat

    @property
    def tools_called(self) -> set[str]:
        return {call.name for turn in self.turns for call in turn.tool_calls}


@dataclass(frozen=True)
class Check:
    group: str  # booking | handoff | tools | reply | markup | outcome
    name: str
    passed: bool
    detail: str = ""


def run_checks(scenario: Scenario, seen: Observation) -> list[Check]:
    checks: list[Check] = []

    for number, (turn, record) in enumerate(zip(scenario.turns, seen.turns, strict=True), 1):
        if turn.expect.bookings_count is not None:
            checks.append(
                Check(
                    "booking",
                    f"turn {number}: {turn.expect.bookings_count} booking(s)",
                    record.bookings_count == turn.expect.bookings_count,
                    f"found {record.bookings_count}",
                )
            )
        if turn.expect.cancelled_count is not None:
            checks.append(
                Check(
                    "booking",
                    f"turn {number}: {turn.expect.cancelled_count} cancelled",
                    record.cancelled_count == turn.expect.cancelled_count,
                    f"found {record.cancelled_count}",
                )
            )
        checks.append(
            Check(
                "outcome",
                f"turn {number}: answered",
                record.outcome == "answered" and bool(record.reply.strip()),
                record.outcome,
            )
        )
        checks.append(
            Check(
                "markup",
                f"turn {number}: no internal markup",
                "antml" not in record.reply.lower(),
            )
        )

    expect = scenario.expect
    if expect.bookings is not None:
        wanted = [(b.service, b.date, b.time) for b in expect.bookings]
        checks.append(
            Check(
                "booking",
                "final bookings",
                seen.bookings == wanted,
                f"expected {_fmt(wanted)}, found {_fmt(seen.bookings)}",
            )
        )
    if expect.cancelled is not None:
        checks.append(
            Check(
                "booking",
                f"{expect.cancelled} cancelled",
                seen.cancelled == expect.cancelled,
                f"found {seen.cancelled}",
            )
        )
    for tool in expect.tools:
        checks.append(Check("tools", f"called {tool}", tool in seen.tools_called))
    if expect.tools_any:
        checks.append(
            Check(
                "tools",
                f"called one of {', '.join(expect.tools_any)}",
                bool(set(expect.tools_any) & seen.tools_called),
                f"called {sorted(seen.tools_called) or 'none'}",
            )
        )
    if expect.handoff is not None:
        handed_off = bool(seen.handoff_reasons)
        checks.append(
            Check(
                "handoff",
                "handed off" if expect.handoff else "not handed off",
                handed_off == expect.handoff,
                f"handoffs: {seen.handoff_reasons or 'none'}",
            )
        )
    if expect.handoff_reason is not None:
        checks.append(
            Check(
                "handoff",
                f"handoff reason {expect.handoff_reason}",
                expect.handoff_reason in seen.handoff_reasons,
                f"handoffs: {seen.handoff_reasons or 'none'}",
            )
        )
    last_reply = seen.turns[-1].reply if seen.turns else ""
    for text in expect.reply_contains:
        checks.append(Check("reply", f"last reply contains {text!r}", text in last_reply))
    return checks


def _fmt(bookings) -> str:
    return "; ".join(f"{s} {d.isoformat()} {t}" for s, d, t in bookings) or "none"
