"""Аудит документа (F1): статусы ссылок, правки после даты документа, снятые письма, подсказки."""

from taxcorpus.audit import audit_text, render_html, render_markdown
from taxcorpus.interpretations import Document
from taxcorpus.parser import parse_document
from taxcorpus.tools import LocalCorpus

TEXT = """Глава 14. Налоговый контроль

Статья 88. Камеральная налоговая проверка

1. Проверка по месту нахождения органа.

2. Проверка в течение трех месяцев.

<В ред. Федерального закона от 29 мая 2024 N 100-ФЗ (изменения вступают в силу с 1 января 2025 г.)>

3. <Утратил силу с 1 января 2020 г.: Федеральный закон от 29 сентября 2019 N 325-ФЗ>
"""

OUTDATED = Document(doc_id="fns-old", kind="letter", agency="ФНС", number="ЕД-4-15/1", date="2018-01-10",
                    title="О сроках", text="пункт 2 статьи 88 Кодекса", mandatory=True, status="outdated")
ACTUAL = Document(doc_id="fns-new", kind="letter", agency="ФНС", number="БС-4-11/2", date="2025-02-01",
                  title="О камеральных проверках", text="пункт 2 статьи 88 Кодекса", mandatory=True, status="actual")


def corpus():
    _, records, _ = parse_document(TEXT, "nk1")
    return LocalCorpus.from_records(records, {"nk1": "2026-08-04"}, documents=[OUTDATED, ACTUAL])


MEMO = ("Срок проверки — п. 2 ст. 88 НК РФ; см. также п. 3 ст. 88 НК РФ и ст. 999 НК РФ. "
        "Позиция подтверждена письмом ФНС от 10.01.2018 № ЕД-4-15/1 и письмом Минфина от 01.02.2020 № 03-02-07/1/5. "
        "Изменения внесены Федеральным законом от 29.05.2024 № 100-ФЗ.")


def test_audit_statuses_and_context():
    report = audit_text(corpus(), MEMO, "2026-09-10", doc_date="2023-06-01")
    by_raw = {it.raw: it for it in report.items}
    assert by_raw["п. 2 ст. 88 НК РФ"].status == "ok"
    assert by_raw["п. 2 ст. 88 НК РФ"].changed_since_doc[0]["law"] == "100-ФЗ"      # правка после даты документа
    assert by_raw["п. 2 ст. 88 НК РФ"].outdated_letters[0]["number"] == "ЕД-4-15/1"  # снятое письмо по норме
    assert by_raw["п. 3 ст. 88 НК РФ"].status == "stale"
    assert by_raw["ст. 999 НК РФ"].status == "missing"
    assert by_raw["письмом ФНС от 10.01.2018 № ЕД-4-15/1"].status == "outdated_doc"
    assert by_raw["письмом Минфина от 01.02.2020 № 03-02-07/1/5"].status == "unknown_doc"
    fz = [it for it in report.items if it.kind == "federal_law"]
    assert fz and fz[0].status == "external" and "100-ФЗ" in fz[0].note
    assert not report.ok and report.counts["ok"] == 1
    # обязательное актуальное письмо по цитируемой норме, не упомянутое в документе
    assert report.suggestions[0]["number"] == "БС-4-11/2"
    # позиции в тексте корректны и упорядочены
    assert all(MEMO[it.start:it.end] == it.raw for it in report.items)
    assert [it.start for it in report.items] == sorted(it.start for it in report.items)


def test_audit_renderers():
    report = audit_text(corpus(), MEMO, "2026-09-10", doc_date="2023-06-01")
    md = render_markdown(report)
    assert "НЕ ДЕЙСТВУЕТ" in md and "НЕТ В КОРПУСЕ" in md and "менялась после даты документа" in md
    assert "Не упомянуты обязательные письма" in md
    html = render_html(report, MEMO)
    assert '<mark class="audit stale"' in html and '<mark class="audit missing"' in html
    assert "&lt;" not in MEMO or "&lt;" in html


def test_audit_clean_document_is_ok():
    report = audit_text(corpus(), "Срок проверки — п. 2 ст. 88 НК РФ.", "2026-09-10")
    assert report.ok and report.counts == {"ok": 1}
