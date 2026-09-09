"""Эталон v0 против разобранного корпуса (без БД): ожидаемая единица существует,
её полный текст содержит якорную фразу, цитаты резолвятся в ожидаемый ID.

Пропускается, если данные не сгенерированы (data/processed/*_units.jsonl — не в git;
команда: python -m taxcorpus parse …). Поисковые и as_of-вопросы гоняются против БД
скриптом scripts/build_golden.py.
"""

import json
from pathlib import Path

import pytest

from taxcorpus.amendments import amendments_from_records, repeal_dates
from taxcorpus.resolver import UnitIndex, resolve_citation

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests" / "golden" / "golden_v0.json").read_text(encoding="utf-8"))
DATA = ROOT / "data" / "processed"
UNIT_FILES = sorted(DATA.glob("*_units.jsonl"))

pytestmark = pytest.mark.skipif(len(UNIT_FILES) < 2, reason="корпус не сгенерирован (parse)")


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


@pytest.mark.parametrize("q", [q for q in GOLDEN["questions"] if q["kind"] == "search"],
                         ids=lambda q: q["id"])
def test_expected_unit_holds_anchor(q, corpus):
    for unit_id in q["expected"]:
        assert unit_id in corpus, f"{q['id']}: единицы {unit_id} нет в корпусе"
        text = _norm(corpus[unit_id].get("full_text") or corpus[unit_id]["text"])
        assert _norm(q["anchor"]) in text, f"{q['id']}: якорь не найден в {unit_id}"
        assert corpus[unit_id]["is_chunk"], f"{q['id']}: {unit_id} не является чанком поиска"


@pytest.mark.parametrize("q", [q for q in GOLDEN["questions"] if q["kind"] == "resolve"],
                         ids=lambda q: q["id"])
def test_citation_resolves(q, corpus):
    index = UnitIndex(list(corpus.values()))
    res = resolve_citation(q["citation"], index)
    assert res.unit_id in q["expected"], f"{q['id']}: {q['citation']} -> {res}"


@pytest.mark.parametrize("q", [q for q in GOLDEN["questions"] if q["kind"].startswith("as_of")],
                         ids=lambda q: q["id"])
def test_as_of_interval_from_repeal_notes(q, corpus):
    """Интервал действия, который построит загрузчик: valid_from = дата редакции,
    у отменённых — valid_to = дата утраты силы (см. db.load_corpus)."""
    unit = corpus[q["unit_id"]]
    edition_from = json.loads((DATA / f"{unit['act']}_meta.json").read_text(encoding="utf-8"))["valid_from"]
    repealed = repeal_dates(amendments_from_records([unit]))
    if q["unit_id"] in repealed:
        valid_from, valid_to = None, repealed[q["unit_id"]] or edition_from
    else:
        valid_from, valid_to = edition_from, None
    present = (valid_from is None or valid_from <= q["as_of"]) and (valid_to is None or valid_to > q["as_of"])
    assert present == (q["kind"] == "as_of_present"), f"{q['id']}: [{valid_from}, {valid_to}) на {q['as_of']}"
