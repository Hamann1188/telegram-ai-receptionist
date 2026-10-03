"""Free appointment slots: a pure function of the clinic profile, bookings and the clock."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from receptionist.core.clinic import WEEKDAYS, Clinic, Service

Interval = tuple[datetime, datetime]  # [start, end), timezone-aware


def free_slots(
    clinic: Clinic,
    service: Service,
    busy: Iterable[Interval],
    now: datetime,
    date_from: date,
    days: int,
) -> list[datetime]:
    """Start times (in the clinic's time zone) when `service` can be booked.

    `busy` holds the confirmed bookings of the service's resource. A slot is free when
    it fits inside the opening hours, doesn't overlap a busy interval, starts at least
    `min_notice_minutes` after `now`, and its day is within the booking horizon and on
    a day the resource works. `date_from` is a date in the clinic's time zone.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    tz = clinic.tz
    rules = clinic.booking
    step = timedelta(minutes=rules.slot_step_minutes)
    duration = timedelta(minutes=service.duration_minutes)
    earliest = now + timedelta(minutes=rules.min_notice_minutes)
    last_day = now.astimezone(tz).date() + timedelta(days=rules.horizon_days)
    resource = clinic.resources[service.resource]
    taken = sorted(busy)

    slots: list[datetime] = []
    for offset in range(max(days, 0)):
        day = date_from + timedelta(days=offset)
        if day > last_day:
            break
        weekday = WEEKDAYS[day.weekday()]
        hours = clinic.hours[weekday]
        if hours is None or day in clinic.holidays:
            continue
        if resource.days is not None and weekday not in resource.days:
            continue
        start = datetime.combine(day, hours.open, tz)
        close = datetime.combine(day, hours.close, tz)
        while start + duration <= close:
            if start >= earliest and not _overlaps(start, start + duration, taken):
                slots.append(start)
            start += step
    return slots


def _overlaps(start: datetime, end: datetime, intervals: Iterable[Interval]) -> bool:
    return any(b_start < end and start < b_end for b_start, b_end in intervals)
