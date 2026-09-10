"""Извлечение и проверка цитат в свободном тексте (шаг 5 пайплайна агента)."""

import json
from pathlib import Path

import pytest

from taxcorpus.citations import CitationVerifier, extract_citations
from taxcorpus.parser import parse_document

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed"

TEXT = """Статья 10. Порядок

1. Первый пункт.

2. Второй пункт:

1) один;

2) два.

3. <Утратил силу с 1 января 2020 г.: Федеральный закон от 29 сентября 2019 N 325-ФЗ>

Статья 11. Понятия

1. Первый абзац.

Второй абзац.
"""


def test_extract_citations_variants():
    items = extract_citations(
        "Согласно подп. 2 п. 2 ст. 10 НК РФ и пункту 1 статьи 11 Кодекса, а также ст. 6.1 НК, "
        "абзац второй п. 1 ст. 11 и пп. 4 п. 1 ст. 218 Налогового кодекса Российской Федерации.")
    assert [i["article"] for i in items] == ["10", "11", "6.1", "11", "218"]
    first = {k: v for k, v in items[0].items() if k not in ("start", "end")}
    assert first == {"raw": "подп. 2 п. 2 ст. 10 НК РФ", "article": "10", "point": "2", "subpoint": "2"}
    assert items[0]["start"] == 9 and items[0]["end"] == 34
    assert items[1]["point"] == "1" and items[1]["raw"].endswith("Кодекса")
    assert items[3]["paragraph_ordinal"] == 2
    assert items[4]["subpoint"] == "4"
    # «ст. 2 закона» без слов-маркеров тоже извлекается — проверка отбросит, если такой статьи нет
    assert extract_citations("в 2025 г. ставка 20 процентов") == []


def test_verify_marks_ok_stale_and_missing():
    _, records, _ = parse_document(TEXT, "nk1")
    v = CitationVerifier(records, {"nk1": "2026-08-04"})
    rep = v.verify("см. подп. 2 п. 2 ст. 10 НК РФ, п. 3 ст. 10 НК РФ, п. 9 ст. 10, ст. 99 НК РФ, "
                   "абзац второй п. 1 ст. 11", "2026-09-10")
    statuses = [(c.raw, c.status, c.unit_id) for c in rep.checks]
    assert statuses[0] == ("подп. 2 п. 2 ст. 10 НК РФ", "ok", "nk1.art10.p2.sp2")
    assert statuses[1] == ("п. 3 ст. 10 НК РФ", "not_in_force", "nk1.art10.p3")
    assert statuses[2] == ("п. 9 ст. 10", "partial", "nk1.art10")
    assert statuses[3] == ("ст. 99 НК РФ", "unresolved", None)
    assert statuses[4] == ("абзац второй п. 1 ст. 11", "ok", "nk1.art11.p1.ab2")
    assert not rep.ok and len(rep.problems) == 3
    # до отмены п. 3 действовал
    assert v.verify("п. 3 ст. 10 НК РФ", "2019-06-01").ok
    # неотменённая единица на дату раньше редакции — не подтверждена
    assert not v.verify("п. 1 ст. 10 НК РФ", "2000-01-01").ok
    assert "STALE" in rep.render() and "MISS" in rep.render()


@pytest.mark.skipif(len(list(DATA.glob("*_units.jsonl"))) < 2, reason="корпус не сгенерирован")
def test_verify_against_corpus_golden_citations():
    records = []
    editions = {}
    for path in sorted(DATA.glob("*_units.jsonl")):
        act = path.name.split("_")[0]
        editions[act] = json.loads((DATA / f"{act}_meta.json").read_text(encoding="utf-8"))["valid_from"]
        with path.open(encoding="utf-8") as fh:
            records.extend(json.loads(l) for l in fh if l.strip())
    v = CitationVerifier(records, editions)
    rep = v.verify("Камеральная проверка — п. 2 ст. 88 НК РФ; вычет на ребёнка — подп. 4 п. 1 ст. 218 НК РФ; "
                   "ставка ПСН — п. 1 ст. 346.50 НК РФ; п. 18.1 ст. 217 НК РФ; сроки — ст. 6.1 НК РФ.",
                   "2026-09-10")
    assert rep.ok, rep.render()
    assert [c.unit_id for c in rep.checks] == [
        "nk1.ch14.art88.p2", "nk2.ch23.art218.p1.sp4", "nk2.ch26-5.art346-50.p1",
        "nk2.ch23.art217.p18-1", "nk1.ch1.art6-1"]
    # отменённый п. 3 ст. 10 — STALE на сегодня
    assert v.verify("п. 3 ст. 10 НК РФ", "2026-09-10").checks[0].status == "not_in_force"
