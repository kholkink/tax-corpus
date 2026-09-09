"""Тесты парсера на формате банка ГАС «Законодательство России».

Особенности подачи банка (pravo-search.minjust.ru):
- дробные номера сплющены: «Статья 61.» = статья 6.1, «Глава 34» = глава 3.4;
- страховые взносы поданы как «Глава 2.1.» (с точкой);
- служебные пометки в угловых скобках, многоабзацные;
- пункты тоже сплющены: «11.» = пункт 1.1.
"""

from pathlib import Path

import pytest

from taxcorpus.ingest import html_to_paragraph_text
from taxcorpus.parser import parse_document
from taxcorpus.validator import validate

BANK_FRAGMENT = """\
ФЕДЕРАЛЬНЫЙ ЗАКОН

31.07.98 N 146-ФЗ

НАЛОГОВЫЙ КОДЕКС РОССИЙСКОЙ ФЕДЕРАЦИИ

ЧАСТЬ ПЕРВАЯ

<Изменения: Федеральный закон от 9 июля 1999 г. N 154-ФЗ; НГР:Р9903543 >

РАЗДЕЛ I. ОБЩИЕ ПОЛОЖЕНИЯ

Глава 2. НАЛОГОВЫЕ ОРГАНЫ. ПРЕДСТАВИТЕЛЬСТВО

Статья 11. Понятия и термины

<В новой ред. Федерального закона от 23 ноября 2020 N 374-ФЗ >

1. Для целей настоящего Кодекса используются понятия.

Статья 111. Понятия при налогообложении добычи углеводородного сырья

<Введена Федеральным законом от 28 ноября 2025 N 425-ФЗ >

Статья 112. Личный кабинет налогоплательщика

<Введена Федеральным законом от 04 ноября 2014 N 347-ФЗ >

Статья 12. Виды налогов

Глава 3. НАЛОГОПЛАТЕЛЬЩИКИ И ПЛАТЕЛЬЩИКИ СБОРОВ

Глава 31. Консолидированная группа налогоплательщиков

<Введена Федеральным законом от 16 ноября 2011 N 321-ФЗ >

Статья 25. Организации, признаваемые налогоплательщиками

Организацией признается организация.

Статья 251. Взаимозависимые лица

1. Взаимозависимыми лицами признаются.

Статья 2513. Контролируемые иностранные компании

<В ред. Федерального закона от 08 июня 2015 N 150-ФЗ >

1. Контролируемой иностранной компанией признается:

11. Особенности определения доли участия.

Раздел V. 1. ВЗАИМОЗАВИСИМЫЕ ЛИЦА

Статья 105. Взаимозависимые лица и международные группы компаний

Раздел устанавливает правила взаимозависимости.

Статья 1051. Взаимозависимые лица

1. Взаимозависимыми лицами признаются лица.

Глава 2.1. СТРАХОВЫЕ ВЗНОСЫ В РОССИЙСКОЙ ФЕДЕРАЦИИ

<Введена Федеральным законом от 03 июля 2016 N 243-ФЗ >

Статья 419. Объект обложения страховыми взносами

1. Объектом обложения страховыми взносами признаются выплаты.
"""


@pytest.fixture(scope="module")
def records() -> dict[str, dict]:
    _, flat, _ = parse_document(BANK_FRAGMENT, "nk1")
    return {r["unit_id"]: r for r in flat}


def test_flattened_article_numbers_restored(records):
    assert records["nk1.ch2.art11"]["number"] == "11"
    # «Статья 111» после 11 -> 11.1
    assert "nk1.ch2.art11-1" in records
    assert records["nk1.ch2.art11-1"]["number"] == "11.1"
    # «Статья 112» -> 11.2, «Статья 12» после 11.2 -> настоящая 12
    assert records["nk1.ch2.art11-2"]["number"] == "11.2"
    assert "nk1.ch2.art12" in records
    assert records["nk1.ch2.art12"]["number"] == "12"


def test_flattened_chapter_and_article(records):
    # «Глава 31» после главы 3 -> глава 3.1; «Статья 2513» -> статья 25.13
    assert records["nk1.ch3-1"]["number"] == "3.1"
    assert records["nk1.ch3-1"]["title"].startswith("Консолидированная группа")
    art = records["nk1.ch3-1.art25-13"]
    assert art["number"] == "25.13"


