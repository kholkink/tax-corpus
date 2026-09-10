"""Калькуляторы с цитатами (F5): правила подтверждены якорями в корпусе; числовые примеры."""

import json
from datetime import date
from pathlib import Path

import pytest

from taxcorpus import calculators as C
from taxcorpus.deadlines import ProductionCalendar
from taxcorpus.tools import run_calculator

CAL = ProductionCalendar()
RATES = C.KeyRates([
    {"valid_from": "2022-01-01", "valid_to": "2024-01-01", "rate": 7.5},
    {"valid_from": "2024-01-01", "valid_to": "2025-06-09", "rate": 16.0},
    {"valid_from": "2025-06-09", "valid_to": None, "rate": 20.0},
], last_day="2026-09-10")

UNITS_FILE = Path(__file__).resolve().parents[1] / "data" / "processed" / "nk1_units.jsonl"


def test_penalty_individual_flat_rate():
    # физлицо: 1/300 ключевой ставки за каждый день; 10 дней при 16 %: 100000*0.16/300*10 = 533.33
    r = C.compute_penalty(100_000, date(2024, 3, 1), date(2024, 3, 11), "individual", RATES)
    assert r.value == 533.33
    assert C.UNITS["penalty_days"] in r.applied and C.UNITS["penalty_rate"] in r.applied
    assert "2024-03-02–2024-03-11: 10 дн." in r.steps[1]


def test_penalty_organization_2025_rule_segments():
    # 2025: дни 1–30 1/300, 31–90 1/150, с 91-го 1/300 (п. 5.1 ст. 75); ставка 16 % до 09.06.2025
    r = C.compute_penalty(300_000, date(2025, 1, 31), date(2025, 5, 31), "organization", RATES)
    d300 = 300_000 * 0.16 / 300
    expected = round(d300 * 30 + d300 * 2 * 60 + d300 * 30, 2)  # 30 + 60 + 30 дней = 120 дней
    assert r.value == expected
    assert C.UNITS["penalty_2025"] in r.applied and C.UNITS["penalty_2022"] not in r.applied
    denoms = [s.split("/ ")[1].split(" =")[0] for s in r.steps if "дн." in s]
    assert denoms == ["300", "150", "300"]


def test_penalty_organization_2022_moratorium_and_general_rule():
    r = C.compute_penalty(1000, date(2023, 1, 1), date(2023, 3, 1), "organization", RATES)
    assert C.UNITS["penalty_2022"] in r.applied and "150" not in "".join(r.steps)  # 2022–2024: всё 1/300
    r = C.compute_penalty(1000, date(2027, 1, 1), date(2027, 3, 1), "organization", RATES)
    assert C.UNITS["penalty_rate"] in r.applied and any("/ 150" in s for s in r.steps)   # общее правило: с 31-го дня 1/150
    assert C.compute_penalty(1000, date(2025, 5, 5), date(2025, 5, 5), "organization", RATES).value == 0.0


def test_fines():
    r = C.compute_fine("119", 200_000, date(2025, 4, 25), date(2025, 7, 1))
    assert r.value == 30_000.0  # 3 месяца × 5 % = 15 %, не более 30 %: 30 000
    r = C.compute_fine("119", 500_000, date(2025, 4, 25), date(2026, 4, 25))  # 12 месяцев -> 60 %, кап 30 %
    assert r.value == 150_000.0
    r = C.compute_fine("119", 0, date(2025, 4, 25), date(2025, 4, 26))       # нечего платить — минимум 1000
    assert r.value == 1000.0
    assert C.compute_fine("122", 100_000).value == 20_000.0
    assert C.compute_fine("122_willful", 100_000).value == 40_000.0
    assert C.compute_fine("126", documents=7).value == 1400.0
    r = C.compute_fine("122", 100_000, mitigating=["тяжёлое материальное положение"], aggravating=True)
    assert r.value == 20_000.0  # +100 % = 40 000, затем /2 = 20 000
    assert {C.UNITS["mitigating"], C.UNITS["reduce"], C.UNITS["aggravating"], C.UNITS["increase"]} <= set(r.applied)
    with pytest.raises(ValueError):
        C.compute_fine("122", 100_000, mitigating=["x"], reduction_factor=1.5)


def test_appeal_deadlines_and_limitation():
    r = C.appeal_deadlines(act_received=date(2026, 3, 10), decision_received=date(2026, 5, 15),
                           decision_date=date(2026, 5, 12), complaint_decision_date=date(2026, 7, 1), cal=CAL)
    v = r.value
    assert v["objections_until"] == "2026-04-10" and v["decision_in_force"] == "2026-06-15"
    assert v["appeal_until"] == "2026-06-14" and v["complaint_until"] == "2027-05-12"
    assert v["fns_complaint_until"] == "2026-10-01"
    assert {C.UNITS["objections"], C.UNITS["decision_force"], C.UNITS["appeal"], C.UNITS["complaint"]} <= set(r.applied)
    # ст. 122: срок со следующего дня после окончания периода (2022) -> истекает 31.12.2025
    r = C.limitation_status(None, date(2026, 2, 1), article="122", period_end=date(2022, 12, 31), cal=CAL)
    assert r.value["expired"] is True and r.value["limitation_ends"] == "2025-12-31"
    r = C.limitation_status(date(2024, 6, 1), date(2026, 2, 1), article="126", cal=CAL)
    assert r.value["expired"] is False


def test_run_calculator_dispatch():
    out = run_calculator("compute_fine", {"article": "122", "base": 1000, "due_date": None, "actual_date": None,
                                          "documents": 0, "mitigating": [], "aggravating": False,
                                          "reduction_factor": 2}, CAL)
    assert out["value"] == 200.0 and out["applied_units"] == [C.UNITS["fine_122"]]
    with pytest.raises(ValueError):
        run_calculator("nope", {}, CAL)


def test_key_rate_file_and_lookup():
    rates = C.KeyRates.load()
    assert rates.rate_on(date(2026, 9, 1)) == 14.0 and rates.rate_on(date(2015, 1, 1)) is None
    assert len(rates.intervals) > 40


@pytest.mark.skipif(not UNITS_FILE.exists(), reason="корпус не сгенерирован")
def test_rules_anchored_in_corpus():
    units = {}
    with UNITS_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["unit_id"] in C.UNITS.values():
                units[rec["unit_id"]] = rec["full_text"].lower()
    anchors = {
        "penalty_days": "по день (включительно)", "penalty_rate": "одной стопятидесятой",
        "penalty_2022": "с 9 марта 2022 года по 31 декабря 2024", "penalty_2025": "по 90-й день",
        "mitigating": "смягчающими ответственность", "aggravating": "отягчающим",
        "reduce": "не менее чем в два раза", "increase": "увеличивается на 100 процентов",
        "fine_119": "не менее 1000 рублей", "fine_122": "20 процентов", "fine_122_willful": "40 процентов",
        "fine_126": "200 рублей", "objections": "в течение одного месяца со дня получения акта",
        "decision_force": "по истечении одного месяца со дня вручения",
        "appeal": "до дня вступления в силу", "complaint": "в течение одного года",
        "limitation": "истекли три года",
    }
    for key, phrase in anchors.items():
        assert phrase in units[C.UNITS[key]], f"{key}: {C.UNITS[key]}"
