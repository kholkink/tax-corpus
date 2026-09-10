"""Тесты исправлений по итогам ревью архитектуры (сентябрь 2026).

1. Пометка «<Утратил силу …>» в одном абзаце с маркером — это edit_note, не текст.
2. Дата вступления правки в силу извлекается; repeal распознаётся раньше «в ред.».
3. Несколько пометок у одной единицы накапливаются, а не перезаписываются.
4. «статьи 10 Федерального закона …» — не внутренняя ссылка на НК.
5. Резолвер работает по индексу всего корпуса (ссылки ч.1 -> ч.2).
6. Валидатор не ругается на статью из одних пунктов и на отменённую единицу.
"""

from taxcorpus.amendments import (amendments_from_note, effective_date_of, operation_of,
                                  repeal_dates, scope_of)
from taxcorpus.parser import parse_document
from taxcorpus.references import extract_references
from taxcorpus.resolver import UnitIndex, resolve_all, resolve_citation
from taxcorpus.validator import validate

REPEALED_POINT = """Статья 10. Порядок производства по делам

1. Порядок привлечения к ответственности устанавливается настоящей статьей.

2. Производство по делам ведется в порядке, установленном законодательством.

<В ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ>

<Изменен Федеральным законом от 29 июня 2004 г. N 58-ФЗ>

3. <Утратил силу с 1 августа 2004 г.: Федеральный закон от 29 июня 2004 г. N 58-ФЗ>

Статья 11. Институты, понятия и термины

1. Институты, понятия и термины применяются в том значении, в каком они используются.
"""


def _records(text: str, act: str = "nk1") -> list[dict]:
    _, records, _ = parse_document(text, act)
    return records


def test_repeal_note_in_marker_paragraph_becomes_edit_note():
    by_id = {r["unit_id"]: r for r in _records(REPEALED_POINT)}
    point = by_id["nk1.art10.p3"]
    assert point["text"] == ""
    assert point["edit_note"].startswith("<Утратил силу с 1 августа 2004 г.")
    # у отменённого пункта нет дочернего абзаца с текстом пометки
    assert "nk1.art10.p3.ab1" not in by_id


def test_multiple_notes_accumulate():
    by_id = {r["unit_id"]: r for r in _records(REPEALED_POINT)}
    notes = by_id["nk1.art10.p2"]["edit_note"].split("\n")
    assert len(notes) == 2
    assert notes[0].startswith("<В ред.")
    assert notes[1].startswith("<Изменен")


def test_effective_date_and_repeal_operation():
    note = "<Утратил силу с 1 января 2023 г.: Федеральный закон от 14 июля 2022 N 263-ФЗ>"
    assert effective_date_of(note).isoformat() == "2023-01-01"
    assert effective_date_of("<Утратил силу со 2 августа 2021 г.: …>").isoformat() == "2021-08-02"
    assert effective_date_of("<В ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ>") is None
    # repeal стоит раньше «в ред.» -> repeal, а не replace
    assert operation_of("<Утратил силу с 1 января 2023 г.; в ред. ФЗ …>") == "repeal"
    rows = amendments_from_note("nk1.ch14.art88.p13", note)
    assert rows == [{
        "target_unit_id": "nk1.ch14.art88.p13", "scope": "unit", "operation": "repeal",
        "amending_act_number": "263-ФЗ", "amending_act_date": "2022-07-14",
        "effective_date": "2023-01-01", "raw_note": note,
    }]


def test_amendments_from_multiline_note_and_repeal_dates():
    note = ("<В ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ>\n"
            "<Утратил силу с 1 августа 2004 г.: Федеральный закон от 29 июня 2004 г. N 58-ФЗ>")
    rows = amendments_from_note("u", note)
    assert [r["operation"] for r in rows] == ["replace", "repeal"]
    assert repeal_dates(rows) == {"u": "2004-08-01"}
    assert repeal_dates([{"target_unit_id": "v", "operation": "repeal", "effective_date": None}]) \
        == {"v": None}


def test_foreign_act_article_is_not_internal_citation():
    text = ("в соответствии со статьей 10 Федерального закона \"О защите и поощрении "
            "капиталовложений\" и пунктом 2 статьи 15 настоящего Кодекса")
    records = extract_references("nk1.ch1.art5.p4-3", text)
    kinds = [(r["kind"], r["target"].get("article")) for r in records]
    assert ("internal_citation", "15") in kinds
    assert ("internal_citation", "10") not in kinds
    foreign = next(r for r in records if r["kind"] == "external_act_unit")
    assert foreign["target"]["type"] == "act"
    assert foreign["target"]["cited_unit"] == "статьей 10"
    assert foreign["confidence"] < 1.0
    # индекс по ч.1: цель типа act -> external, а не ложный resolved
    index = UnitIndex(_records(REPEALED_POINT))
    assert index.resolve_reference(foreign["target"], "nk1.ch1.art5.p4-3").status == "external"