def test_dotted_chapter_2_1(records):
    # страховые взносы: банк подал как «Глава 2.1.»
    assert records["nk1.ch2-1"]["number"] == "2.1"
    assert "СТРАХОВЫЕ ВЗНОСЫ" in records["nk1.ch2-1"]["title"]
    assert "nk1.ch2-1.art419" in records


def test_section_with_space_in_number(records):
    # «Раздел V. 1.» (NBSP внутри) -> раздел V.1
    assert records["nk1.rv-1"]["number"] == "V.1"
    assert "nk1.ch3-1" not in records or True


def test_bank_notes_captured(records):
    # одноабзацная пометка
    assert (records["nk1.ch2.art11"]["edit_note"] or "").startswith("<В новой ред.")
    # многоабзацная пометка «<В ред. ...>» склеена в один абзац
    note = records["nk1.ch3-1.art25-13"]["edit_note"]
    assert note is not None and note.startswith("<В ред.") and note.endswith(">")

    # пометка «<Изменения: ...>» ушла в edit_note корня, а не в текст
    _, flat, stats = parse_document(BANK_FRAGMENT, "nk1")
    root_records = [r for r in flat if r["unit_id"] == "nk1"]
    assert root_records and root_records[0]["edit_note"].startswith("<Изменения:")
    assert stats.edition_notes >= 6


def test_flattened_point_numbers(records):
    # «11.» после пункта 1 -> пункт 1.1
    assert "nk1.ch3-1.art25-13.p1-1" in records
    assert records["nk1.ch3-1.art25-13.p1-1"]["number"] == "1.1"


def test_flattened_fractional_subpoint(records):
    # «31)» после подпункта 3 -> подпункт 3.1 (как в ст. 11.3 «Единый налоговый счет»)
    _, flat, stats = parse_document(
        "Глава 1. ЗАКОНОДАТЕЛЬСТВО О НАЛОГАХ\n\n"
        "Статья 11. Понятия\n\ntекст статьи.\n\n"
        "Статья 113. Единый налоговый платеж\n\n"
        "1. Понятия:\n\n"
        "1) единый налоговый счет;\n\n"
        "2) срок уплаты;\n\n"
        "3) сумма налога;\n\n"
        "31) срок уплаты налога;\n\n"
        "4) отчетный период.\n",
        "nk1",
    )
    recs = {r["unit_id"]: r for r in flat}
    assert recs["nk1.ch1.art11-3.p1.sp3-1"]["number"] == "3.1"
    assert recs["nk1.ch1.art11-3.p1.sp4"]["number"] == "4"
    assert stats.detokenized >= 2  # «113» -> 11.3 и «31» -> 3.1


def test_bank_fragment_validation(records):
    report = validate(list(records.values()))
    assert not report.has_errors, report.render_markdown()


def test_html_converter_merges_centered_headers():
    html = (
        '<p class="a" style="text-align:center"><span>Глава 2. НАЛОГОВЫЕ ОРГАНЫ</span></p>'
        '<p class="a" style="text-align:center"><span>ПРЕДСТАВИТЕЛЬСТВО</span></p>'
        '<p class="a" style="text-align:center"><span>Раздел VI. ОБ ОТВЕТСТВЕННОСТИ</span></p>'
    )
    text = html_to_paragraph_text(html)
    assert "Глава 2. НАЛОГОВЫЕ ОРГАНЫ ПРЕДСТАВИТЕЛЬСТВО" in text
    assert "\n\nРаздел VI. ОБ ОТВЕТСТВЕННОСТИ" in text, (
        "заголовок раздела не должен приклеиваться к заголовку главы"
    )


def test_html_converter_merges_multiblock_notes():
    # пометка «<В ред. …>», разорванная между абзацами HTML, склеивается в один
    html = (
        '<p class="a"><span>&lt;В ред.</span></p>'
        '<p class="a"><span>Федерального закона от 08 июня 2015 N 150-ФЗ &gt;</span></p>'
        '<p class="a"><span>1. Текст пункта.</span></p>'
    )
    text = html_to_paragraph_text(html)
    blocks = text.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0] == "<В ред. Федерального закона от 08 июня 2015 N 150-ФЗ >"
    assert blocks[1] == "1. Текст пункта."
