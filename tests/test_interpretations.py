"""Разъяснения: реестр, привязка к нормам, выдача по единице, проверка цитат на письма."""

from taxcorpus.agent import TaxAgent
from taxcorpus.interpretations import (Document, InterpretationIndex, link_document,
                                       normalize_date, normalize_number)
from taxcorpus.parser import parse_document
from taxcorpus.tools import LocalCorpus, execute_tool
from tests.test_agent import FakeClient, _block, _response

TEXT = """Глава 14. Налоговый контроль

Статья 88. Камеральная налоговая проверка

1. Проверка по месту нахождения органа.

2. Проверка в течение трех месяцев.

3. Требование пояснений.

Статья 89. Выездная налоговая проверка

1. Решение о проведении.
"""

LETTER = Document(
    doc_id="fns-2024-03-11-bs-4-11-2702", kind="letter", agency="ФНС", number="БС-4-11/2702@",
    date="2024-03-11", title="О сроках камеральной проверки",
    text="В соответствии с пунктом 2 статьи 88 Налогового кодекса Российской Федерации "
         "камеральная проверка проводится в течение трех месяцев. Положения пункта 3 статьи 88 "
         "Кодекса применяются с учётом пункта 1 статьи 89 Кодекса.",
    source_url="https://www.nalog.gov.ru/", mandatory=True)
PLENUM = Document(
    doc_id="plenum-vas-57-2013", kind="plenum", agency="ВАС РФ", number="57", date="2013-07-30",
    title="О некоторых вопросах части первой НК", text="Согласно пункту 2 статьи 88 НК РФ …")
LATE = Document(
    doc_id="minfin-2027", kind="letter", agency="Минфин", number="03-02-07/1", date="2027-01-15",
    title="", text="пункт 2 статьи 88 Кодекса")


def _records():
    return parse_document(TEXT, "nk1")[1]


def test_link_document_resolves_citations():
    corpus = LocalCorpus.from_records(_records(), {"nk1": "2026-08-04"})
    edges = link_document(LETTER, corpus.index)
    assert {e["to_unit_id"] for e in edges} == {"nk1.ch14.art88.p2", "nk1.ch14.art88.p3",
                                                "nk1.ch14.art89.p1"}
    assert all(e["kind"] == "interprets" and e["status"] == "resolved" for e in edges)


def test_get_interpretations_orders_and_filters_by_date():
    corpus = LocalCorpus.from_records(_records(), {"nk1": "2026-08-04"},
                                      documents=[LETTER, PLENUM, LATE])
    rows = corpus.get_interpretations("nk1.ch14.art88.p2", "2026-09-10")
    assert [r["doc_id"] for r in rows] == ["plenum-vas-57-2013", "fns-2024-03-11-bs-4-11-2702"]
    assert rows[1]["mandatory"] and "пунктом 2 статьи 88" in rows[1]["cites"]
    # ссылка на статью целиком тоже находит документы по её пунктам
    assert len(corpus.get_interpretations("nk1.ch14.art88", "2026-09-10")) == 2
    # письмо 2027 года появляется только на позднюю дату
    assert any(r["doc_id"] == "minfin-2027" for r in corpus.get_interpretations("nk1.ch14.art88.p2", "2027-02-01"))
    out, err = execute_tool(corpus, "get_interpretations", {"unit_id": "nk1.ch14.art89.p1"}, "2026-09-10")
    assert not err and "fns-2024" in out
    out, _ = execute_tool(corpus, "get_interpretations", {"unit_id": "nk1.ch14.art88.p1"}, "2026-09-10")
    assert "нет разъяснений" in out


def test_doc_citation_parsing_and_registry_check():
    assert normalize_date("11.03.2024") == "2024-03-11"
    assert normalize_date("1 марта 2024") == "2024-03-01"
    assert normalize_number("бс-4-11/2702@ ") == "БС-4-11/2702@"
    idx = InterpretationIndex([LETTER], UnitIndexStub())
    checks = idx.verify_doc_citations(
        "См. письмо ФНС России от 11.03.2024 № БС-4-11/2702@ и письмо Минфина от 5 мая 2020 г. N 03-07-11/1.")
    assert [(c["status"], c["doc_id"]) for c in checks] == [
        ("ok", "fns-2024-03-11-bs-4-11-2702"), ("unknown", None)]


class UnitIndexStub:
    def resolve_reference(self, target, from_unit_id):
        from taxcorpus.resolver import Resolution
        return Resolution(None, "unresolved", None)


def test_agent_flags_unknown_letters():
    corpus = LocalCorpus.from_records(_records(), {"nk1": "2026-08-04"}, documents=[LETTER])
    answer = _block(type="text", text="**Вывод** п. 2 ст. 88 НК РФ; письмо ФНС от 11.03.2024 № БС-4-11/2702@; "
                                       "письмо Минфина от 01.02.2021 № 03-03-06/1/999.")
    fixed = _block(type="text", text="**Вывод** п. 2 ст. 88 НК РФ; письмо ФНС от 11.03.2024 № БС-4-11/2702@.")
    client = FakeClient([_response([answer], "end_turn"), _response([fixed], "end_turn")])
    result = TaxAgent(client, corpus, fallbacks=False).ask("?", "2026-09-10")
    assert result.reworked and result.verification.ok
    assert "03-03-06/1/999" in client.requests[1]["messages"][-1]["content"]


def test_search_interpretations_offline():
    corpus = LocalCorpus.from_records(_records(), {"nk1": "2026-08-04"}, documents=[LETTER, PLENUM, LATE])
    rows = corpus.search_interpretations("сроки камеральной проверки", "2026-09-10")
    assert rows and rows[0]["doc_id"] == "fns-2024-03-11-bs-4-11-2702" and rows[0]["approximate"]
    assert all(r["date"] <= "2026-09-10" for r in rows)
    out, err = execute_tool(corpus, "search_interpretations", {"query": "камеральная"}, "2026-09-10")
    assert not err and "fns-2024" in out
