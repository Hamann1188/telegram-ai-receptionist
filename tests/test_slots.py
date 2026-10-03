from datetime import UTC, date, datetime, time, timedelta

import pytest

from receptionist.core.clinic import Clinic
from receptionist.core.slots import free_slots

# A Monday, a Tuesday and a Sunday in October 2026.
MON, TUE, SUN = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 11)


@pytest.fixture
def clinic() -> Clinic:
    hours = {"open": "09:00", "close": "20:00"}
    return Clinic.model_validate(
        {
            "name": "Test Clinic",
            "fictional": True,
            "timezone": "Asia/Tashkent",
            "address": "x",
            "phone": "x",
            "telegram": "x",
            "hours": {
                **{day: hours for day in ("mon", "tue", "wed", "thu", "fri")},
                "sat": {"open": "10:00", "close": "16:00"},
                "sun": None,
            },
            "holidays": ["2026-10-07"],
            "booking": {"slot_step_minutes": 30, "min_notice_minutes": 60, "horizon_days": 30},
            "resources": {
                "therapist": {"name": {"en": "a", "ru": "a", "uz": "a"}},
                "pediatric": {"name": {"en": "b", "ru": "b", "uz": "b"}, "days": ["tue"]},
            },
            "services": [
                _service("filling", "therapist", 60),
                _service("root-canal", "therapist", 90),
                _service("child", "pediatric", 30),
            ],
            "faq": {},
        }
    )


def _service(service_id: str, resource: str, minutes: int) -> dict:
    name = {"en": service_id, "ru": service_id, "uz": service_id}
    return {
        "id": service_id,
        "name": name,
        "resource": resource,
        "duration_minutes": minutes,
        "price_uzs": 1,
    }


def local(clinic: Clinic, day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), clinic.tz)


def times(slots: list[datetime]) -> list[str]:
    return [s.strftime("%H:%M") for s in slots]


def slots_on(clinic, service_id, day, busy=(), now=None, days=1):
    now = now or local(clinic, day - timedelta(days=1), 8)
    return free_slots(clinic, clinic.service(service_id), busy, now, day, days)


def test_whole_working_day_in_steps(clinic):
    slots = slots_on(clinic, "filling", MON)
    assert times(slots)[:3] == ["09:00", "09:30", "10:00"]
    assert times(slots)[-1] == "19:00"  # a 60-minute visit must end by closing time
    assert len(slots) == 21


def test_longer_service_ends_by_closing_time(clinic):
    assert times(slots_on(clinic, "root-canal", MON))[-1] == "18:30"


def test_slots_are_in_the_clinic_time_zone(clinic):
    first = slots_on(clinic, "filling", MON)[0]
    assert first.utcoffset() == timedelta(hours=5)
    assert first.astimezone(UTC).hour == 4


def test_closed_days_and_holidays_have_no_slots(clinic):
    assert slots_on(clinic, "filling", SUN) == []
    assert slots_on(clinic, "filling", date(2026, 10, 7)) == []  # holiday


def test_saturday_hours(clinic):
    slots = slots_on(clinic, "filling", date(2026, 10, 10))
    assert (times(slots)[0], times(slots)[-1]) == ("10:00", "15:00")


def test_resource_works_only_on_its_days(clinic):
    week = slots_on(clinic, "child", MON, days=7)
    assert {s.date() for s in week} == {TUE}


def test_existing_bookings_block_overlapping_starts(clinic):
    busy = [(local(clinic, MON, 10), local(clinic, MON, 11))]
    starts = times(slots_on(clinic, "filling", MON, busy))
    assert "09:00" in starts  # ends exactly when the booking starts
    assert "09:30" not in starts  # would run into 10:00
    assert "10:00" not in starts and "10:30" not in starts
    assert "11:00" in starts  # starts exactly when the booking ends


def test_busy_intervals_in_utc_are_compared_correctly(clinic):
    busy = [(datetime(2026, 10, 5, 5, 0, tzinfo=UTC), datetime(2026, 10, 5, 6, 0, tzinfo=UTC))]
    starts = times(slots_on(clinic, "filling", MON, busy))  # 10:00-11:00 local
    assert "10:00" not in starts and "11:00" in starts


def test_past_times_and_minimum_notice_are_excluded(clinic):
    now = local(clinic, MON, 12, 10)  # earliest start is 13:10, so 13:30
    assert times(slots_on(clinic, "filling", MON, now=now))[0] == "13:30"


def test_now_in_utc_gives_the_same_result(clinic):
    now_utc = local(clinic, MON, 12, 10).astimezone(UTC)
    assert times(slots_on(clinic, "filling", MON, now=now_utc))[0] == "13:30"


def test_days_after_the_horizon_are_excluded(clinic):
    now = local(clinic, MON, 8)
    later = free_slots(clinic, clinic.service("filling"), [], now, MON + timedelta(days=29), 5)
    assert {s.date() for s in later} == {MON + timedelta(days=29), MON + timedelta(days=30)}


def test_dates_in_the_past_have_no_slots(clinic):
    assert slots_on(clinic, "filling", MON, now=local(clinic, TUE, 8)) == []


def test_zero_days_returns_nothing(clinic):
    assert slots_on(clinic, "filling", MON, days=0) == []


def test_naive_now_is_rejected(clinic):
    with pytest.raises(ValueError, match="timezone-aware"):
        free_slots(clinic, clinic.service("filling"), [], datetime(2026, 10, 5, 8), MON, 1)