def test_other_codes_are_foreign_too():
    text = "согласно статье 395 Гражданского кодекса Российской Федерации"
    records = extract_references("nk1.ch11.art75.p4", text)
    assert [r["kind"] for r in records] == ["external_act_unit"]


def test_cross_part_reference_resolves_with_corpus_index():
    part1 = _records(REPEALED_POINT, "nk1")
    part2 = _records("Глава 25. Налог на прибыль\n\nСтатья 284. Ставки\n\n1. Ставка 20 процентов.\n",
                     "nk2")
    refs = [
        {"from_unit_id": "nk1.art10.p1", "kind": "internal_citation", "raw_citation": "статьи 284",
         "target": {"type": "unit", "article": "284"}},
        {"from_unit_id": "nk1.art10.p1", "kind": "internal_citation", "raw_citation": "главы 25",
         "target": {"type": "unit", "chapter": "25"}},
    ]
    alone = resolve_all(part1, refs)
    assert [r["status"] for r in alone] == ["unresolved", "unresolved"]
    joint = resolve_all(part1, refs, index_records=[*part1, *part2])
    assert [r["to_unit_id"] for r in joint] == ["nk2.ch25.art284", "nk2.ch25"]


def test_resolve_citation_context_is_explicit():
    index = UnitIndex(_records(REPEALED_POINT))
    assert resolve_citation("п. 1 ст. 11", index).unit_id == "nk1.art11.p1"
    # без контекста цитата без статьи не резолвится, с контекстом — относительно статьи
    assert resolve_citation("п. 2", index).status == "unresolved"
    assert resolve_citation("п. 2", index, from_unit_id="nk1.art10.p1").unit_id == "nk1.art10.p2"


def test_validator_ignores_expected_empty_text():
    report = validate(_records(REPEALED_POINT))
    empty = [i.unit_id for i in report.warnings if i.rule == "empty_text"]
    assert "nk1.art10" not in empty      # статья из одних пунктов
    assert "nk1.art10.p3" not in empty   # отменённый пункт с пометкой


def test_child_scope_repeal_does_not_close_parent():
    note = "<Абзац пятый утратил силу: Федеральный закон от 31 июля 2023 N 389-ФЗ >"
    assert scope_of(note) == "child"
    assert scope_of("<Утратил силу с 1 января 2023 г.: …>") == "unit"
    rows = amendments_from_note("nk1.ch14.art88.p2", note)
    assert rows[0]["operation"] == "repeal" and rows[0]["scope"] == "child"
    assert repeal_dates(rows) == {}  # пункт продолжает действовать, отменён лишь абзац


def test_note_without_closing_bracket_is_still_a_note():
    text = ("Статья 204. Сроки\n\n1. Уплата акциза производится.\n\n"
            "2. <Утратил силу с 1 июля 2026 г.: Федеральный закон от 28 ноября 2025 N 425-ФЗ\n")
    by_id = {r["unit_id"]: r for r in _records(text, "nk2")}
    assert by_id["nk2.art204.p2"]["text"] == ""
    assert by_id["nk2.art204.p2"]["edit_note"].startswith("<Утратил силу с 1 июля 2026")


def test_duplicate_marker_does_not_make_number_ambiguous():
    text = ("Статья 227. Особенности\n\n1. Текст.\n\n"
            "Статья 2271. Особенности\n\n1. Первый.\n\n"
            "Статья 2271. Особенности\n\n1. Повтор.\n\n"
            "Статья 228. Далее\n\n1. Текст.\n")
    records = _records(text, "nk2")
    assert any(r["duplicate_of"] == "nk2.art227-1" for r in records)
    index = UnitIndex(records)
    assert resolve_citation("ст. 227.1", index).unit_id == "nk2.art227-1"


def test_detokenize_after_gap_uses_next_integer_prefix():
    text = ("Статья 309. Тест\n\n1. Доходы:\n\n8) восемь;\n\n91) девять-один;\n\n"
            "95) девять-пять;\n\n10) десять.\n")
    numbers = [r["number"] for r in _records(text, "nk2") if r["kind"] == "subpoint"]
    assert numbers == ["8", "9.1", "9.5", "10"]


def test_lost_point_one_is_inferred():
    text = ("Статья 150. Тест\n\nНе подлежит налогообложению ввоз:\n\n1) товаров;\n\n"
            "2) следующих товаров;\n\n"
            "2. <Утратил силу с 1 января 2004 г.: Федеральный закон от 07 июля 2003 N 117-ФЗ>\n")
    by_id = {r["unit_id"]: r for r in _records(text, "nk2")}
    assert by_id["nk2.art150.p1"]["inferred"] is True
    assert by_id["nk2.art150.p1"]["text"] == "Не подлежит налогообложению ввоз:"
    assert "nk2.art150.p1.sp1" in by_id and "nk2.art150.p1.sp2" in by_id
    assert by_id["nk2.art150.p2"]["edit_note"].startswith("<Утратил силу")
    assert not any("@" in uid for uid in by_id)


