"""Тесты извлечения правок (Amendment) из пометок редакции банка."""

from taxcorpus.amendments import (
    amendments_from_note,
    amendments_from_records,
    law_pairs,
    operation_of,
    parse_word_date,
)


def test_word_dates():
    assert parse_word_date("от 27 июля 2006 г. N 137-ФЗ").isoformat() == "2006-07-27"
    # год без «г.» — частый случай банка
    assert parse_word_date("от 08 июня 2015 N 150-ФЗ").isoformat() == "2015-06-08"
    assert parse_word_date("от 4 ноября 2014 года").isoformat() == "2014-11-04"
    assert parse_word_date("без даты") is None


def test_law_pairs_multiple_laws():
    note = ("<В ред. Федерального закона от 23 ноября 2020 N 374-ФЗ (изменения вступают "
            "в силу с 23 декабря 2020 г.), Федерального закона от 19 ноября 2021 N 371-ФЗ >")
    pairs = law_pairs(note)
    assert pairs == [("374-ФЗ", "2020-11-23"), ("371-ФЗ", "2021-11-19")]


def test_law_pairs_numeric_dates():
    pairs = law_pairs("<В ред. Федерального закона от 23.11.2020 N 374-ФЗ>")
    assert pairs == [("374-ФЗ", "2020-11-23")]


def test_operations():
    assert operation_of("<В новой ред. Федерального закона от ...>") == "replace"
    assert operation_of("<Введена Федеральным законом от ...>") == "insert"
    assert operation_of("<Дополнен пунктом 7.1...>") == "insert"
    assert operation_of("<Наименование в ред. ...>") == "title_change"
    assert operation_of("<Утратил силу с 1 января 2023 г.>") == "repeal"


def test_amendments_from_note():
    rows = amendments_from_note(
        "nk1.ch1.art6-1", "<В новой ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ >")
    assert len(rows) == 1
    assert rows[0] == {
        "target_unit_id": "nk1.ch1.art6-1",
        "scope": "unit",
        "operation": "replace",
        "amending_act_number": "137-ФЗ",
        "amending_act_date": "2006-07-27",
        "effective_date": None,
        "raw_note": "<В новой ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ >",
    }


def test_act_changelog_skipped():
    assert amendments_from_note("nk1", "<Изменения: Федеральный закон от ...>") == []


def test_amendments_from_records():
    records = [
        {"unit_id": "nk1.ch1.art6-1",
         "edit_note": "<В новой ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ >"},
        {"unit_id": "nk1.ch2.art11-2",
         "edit_note": "<Введена Федеральным законом от 04 ноября 2014 N 347-ФЗ >"},
        {"unit_id": "nk1.ch1.art1", "edit_note": None},
    ]
    rows = amendments_from_records(records)
    assert [(r["target_unit_id"], r["operation"]) for r in rows] == [
        ("nk1.ch1.art6-1", "replace"),
        ("nk1.ch2.art11-2", "insert"),
    ]
