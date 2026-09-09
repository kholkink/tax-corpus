"""Тесты извлечения ссылок на реальных фразах НК РФ."""

import pytest

from taxcorpus.references import extract_references

UNIT_ID = "nk1.ch2.art21.p1"


def refs(text: str) -> list[dict]:
    return extract_references(UNIT_ID, text)


def one(text: str) -> dict:
    records = refs(text)
    assert len(records) == 1, records
    return records[0]


# --- внутренние ссылки ---


def test_point_of_article():
    rec = one("Налогоплательщик обязан в соответствии с пунктом 2 статьи 15 "
              "представить документы.")
    assert rec["kind"] == "internal_citation"
    assert rec["raw_citation"] == "пунктом 2 статьи 15"
    assert rec["target"] == {"type": "unit", "article": "15", "point": "2"}


def test_subpoint_point_article():
    rec = one("Суммы учитываются согласно подпункту 7 пункта 3 статьи 170.")
    assert rec["target"] == {
        "type": "unit", "article": "170", "point": "3", "subpoint": "7",
    }


def test_article_declensions_and_fractional():
    assert one("Понятия применяются в значении статьи 11.")["target"]["article"] == "11"
    rec = one("Порядок контроля установлен статьей 54.1.")
    assert rec["target"]["article"] == "54.1"


def test_fractional_point():
    rec = one("Проверка проводится с учетом пункта 2.1 статьи 54.1.")
    assert rec["target"]["article"] == "54.1"
    assert rec["target"]["point"] == "2.1"


def test_chapter_reference():
    rec = one("Особенности исчисления установлены главой 21 настоящего Кодекса.")
    assert rec["target"]["chapter"] == "21"


def test_section_roman():
    rec = one("Общие положения определены разделом VII.")
    assert rec["target"]["section"] == "VII"


def test_article_with_code_suffix():
    rec = one("Налоговая база определяется согласно пункту 3 статьи 164 "
              "настоящего Кодекса.")
    assert rec["target"] == {"type": "unit", "article": "164", "point": "3"}


def test_paragraph_ordinal_word():
    rec = one("Доля определяется в абзаце втором пункта 1 статьи 346.19.")
    target = rec["target"]
    assert target["article"] == "346.19"
    assert target["point"] == "1"
    assert target["paragraph_ordinal"] == 2
    assert isinstance(target["paragraph_ordinal"], int)


def test_paragraph_ordinal_compound():
    rec = one("Правило закреплено в абзаце двадцать первом настоящей статьи.")
    assert rec["target"] == {"type": "unit", "paragraph_ordinal": 21}


def test_coordinate_list_and():
    records = refs("Особенности установлены пунктами 1 и 3 статьи 45.")
    assert len(records) == 2
    assert {r["target"]["point"] for r in records} == {"1", "3"}
    assert all(r["target"]["article"] == "45" for r in records)
    assert len({r["raw_citation"] for r in records}) == 1


def test_coordinate_list_commas():
    records = refs("Налог исчисляется по правилам пунктов 1, 2 и 4 статьи 45.")
    assert len(records) == 3
    assert [r["target"]["point"] for r in records] == ["1", "2", "4"]


def test_subpoint_coordinate_list():
    records = refs("Освобождение применяется в подпунктах 1 и 2 пункта 1 статьи 23.")
    assert len(records) == 2
    assert {r["target"]["subpoint"] for r in records} == {"1", "2"}
    assert all(r["target"]["point"] == "1" for r in records)
    assert all(r["target"]["article"] == "23" for r in records)


def test_article_list():
    records = refs("Отношения регулируются статьями 5 и 6 настоящего Кодекса.")
    assert [r["target"]["article"] for r in records] == ["5", "6"]


# --- внешние ссылки ---


