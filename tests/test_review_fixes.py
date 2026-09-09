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