def test_coordinate_rows_are_not_points():
    text = ("Статья 333.45. Тест\n\n1. Объект:\n\n4) участок севернее:\n\n"
            "1. 61 53 00; 75 02 00;\n\n2. 62 00 00; 75 02 00;\n")
    recs = _records(text, "nk2")
    assert [r["number"] for r in recs if r["kind"] == "point"] == ["1"]
    sp = next(r for r in recs if r["kind"] == "subpoint")
    assert "61 53 00" in sp["text"]


def test_effective_date_is_per_law():
    note = ("<В ред. Федерального закона от 31 июля 2020 N 266-ФЗ (изменения вступают в силу "
            "с 1 января 2021 г.), Федерального закона от 28 декабря 2022 N 564-ФЗ , "
            "Федерального закона от 12 июля 2024 N 176-ФЗ (изменения вступают в силу с 1 января 2025 г.)>")
    rows = amendments_from_note("u", note)
    assert [(r["amending_act_number"], r["effective_date"]) for r in rows] == [
        ("266-ФЗ", "2021-01-01"), ("564-ФЗ", None), ("176-ФЗ", "2025-01-01")]
    repeal = amendments_from_note("u", "<Утратил силу с 1 января 2023 г.: Федеральный закон от 14 июля 2022 N 263-ФЗ>")
    assert repeal[0]["effective_date"] == "2023-01-01"


CONTEXT_TEXT = """Статья 10. Тест

1. Первый абзац пункта один.

Второй абзац пункта один.

2. Пункт два:

1) подпункт один;

2) подпункт два.

3. Пункт три.
"""


def test_reference_captures_relative_context():
    recs = extract_references("nk1.art10.p1", "указанных в абзаце втором настоящего пункта, и в пункте 3 настоящей статьи")
    assert recs[0]["target"] == {"type": "unit", "paragraph_ordinal": 2, "relative_to": "point"}
    assert recs[1]["target"] == {"type": "unit", "point": "3", "relative_to": "article"}
    plain = extract_references("nk1.art10.p1", "в соответствии с пунктом 2 статьи 5 настоящего Кодекса")
    assert "relative_to" not in plain[0]["target"]


def test_contextual_resolution_uses_source_ancestors():
    index = UnitIndex(_records(CONTEXT_TEXT))
    r = index.resolve_reference({"type": "unit", "paragraph_ordinal": 2, "relative_to": "point"},
                                "nk1.art10.p1.ab1")
    assert (r.unit_id, r.status) == ("nk1.art10.p1.ab2", "resolved")
    # подпункт без пункта: по умолчанию — в пункте-источнике
    r = index.resolve_reference({"type": "unit", "subpoint": "1"}, "nk1.art10.p2.sp2")
    assert (r.unit_id, r.status) == ("nk1.art10.p2.sp1", "resolved")
    # пункт без статьи — в статье-источнике
    r = index.resolve_reference({"type": "unit", "point": "3", "relative_to": "article"}, "nk1.art10.p1")
    assert r.unit_id == "nk1.art10.p3"
    # абзац из текста, не принадлежащего пункту, — относительно статьи
    r = index.resolve_reference({"type": "unit", "paragraph_ordinal": 9}, "nk1.art10")
    assert r.status == "partial" and r.unit_id == "nk1.art10"


def test_ranges_and_ordinal_lists_in_references():
    recs = extract_references("u", "подпунктами 1 - 3 и 7 пункта 2 статьи 5")
    assert [r["target"]["subpoint"] for r in recs] == ["1", "2", "3", "7"]
    assert all(r["target"]["point"] == "2" and r["target"]["article"] == "5" for r in recs)
    recs = extract_references("u", "в абзацах втором - четвертом и шестом настоящего пункта")
    assert [r["target"]["paragraph_ordinal"] for r in recs] == [2, 3, 4, 6]
    assert all(r["target"]["relative_to"] == "point" for r in recs)
    recs = extract_references("u", "в абзацах втором и третьем пункта 1 настоящей статьи")
    assert [(r["target"]["paragraph_ordinal"], r["target"]["point"]) for r in recs] == [(2, "1"), (3, "1")]


def test_paragraph_counts_subpoint_lines():
    index = UnitIndex(_records(CONTEXT_TEXT))
    # п. 2: строка 1 — «Пункт два:», строки 2 и 3 — подпункты 1) и 2)
    r = index.resolve_reference({"type": "unit", "point": "2", "paragraph_ordinal": 3}, "nk1.art10.p1")
    assert (r.unit_id, r.status) == ("nk1.art10.p2.sp2", "resolved")
    r = index.resolve_reference({"type": "unit", "point": "2", "paragraph_ordinal": 1}, "nk1.art10.p1")
    assert r.unit_id == "nk1.art10.p2.ab1"
    r = index.resolve_reference({"type": "unit", "point": "2", "paragraph_ordinal": 9}, "nk1.art10.p1")
    assert r.status == "partial"