def test_federal_law_single():
    rec = one("Положение применяется с учетом изменений, внесенных "
              "Федеральным законом от 23.11.2020 № 374-ФЗ.")
    assert rec["kind"] == "external_federal_law"
    assert rec["target"] == {"type": "act", "law_date": "23.11.2020",
                             "law_number": "374-ФЗ"}


def test_federal_law_two():
    records = refs("Изменения внесены Федеральными законами от 26.07.2019 № 210-ФЗ "
                   "и от 29.09.2019 № 325-ФЗ.")
    assert len(records) == 2
    assert [r["target"]["law_number"] for r in records] == ["210-ФЗ", "325-ФЗ"]
    assert [r["target"]["law_date"] for r in records] == ["26.07.2019", "29.09.2019"]
    assert len({r["raw_citation"] for r in records}) == 1


def test_gov_decree_full_name():
    rec = one("Порядок утвержден Постановлением Правительства Российской Федерации "
              "от 01.02.2024 № 100.")
    assert rec["kind"] == "external_gov_decree"
    assert rec["target"] == {"type": "act", "law_date": "01.02.2024",
                             "law_number": "100"}


def test_gov_decree_short_rf():
    rec = one("Список территорий утвержден постановлением Правительства РФ "
              "от 30.09.2015 № 1044.")
    assert rec["target"]["law_number"] == "1044"
    assert rec["target"]["law_date"] == "30.09.2015"


def test_agency_order_fns():
    rec = one("Форматы утверждены приказом ФНС России от 31.07.2014 № ММВ-7-6/398@.")
    assert rec["kind"] == "external_agency_act"
    assert rec["target"] == {"type": "act", "law_date": "31.07.2014",
                             "law_number": "ММВ-7-6/398@", "agency": "ФНС"}


def test_agency_letter_minfin():
    rec = one("Согласно письму Минфина России от 15.02.2024 № 03-04-05/1234 "
              "расходы учитываются.")
    assert rec["target"]["agency"] == "Минфин"
    assert rec["target"]["law_number"] == "03-04-05/1234"
    assert rec["target"]["law_date"] == "15.02.2024"


# --- отрицательные случаи ---


@pytest.mark.parametrize("text", [
    "Налог исчисляется с 1 января 2025 года.",
    "Срок считается в течение 3 лет со дня получения требований.",
    "Ставка налога составляет 10 процентов.",
    "Декларация представляется не позднее 28 числа.",
    "Подробности: см. ст. 5 того же кодекса.",
    "Изменения внесены Федеральным законом № 374-ФЗ.",
    "Текст приведен на странице 45 издания.",
])
def test_negative_phrases(text):
    assert refs(text) == []


# --- форма записи и общий текст ---


def test_record_shape():
    records = refs("Обязанности установлены подпунктом 1 пункта 1 статьи 23, "
                   "а порядок внесения изменений определен Федеральным законом "
                   "от 23.11.2020 № 374-ФЗ.")
    assert len(records) == 2
    for rec in records:
        assert set(rec) == {"from_unit_id", "kind", "raw_citation", "target",
                            "extracted_by", "confidence"}
        assert rec["from_unit_id"] == UNIT_ID
        assert rec["extracted_by"] == "regex"
        assert rec["confidence"] == 1.0
        assert isinstance(rec["raw_citation"], str) and rec["raw_citation"]
        assert isinstance(rec["target"], dict) and rec["target"]


def test_multi_paragraph_text():
    text = ("Обязанности налогоплательщика установлены статьей 23.\n\n"
            "Сроки определяются согласно пункту 2 статьи 6.1.")
    records = refs(text)
    assert [(r["target"]["article"], r["target"].get("point")) for r in records] == [
        ("23", None), ("6.1", "2"),
    ]


def test_kinds_in_text_order():
    text = ("Форматы утверждены приказом ФНС России от 31.07.2014 № ММВ-7-6/398@, "
            "применяются с учетом пункта 2 статьи 15.")
    records = refs(text)
    assert [r["kind"] for r in records] == ["external_agency_act", "internal_citation"]
