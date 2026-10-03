from datetime import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from receptionist.core.clinic import Clinic, load_clinic

CLINIC_FILE = Path(__file__).resolve().parents[1] / "data" / "clinic.yaml"


@pytest.fixture(scope="module")
def clinic() -> Clinic:
    return load_clinic(CLINIC_FILE)


def test_profile_loads_and_is_marked_fictional(clinic):
    assert clinic.name == "Registan Smile Clinic"
    assert clinic.fictional is True
    assert clinic.timezone == "Asia/Tashkent"


def test_hours_match_the_patient_handbook(clinic):
    assert clinic.hours["mon"].open == time(9) and clinic.hours["fri"].close == time(20)
    assert (clinic.hours["sat"].open, clinic.hours["sat"].close) == (time(10), time(16))
    assert clinic.hours["sun"] is None


@pytest.mark.parametrize(
    ("service_id", "price"),
    [
        ("consultation", 100_000),
        ("hygiene", 450_000),
        ("filling", 550_000),
        ("child-checkup", 80_000),
    ],
)
def test_prices_match_the_price_list(clinic, service_id, price):
    assert clinic.service(service_id).price_uzs == price


def test_childrens_dentist_works_tuesday_thursday_saturday(clinic):
    assert clinic.resources["pediatric"].days == {"tue", "thu", "sat"}


def test_every_service_has_names_in_three_languages(clinic):
    for service in clinic.services:
        assert all((service.name.en, service.name.ru, service.name.uz)), service.id


def test_faq_covers_the_basics(clinic):
    for topic in ("hours", "address", "parking", "payment", "cancellation", "children"):
        assert clinic.faq[topic].strip(), topic


def test_unknown_service_is_none(clinic):
    assert clinic.service("teleportation") is None


def _raw() -> dict:
    return yaml.safe_load(CLINIC_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["services"][0].update(resource="dentist-on-mars"), "unknown resource"),
        (lambda d: d["services"][0].update(duration_minutes=45), "multiple of 30"),
        (lambda d: d["services"].append(dict(d["services"][0])), "unique"),
        (lambda d: d["hours"].pop("sun"), "missing for sun"),
        (lambda d: d["hours"]["mon"].update(open="21:00"), "closes at"),
        (lambda d: d.update(timezone="Mars/Olympus"), "unknown time zone"),
        (lambda d: d.update(colour="green"), "Extra inputs"),
    ],
)
def test_invalid_profiles_are_rejected(change, message):
    data = _raw()
    change(data)
    with pytest.raises(ValidationError, match=message):
        Clinic.model_validate(data)
