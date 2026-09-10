"""compute_deadline по ст. 6.1 НК: правила п. 2–8, календарь, ссылки на единицы корпуса."""

import json
from datetime import date
from pathlib import Path

import pytest

from taxcorpus.deadlines import ART, RULES, ProductionCalendar, compute_deadline

CAL = ProductionCalendar()  # только выходные и праздники ст. 112 ТК


def test_working_days_skip_weekends():
    # пятница 7 марта 2025 -> счёт с понедельника 10 марта: 10 рабочих дней = 21 марта
    r = compute_deadline(date(2025, 3, 7), 10, "days", CAL)
    assert r.counting_from == date(2025, 3, 8)  # следующий календарный день (п. 2)
    assert r.end == date(2025, 3, 21) and not r.shifted
    assert r.applied == [RULES["start"], RULES["days"], RULES["until_midnight"]]


def test_working_days_skip_holidays():
    # с 29 апреля 2025: 30.04 (1), 1 и 2 мая — праздник и пятница (2), 5.05 (3)…
    r = compute_deadline(date(2025, 4, 29), 3, "days", CAL)
    assert r.end == date(2025, 5, 5)


def test_calendar_days_and_shift_to_working_day():
    # 8 календарных дней с четверга 2 октября 2025: 3..10 октября; 10.10 — пятница
    r = compute_deadline(date(2025, 10, 2), 8, "calendar_days", CAL)
    assert r.end == date(2025, 10, 10) and not r.shifted
    # 8 календарных дней с 3 октября: 4..11 октября, 11.10 — суббота -> понедельник 13.10 (п. 7)
    r = compute_deadline(date(2025, 10, 3), 8, "calendar_days", CAL)
    assert r.nominal_end == date(2025, 10, 11)
    assert r.end == date(2025, 10, 13) and r.shifted
    assert RULES["shift"] in r.applied


def test_months_same_day_and_clipping():
    # камеральная проверка: декларация 20 марта -> 3 месяца -> 20 июня 2025 (пятница)
    r = compute_deadline(date(2025, 3, 20), 3, "months", CAL)
    assert r.end == date(2025, 6, 20) and RULES["months"] in r.applied
    # 31 января + 3 месяца: в апреле нет 31-го -> 30 апреля
    r = compute_deadline(date(2025, 1, 31), 3, "months", CAL)
    assert r.nominal_end == date(2025, 4, 30)
    # 8 марта 2025 + 3 месяца = 8 июня (воскресенье) -> 9 июня
    r = compute_deadline(date(2025, 3, 8), 3, "months", CAL)
    assert r.nominal_end == date(2025, 6, 8) and r.end == date(2025, 6, 9)


def test_years_and_leap_day():
    r = compute_deadline(date(2024, 2, 29), 1, "years", CAL)
    assert r.nominal_end == date(2025, 2, 28)
    r = compute_deadline(date(2022, 6, 15), 3, "years", CAL)
    assert r.nominal_end == date(2025, 6, 15) and RULES["years"] in r.applied


def test_quarters_end_of_last_month():
    r = compute_deadline(date(2025, 2, 10), 1, "quarters", CAL)
    assert r.nominal_end == date(2025, 5, 31) and RULES["quarters"] in r.applied


def test_calendar_overrides_and_note():
    cal = ProductionCalendar(extra_holidays={date(2025, 5, 2)}, extra_workdays={date(2025, 11, 1)},
                             years_loaded={2025})
    assert not cal.is_working_day(date(2025, 5, 2))       # перенесённый выходной
    assert cal.is_working_day(date(2025, 11, 1))          # рабочая суббота
    r = compute_deadline(date(2025, 4, 29), 3, "days", cal)
    assert r.end == date(2025, 5, 6)                      # 30.04, 5.05, 6.05
    assert r.calendar_note is None
    assert compute_deadline(date(2025, 4, 29), 3, "days", CAL).calendar_note


def test_invalid_input():
    with pytest.raises(ValueError):
        compute_deadline(date(2025, 1, 1), 0, "days", CAL)
    with pytest.raises(ValueError):
        compute_deadline(date(2025, 1, 1), 5, "weeks", CAL)


UNITS_FILE = Path(__file__).resolve().parents[1] / "data" / "processed" / "nk1_units.jsonl"


@pytest.mark.skipif(not UNITS_FILE.exists(), reason="корпус не сгенерирован (parse)")
def test_rules_cite_existing_units_with_expected_wording():
    units = {}
    with UNITS_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["unit_id"].startswith(ART):
                units[rec["unit_id"]] = rec["text"].lower()
    expect = {
        "start": "на следующий день", "years": "исчисляемый годами",
        "quarters": "исчисляемый кварталами", "months": "исчисляемый месяцами",
        "days": "в рабочих днях", "shift": "ближайший следующий за ним рабочий день",
        "until_midnight": "до 24 часов",
    }
    for key, phrase in expect.items():
        assert phrase in units[RULES[key]], f"{key}: {RULES[key]}"
