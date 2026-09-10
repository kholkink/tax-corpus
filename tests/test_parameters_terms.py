"""Параметры v0 подтверждены якорями в тексте источника; термины ст. 11 извлекаются."""

import json
from pathlib import Path

import pytest

from taxcorpus.amendments import amendments_from_records
from taxcorpus.terms import extract_terms, split_definition

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "data" / "parameters" / "parameters_v0.json").read_text(encoding="utf-8"))
DATA = ROOT / "data" / "processed"
UNIT_FILES = sorted(DATA.glob("*_units.jsonl"))

needs_corpus = pytest.mark.skipif(len(UNIT_FILES) < 2, reason="корпус не сгенерирован (parse)")


@pytest.fixture(scope="module")
def corpus() -> dict[str, dict]:
    units: dict[str, dict] = {}
    for path in UNIT_FILES:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    units[rec["unit_id"]] = rec
    return units


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("ё", "е").split())


def test_parameter_names_unique():
    names = [p["name"] for p in SEED["parameters"]]
    assert len(names) == len(set(names))


@needs_corpus
@pytest.mark.parametrize("p", SEED["parameters"], ids=lambda p: p["name"])
def test_parameter_anchor_in_source(p, corpus):
    unit = corpus.get(p["source_unit_id"])
    assert unit is not None, f"{p['name']}: нет единицы {p['source_unit_id']}"
    text = _norm(unit.get("full_text") or unit["text"])
    assert _norm(p["anchor"]) in text, f"{p['name']}: якорь «{p['anchor']}» не найден"
    if p["valid_from_source"] == "amendment":
        dates = {a["effective_date"] for a in amendments_from_records([unit]) if a["scope"] == "unit"}
        assert p["valid_from"] in dates, f"{p['name']}: {p['valid_from']} нет среди дат правок {dates}"
    elif p["valid_from_source"] == "text":
        assert p["valid_from"][:4] in unit["full_text"], f"{p['name']}: год не упомянут в тексте"
    else:
        assert p["valid_from"] is None


def test_split_definition():
    assert split_definition("организации - юридические лица, образованные …;") == \
        ("организации", "юридические лица, образованные …")
    assert split_definition("коэффициент-дефлятор - коэффициент, устанавливаемый ежегодно") == \
        ("коэффициент-дефлятор", "коэффициент, устанавливаемый ежегодно")
    assert split_definition('"Инвестиционный проект" - ограниченный по времени проект') == \
        ("Инвестиционный проект", "ограниченный по времени проект")
    assert split_definition("задолженность (далее - долг) - общая сумма недоимок") == \
        ("задолженность (далее - долг)", "общая сумма недоимок")
    assert split_definition("Для целей настоящего Кодекса используются следующие понятия:") is None


@needs_corpus
def test_terms_from_article_11(corpus):
    rows = extract_terms(list(corpus.values()))
    terms = {r["term"] for r in rows}
    assert {"организации", "физические лица", "индивидуальные предприниматели",
            "коэффициент-дефлятор"} <= terms
    assert len(rows) >= 25
    assert all(r["definition_unit_id"].startswith("nk1.ch1.art11.p2.ab") for r in rows)
