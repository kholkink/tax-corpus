"""Параметры v0 подтверждены якорями в тексте источника; термины ст. 11 извлекаются."""

import json
from pathlib import Path

import pytest

from taxcorpus.amendments import amendments_from_records
from taxcorpus.terms import dictionary_scope, extract_terms, split_definition

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
    code_rows = [r for r in rows if r["definition_unit_id"].startswith("nk1.ch1.art11.p2.ab")]
    assert len(code_rows) >= 25 and all(r["scope"] == "code" for r in code_rows)
    # отраслевые словари: подпункты со своей областью действия
    chapter_rows = [r for r in rows if r["scope"] == "chapter"]
    assert chapter_rows and all(r["scope_unit_id"] and r["scope_unit_id"].count(".") == 1
                                for r in chapter_rows)
    psn = [r for r in rows if r["definition_unit_id"].startswith("nk2.ch26-5.art346-43.p3.sp")]
    assert len(psn) >= 15 and all(r["scope"] == "point" for r in psn)
    assert all(r["scope_unit_id"] == "nk2.ch26-5.art346-43" for r in psn)
    assert len(rows) >= 55


def test_dictionary_scope():
    assert dictionary_scope("Для целей настоящего Кодекса используются следующие понятия:") == "code"
    assert dictionary_scope("В целях настоящей главы используются следующие понятия:") == "chapter"
    assert dictionary_scope("В целях пункта 2 настоящей статьи используются следующие понятия:") == "point"
    assert dictionary_scope("В целях настоящей статьи понятия и термины:") == "article"
