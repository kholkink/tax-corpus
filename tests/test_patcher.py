"""Грамматика изменяющих законов и применитель (F3)."""

from datetime import date

from taxcorpus.parser import parse_document
from taxcorpus.patcher import (apply_instruction, apply_law, effective_date_of_law, parse_instruction, parse_law,
                               rebuild_full_text, roundtrip)
TEXT = """Глава 14. Налоговый контроль

Статья 88. Камеральная налоговая проверка

1. Камеральная налоговая проверка проводится по месту нахождения налогового органа.

2. Камеральная налоговая проверка проводится в течение трех месяцев со дня представления декларации.

3. Требование пояснений.

Статья 89. Выездная налоговая проверка

1. Решение о проведении принимает руководитель налогового органа.
"""

LAW = """Статья 1

Внести в часть первую Налогового кодекса Российской Федерации следующие изменения:

1) в пункте 2 статьи 88 слова "трех месяцев" заменить словами "двух месяцев";

2) статью 88 дополнить пунктом 2.1 следующего содержания:
"2.1. Камеральная проверка декларации по акцизам проводится в течение месяца.";

3) в статье 89:
а) в пункте 1 после слов "Решение о проведении" дополнить словами "выездной налоговой проверки";
б) пункт 1 дополнить абзацем следующего содержания:
"Решение вручается под расписку.";

4) пункт 3 статьи 88 признать утратившим силу;

5) пункт 1 статьи 88 изложить в следующей редакции:
"1. Камеральная налоговая проверка проводится по месту нахождения налогового органа.";

6) в пункте 1 статьи 88 слова "по месту нахождения" исключить.

Статья 2

Настоящий Федеральный закон вступает в силу с 1 января 2027 года.
"""


def _records():
    return {r["unit_id"]: r for r in parse_document(TEXT, "nk1")[1]}


def test_parse_law_instructions_and_addresses():
    ins = parse_law(LAW)
    ops = [(i.operation, i.address.article, i.address.point) for i in ins]
    assert ops == [("replace_words", "88", "2"), ("insert_unit", "88", None), ("insert_words", "89", "1"),
                   ("insert_paragraph", "89", "1"), ("repeal", "88", "3"), ("replace", "88", "1"), ("delete_words", "88", "1")]
    assert ins[0].old == "трех месяцев" and ins[0].new == "двух месяцев"
    assert ins[1].new_number == "2.1" and ins[1].new_kind == "point" and ins[1].new.startswith("2.1.")
    assert ins[2].old == "Решение о проведении" and ins[2].new == "выездной налоговой проверки"
    abz = parse_instruction('в абзаце втором пункта 3 статьи 170 слова "а" заменить словами "б"')
    assert abz.address.paragraph == 2 and abz.address.point == "3" and abz.address.article == "170"
    assert parse_instruction("абзац третий пункта 1 статьи 93 исключить").operation == "delete_paragraph"
    assert parse_instruction("что-то непонятное") is None
    assert effective_date_of_law(LAW) == (date(2027, 1, 1), "по тексту закона")
    d, why = effective_date_of_law("Настоящий Федеральный закон вступает в силу по истечении одного месяца со дня его официального опубликования", date(2026, 8, 4))
    assert d == date(2026, 9, 4) and "месяц" in why
    assert effective_date_of_law("вступает в силу по истечении одного месяца со дня официального опубликования и не ранее 1-го числа очередного налогового периода")[0] is None
    assert effective_date_of_law("без формулировки")[0] is None


def test_apply_law_changes_text_and_full_text():
    records = _records()
    assert "трех месяцев" in records["nk1.ch14.art88.p2"]["text"]
    results = apply_law(records, "nk1", LAW)
    by = {(r.instruction.operation, r.unit_id): r for r in results}
    assert by[("replace_words", "nk1.ch14.art88.p2")].status == "ok" and "двух месяцев" in records["nk1.ch14.art88.p2"]["text"]
    assert "было" in by[("replace_words", "nk1.ch14.art88.p2")].diff
    new = records["nk1.ch14.art88.p2-1"]
    assert new["inserted_by_patch"] and new["parent_unit_id"] == "nk1.ch14.art88" and "акцизам" in new["text"]
    assert records["nk1.ch14.art88.p3"]["repealed_by_patch"] and "Требование пояснений" not in records["nk1.ch14.art88"]["full_text"]
    assert "акцизам" in records["nk1.ch14.art88"]["full_text"] and "двух месяцев" in records["nk1.ch14.art88"]["full_text"]
    p1 = records["nk1.ch14.art89.p1"]
    assert p1["text"].startswith("Решение о проведении выездной налоговой проверки") and p1["paragraphs"][-1] == "Решение вручается под расписку."
    assert records["nk1.ch14.art88.p1"]["text"] == "1. Камеральная налоговая проверка проводится налогового органа."
    assert all(r.status == "ok" for r in results)


def test_failed_instructions_go_to_queue_and_roundtrip():
    records = _records()
    bad = parse_instruction('в пункте 2 статьи 88 слова "нет таких слов" заменить словами "x"')
    r = apply_instruction(records, "nk1", bad)
    assert r.status == "failed" and "0 раз" in r.reason
    r2 = apply_instruction(records, "nk1", parse_instruction("пункт 9 статьи 999 признать утратившим силу"))
    assert r2.status == "failed" and "не найдена" in r2.reason
    r3 = apply_instruction(records, "nk1", parse_instruction("абзац пятый пункта 1 статьи 88 исключить"))
    assert r3.status == "failed" and "нет абзаца" in r3.reason
    base = _records()
    current = _records()
    apply_law(current, "nk1", LAW)
    rt = roundtrip(base, [LAW], "nk1", current)
    assert rt["ok"] == 7 and rt["mismatches"] == [] and rt["failed"] == []
    current["nk1.ch14.art89.p1"]["text"] += " Лишние слова."
    rt2 = roundtrip(base, [LAW], "nk1", current)
    assert [m["kind"] for m in rt2["mismatches"]] == ["text_differs"] and rt2["mismatches"][0]["unit_id"] == "nk1.ch14.art89.p1"
    rebuild_full_text(current)
    assert "Лишние слова" in current["nk1.ch14.art89"]["full_text"]
