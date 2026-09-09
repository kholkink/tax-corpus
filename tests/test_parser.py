"""Тесты структурного парсера на реалистичном фрагменте НК РФ."""

from pathlib import Path

import pytest

from taxcorpus.parser import parse_document
from taxcorpus.validator import validate

FIXTURE = Path(__file__).parent / "data" / "fragment_nk1.txt"


@pytest.fixture(scope="module")
def records() -> dict[str, dict]:
    raw = FIXTURE.read_text(encoding="utf-8")
    _, records, _ = parse_document(raw, "nk1")
    return {r["unit_id"]: r for r in records}


def test_root_and_sections(records):
    assert records["nk1"]["kind"] == "part"
    assert records["nk1.ri"]["kind"] == "section"
    assert records["nk1.ri"]["number"] == "I"
    assert records["nk1.ri"]["title"] == "ОБЩИЕ ПОЛОЖЕНИЯ"


def test_chapters_caps_headers(records):
    ch1 = records["nk1.ch1"]
    assert ch1["kind"] == "chapter"
    assert ch1["parent_unit_id"] == "nk1.ri"
    assert ch1["title"].startswith("ЗАКОНОДАТЕЛЬСТВО О НАЛОГАХ")
    assert records["nk1.ch2"]["parent_unit_id"] == "nk1.ri"


def test_articles_and_fractional_ids(records):
    assert "nk1.ch1.art1" in records
    assert "nk1.ch1.art2" in records
    # статья 6.1 -> art6-1 в каноническом ID
    assert "nk1.ch1.art6-1" in records
    assert records["nk1.ch1.art6-1"]["number"] == "6.1"
    assert records["nk1.ch1.art6-1"]["label"] == "статья 6.1 НК РФ"
    assert "nk1.ch2.art54-1" in records
    # пропуск: статья 20 «Утратила силу.» присутствует как единица
    assert "nk1.ch2.art20" in records


def test_points_subpoints_paragraphs(records):
    point = records["nk1.ch1.art1.p1"]
    assert point["kind"] == "point"
    assert point["label"] == "пункт 1 статьи 1 НК РФ"

    # подпункты есть у статьи 2: «1) федеральные налоги и сборы;»
    subpoint = records["nk1.ch1.art2.p1.sp1"]
    assert subpoint["kind"] == "subpoint"
    assert subpoint["label"] == "подпункт 1 пункта 1 статьи 2 НК РФ"

    # дробный пункт 2.1 статьи 54.1
    p21 = records["nk1.ch2.art54-1.p2-1"]
    assert p21["kind"] == "point"
    assert p21["number"] == "2.1"
    assert p21["label"] == "пункт 2.1 статьи 54.1 НК РФ"

    # абзац как самостоятельная единица
    ab = records["nk1.ch1.art6-1.p1.ab1"]
    assert ab["kind"] == "paragraph"
    assert ab["text"].startswith("Срок, установленный настоящим Кодексом")
    assert ab["label"] == "абзац первый пункта 1 статьи 6.1 НК РФ"


def test_article_titles(records):
    assert records["nk1.ch2.art21"]["title"] == (
        "Основные обязанности налогоплательщика (плательщика сбора, "
        "плательщика страховых взносов)"
    )
    # статья «Утратила силу.»: заголовка нет, текст есть
    art20 = records["nk1.ch2.art20"]
    assert art20["title"] is None
    assert art20["text"] == "Утратила силу."


def test_edition_note_captured(records):
    assert records["nk1.ch1.art1.p2"]["edit_note"] == (
        "(в ред. Федерального закона от 03.07.2016 № 216-ФЗ)"
    )
    assert "(в ред." not in records["nk1.ch1.art1.p2"]["text"]


def test_text_hash(records):
    assert records["nk1.ch1.art1"]["text_hash"].startswith("sha256:")


def test_document_order(records):
    raw = FIXTURE.read_text(encoding="utf-8")
    _, flat, _ = parse_document(raw, "nk1")
    ids = [r["unit_id"] for r in flat]
    # родители встречаются раньше детей
    assert ids.index("nk1.ch1.art2") < ids.index("nk1.ch1.art2.p1")
    assert ids.index("nk1.ch1.art2.p1") < ids.index("nk1.ch1.art2.p1.sp1")


def test_validation_passes_on_fixture(records):
    report = validate(list(records.values()))
    assert not report.has_errors
    assert any(i.rule == "repealed_units" for i in report.infos)
    assert any(i.rule == "fractional_numbers" for i in report.infos)
    assert report.counts["article"] == 6
    assert report.counts["chapter"] == 2
    assert report.counts["section"] == 1
    assert report.counts["subpoint"] == 7
