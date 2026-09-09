"""Тесты резолвера ссылок на данных в формате банка ГАС."""

from taxcorpus.parser import parse_document
from taxcorpus.resolver import UnitIndex, resolve_all

# фрагмент: ч.1 с главой 3.1 (банк: «Глава 31»), ст. 25/25.1/25.13, п. 1/1.1,
# настоящая ст. 61 рядом со сплющенной 6.1, «глава 34» = 3.4
FRAGMENT = """\
Глава 3. НАЛОГОПЛАТЕЛЬЩИКИ

Статья 6. Порядок исчисления сроков

текст статьи 6.

Статья 61. Общие условия отсрочки

текст статьи 61.

Глава 31. Консолидированная группа налогоплательщиков

Статья 25. Организации

текст статьи 25.

Статья 251. Взаимозависимые лица

1. Взаимозависимыми лицами признаются лица.

Статья 2513. Контролируемые иностранные компании

<В ред. Федерального закона от 08 июня 2015 N 150-ФЗ >

1. Контролируемой иностранной компанией признается:

11. Особенности определения доли участия.

Глава 34. КОНТРОЛИРУЕМЫЕ ИНОСТРАННЫЕ КОМПАНИИ
"""


def make_index():
    _, records, _ = parse_document(FRAGMENT, "nk1")
    return UnitIndex(records)


def test_exact_article_number():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "article": "25"}, "nk1.ch3-1.art25")
    assert r.status == "resolved" and r.unit_id == "nk1.ch3-1.art25"


def test_exact_number_wins_over_variant_collision():
    # «61» — и настоящая ст. 61, и вариант сплющенной 6.1: точный номер важнее
    records = [
        {"unit_id": "nk1.ch1.art6-1", "kind": "article", "number": "6.1",
         "parent_unit_id": "nk1.ch1", "title": None},
        {"unit_id": "nk1.ch1.art61", "kind": "article", "number": "61",
         "parent_unit_id": "nk1.ch1", "title": None},
    ]
    idx = UnitIndex(records)
    r = idx.resolve_reference({"type": "unit", "article": "61"}, "nk1.ch1.art61")
    assert r.status == "resolved" and r.unit_id == "nk1.ch1.art61"


def test_flattened_article_number():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "article": "2513"}, "nk1.ch3-1.art25")
    assert r.status == "resolved" and r.unit_id == "nk1.ch3-1.art25-13"


def test_flattened_chapter_number():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "chapter": "34"}, "nk1.ch3.art61")
    assert r.status == "resolved" and r.depth == "chapter"
    # «Глава 34» банка = глава 3.4
    assert r.unit_id == "nk1.ch3-4"


def test_point_and_flattened_point():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "article": "2513", "point": "1"},
                              "nk1.ch3-1.art25")
    assert r.status == "resolved" and r.unit_id == "nk1.ch3-1.art25-13.p1"
    # «пункт 11» в подаче банка = пункт 1.1
    r = idx.resolve_reference({"type": "unit", "article": "2513", "point": "11"},
                              "nk1.ch3-1.art25")
    assert r.status == "resolved" and r.unit_id == "nk1.ch3-1.art25-13.p1-1"


def test_subpoint_and_paragraph():
    idx = make_index()
    _, records, _ = parse_document(FRAGMENT, "nk1")
    # в фрагменте нет подпунктов — проверяем абзац
    r = idx.resolve_reference(
        {"type": "unit", "article": "2513", "point": "1", "paragraph_ordinal": 1},
        "nk1.ch3-1.art25")
    assert r.status == "resolved" and r.unit_id == "nk1.ch3-1.art25-13.p1.ab1"


def test_context_reference_uses_source_article():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "point": "1"},
                              "nk1.ch3-1.art25-13.p1")
    assert r.status == "resolved" and r.unit_id == "nk1.ch3-1.art25-13.p1"


def test_partial_when_point_missing():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "article": "25", "point": "9"},
                              "nk1.ch3.art61")
    assert r.status == "partial" and r.depth == "article"
    assert r.unit_id == "nk1.ch3-1.art25" or r.unit_id == "nk1.ch3.art25"


def test_unresolved_cross_part_article():
    idx = make_index()
    r = idx.resolve_reference({"type": "unit", "article": "284", "point": "2"},
                              "nk1.ch3.art61")
    assert r.status == "unresolved" and r.unit_id is None


def test_external_act_reference():
    idx = make_index()
    r = idx.resolve_reference(
        {"type": "act", "law_date": "27.07.2006", "law_number": "137-ФЗ"},
        "nk1.ch1.art6-1")
    assert r.status == "external"


def test_resolve_all_enriches_records():
    refs = [
        {"from_unit_id": "nk1.ch3-1.art25-13.p1", "kind": "internal_citation",
         "raw_citation": "статьи 2513", "target": {"type": "unit", "article": "2513"}},
        {"from_unit_id": "nk1.ch3-1.art25-13.p1", "kind": "internal_citation",
         "raw_citation": "пунктом 11", "target": {"type": "unit", "point": "11"}},
    ]
    out = resolve_all(_records(), refs)
    assert out[0]["to_unit_id"] == "nk1.ch3-1.art25-13"
    assert out[0]["status"] == "resolved"
    assert out[1]["to_unit_id"] == "nk1.ch3-1.art25-13.p1-1"
    assert out[1]["resolved_depth"] == "point"


def _records():
    _, records, _ = parse_document(FRAGMENT, "nk1")
    return records
