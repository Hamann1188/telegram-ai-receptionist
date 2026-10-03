"""The clinic profile from data/clinic.yaml: hours, bookable services, FAQ."""

from datetime import date, time
from functools import cached_property
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, PositiveInt, field_validator, model_validator

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LocalizedText(_Model):
    en: str
    ru: str
    uz: str


class OpeningHours(_Model):
    open: time
    close: time

    @model_validator(mode="after")
    def _open_before_close(self):
        if self.open >= self.close:
            raise ValueError(f"opens at {self.open} but closes at {self.close}")
        return self


class BookingRules(_Model):
    slot_step_minutes: PositiveInt
    min_notice_minutes: int
    horizon_days: PositiveInt


class Resource(_Model):
    name: LocalizedText
    days: frozenset[Weekday] | None = None  # None: every day the clinic is open


class Service(_Model):
    id: str
    name: LocalizedText
    resource: str
    duration_minutes: PositiveInt
    price_uzs: int
    price_note: str | None = None


class Clinic(_Model):
    model_config = ConfigDict(frozen=True, extra="forbid", ignored_types=(cached_property,))

    name: str
    fictional: bool
    timezone: str
    address: str
    phone: str
    telegram: str
    hours: dict[Weekday, OpeningHours | None]
    holidays: frozenset[date] = frozenset()
    booking: BookingRules
    resources: dict[str, Resource]
    services: tuple[Service, ...]
    faq: dict[str, str]

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @field_validator("hours")
    @classmethod
    def _every_weekday(cls, value: dict) -> dict:
        missing = [day for day in WEEKDAYS if day not in value]
        if missing:
            raise ValueError(f"hours missing for {', '.join(missing)} (use null for closed)")
        return value

    @model_validator(mode="after")
    def _consistent(self):
        ids = [s.id for s in self.services]
        if len(ids) != len(set(ids)):
            raise ValueError("service ids must be unique")
        step = self.booking.slot_step_minutes
        for service in self.services:
            if service.resource not in self.resources:
                raise ValueError(f"service {service.id!r}: unknown resource {service.resource!r}")
            if service.duration_minutes % step:
                raise ValueError(f"service {service.id!r}: duration is not a multiple of {step}")
        return self

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)


def load_clinic(path: Path) -> Clinic:
    with path.open(encoding="utf-8") as f:
        return Clinic.model_validate(yaml.safe_load(f))
