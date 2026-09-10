"""Факты и таймлайн дела (F6): дословные цитаты, нормализация, сроки через калькуляторы, инструменты агента."""

import json

import pytest

from taxcorpus.facts import FactError, FactStore, parse_amount, parse_date, parse_period
from taxcorpus.workspace import Workspace
from tests.test_workspace import _docx

ACT = ["АКТ налоговой проверки № 12-34 от 20 марта 2026 г.",
       "Проверяемый период: 2024 год. Налогоплательщик: ООО «Ромашка», ИНН 7701234567.",
       "Установлена неуплата НДС в сумме 1 200 000 руб., срок уплаты 25.04.2024.",
       "Акт вручён представителю 27.03.2026."]


def _case(tmp_path):
    ws = Workspace.create("f6", "Проверка", as_of="2026-09-10", root=tmp_path)
    _docx(tmp_path / "акт.docx", ACT)
    ws.add_file(tmp_path / "акт.docx")
    return ws


def test_parsers():
    assert parse_date("20 марта 2026 г.") == parse_date("20.03.2026") == parse_date("2026-03-20") == "2026-03-20"
    assert parse_amount("1 200 000 руб.") == 1_200_000.0 and parse_amount("2,5 млн руб.") == 2_500_000.0
    assert parse_period("2024 год") == "2024-01-01..2024-12-31" and parse_period("1 квартал 2025") == "2025-01-01..2025-03-31"
    with pytest.raises(FactError):
        parse_date("вчера")


def test_agent_fact_requires_verbatim_quote(tmp_path):
    ws = _case(tmp_path)
    store = FactStore(ws)
    f = store.add("event", "27.03.2026", "акт вручён представителю", "inbox/акт.docx",
                  "Акт вручён   представителю 27.03.2026", role="act_received")   # пробелы не важны
    assert f.value == "2026-03-27" and not f.confirmed and f.extracted_by == "agent"
    with pytest.raises(FactError, match="дословно"):
        store.add("amount", "1 200 000", "недоимка", "inbox/акт.docx", "неуплата НДС в сумме 1 300 000 руб.")
    with pytest.raises(FactError, match="source_path"):
        store.add("amount", "1 200 000", "недоимка")
    with pytest.raises(FactError, match="нет в деле"):
        store.add("amount", "1 200 000", "недоимка", "inbox/нет.docx", "x")
    with pytest.raises(FactError, match="роль"):
        store.add("date", "01.01.2024", "x", "inbox/акт.docx", "2024 год", role="whatever")
    with pytest.raises(FactError, match="только фактам-датам"):
        store.add("amount", "1 200 000", "недоимка", "inbox/акт.docx", "1 200 000 руб.", role="tax_due")
    # юрист — источник сам: цитата не обязательна; факт сразу подтверждён
    lf = store.add("party", "ООО «Ромашка»", "налогоплательщик", extracted_by="lawyer")
    assert lf.confirmed and lf.fact_id == 2
    store2 = FactStore(ws)                       # перечитан с диска
    assert [x["fact_id"] for x in store2.list()] == [1, 2]
    assert store2.confirm(1).confirmed and store2.list(confirmed=True)[0]["fact_id"] == 1
    store2.remove(2)
    assert len(store2.list()) == 1 and json.loads((ws.path / "facts.json").read_text(encoding="utf-8"))[0]["confirmed"]


def test_timeline_and_deadlines_create_tasks(tmp_path):
    ws = _case(tmp_path)
    store = FactStore(ws)
    store.add("period", "2024 год", "проверяемый период", "inbox/акт.docx", "Проверяемый период: 2024 год",
              role="period_end", extracted_by="agent")
    store.add("event", "27.03.2026", "акт вручён", "inbox/акт.docx", "Акт вручён представителю 27.03.2026",
              role="act_received")
    store.add("event", "20.03.2026", "составлен акт", "inbox/акт.docx", "от 20 марта 2026 г.")
    store.add("date", "25.04.2024", "срок уплаты НДС", "inbox/акт.docx", "срок уплаты 25.04.2024", role="tax_due")
    tl = store.timeline()
    assert [t["date"] for t in tl] == ["2024-01-01", "2024-04-25", "2026-03-20", "2026-03-27"]
    assert tl[0]["end"] == "2024-12-31"
    out = store.derive_deadlines(create_tasks=True)
    keys = {d["key"]: d["due"] for d in out["deadlines"]}
    assert keys == {"objections_until": "2026-04-27"}          # месяц со дня получения акта (п. 6 ст. 100)
    assert "nk1.ch14.art100.p6" in out["applied"] and out["steps"] and "limitation" not in out
    tasks = ws.tasks()
    assert len(tasks) == 1 and tasks[0]["due"] == "2026-04-27" and "возражения" in tasks[0]["title"]
    store.derive_deadlines()                                   # идемпотентно: задача не дублируется
    assert len(ws.tasks()) == 1
    # решение вынесено и вручено: вступление в силу, апелляция, жалоба, давность по периоду
    store.add("event", "15.05.2026", "решение вынесено", extracted_by="lawyer", role="decision_date")
    store.add("event", "20.05.2026", "решение вручено", extracted_by="lawyer", role="decision_received")
    out = store.derive_deadlines(confirmed_only=True)         # факты агента не подтверждены: без act_received
    keys = {d["key"] for d in out["deadlines"]}
    assert keys == {"decision_in_force", "appeal_until", "complaint_until"} and "limitation" not in out
    store.confirm(1)                                           # период подтверждён -> давность ст. 113
    out = store.derive_deadlines(confirmed_only=True)
    assert out["limitation"]["limitation_ends"] == "2027-12-31" and out["limitation"]["expired"] is False
    assert len(ws.tasks()) == 3                                # + апелляция, жалоба (вступление в силу — не задача)


def test_session_fact_tools(tmp_path):
    from taxcorpus.agent import TaxAgent
    from taxcorpus.case_session import CaseSession
    from taxcorpus.parser import parse_document
    from taxcorpus.tools import LocalCorpus
    from tests.test_agent import TEXT, FakeClient, _block, _response

    ws = _case(tmp_path)
    responses = [
        _response([_block(type="tool_use", id="t1", name="add_fact",
                          input={"kind": "event", "value": "27.03.2026", "text": "акт вручён", "source_path": "inbox/акт.docx",
                                 "quote": "Акт вручён представителю 27.03.2026", "page": None, "role": "act_received"}),
                   _block(type="tool_use", id="t2", name="add_fact",
                          input={"kind": "amount", "value": "1 300 000", "text": "недоимка", "source_path": "inbox/акт.docx",
                                 "quote": "в сумме 1 300 000 руб.", "page": None, "role": None})], "tool_use"),
        _response([_block(type="tool_use", id="t3", name="derive_deadlines", input={"confirmed_only": False}),
                   _block(type="tool_use", id="t4", name="list_facts", input={"kind": None, "confirmed_only": False})], "tool_use"),
        _response([_block(type="text", text="Возражения на акт — до 27.04.2026; расчёт со ссылками см. в задаче.")], "end_turn"),
    ]
    client = FakeClient(responses)
    _, records, _ = parse_document(TEXT, "nk1")
    agent = TaxAgent(client, LocalCorpus.from_records(records, {"nk1": "2026-08-04"}), fallbacks=False)
    session = CaseSession(ws, agent)
    turn = session.send("Извлеки факты из акта и посчитай сроки")
    assert turn.kind == "answer"
    names = [t["name"] for t in session.tool_log]
    assert names == ["add_fact", "add_fact", "derive_deadlines", "list_facts"]
    assert not session.tool_log[0]["is_error"] and session.tool_log[1]["is_error"]      # вторая цитата не дословна
    assert "дословно" in session.tool_log[1]["output"]
    deadlines = json.loads(session.tool_log[2]["output"])
    assert deadlines["deadlines"][0]["due"] == "2026-04-27" and deadlines["tasks"]
    facts = json.loads(session.tool_log[3]["output"])
    assert len(facts) == 1 and facts[0]["confirmed"] is False
    assert any(t["name"] == "add_fact" for t in session.tools())
    assert "add_fact" in session.system() and "derive_deadlines" in session.system()


def test_facts_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from taxcorpus import api

    monkeypatch.setattr(api, "WORKSPACES_ROOT", tmp_path)
    monkeypatch.setattr(api, "_sync", lambda *a, **k: None)
    c = TestClient(api.app)
    assert c.post("/workspaces", json={"slug": "f6api", "title": "Дело"}).status_code == 201
    r = c.post("/workspaces/f6api/facts", json={"kind": "event", "value": "27.03.2026", "text": "акт вручён",
                                                "role": "act_received"})
    assert r.status_code == 201 and r.json()["confirmed"] is True and r.json()["value"] == "2026-03-27"
    r = c.post("/workspaces/f6api/facts", json={"kind": "amount", "value": "x", "text": "?"})
    assert r.status_code == 400 and "сумма" in r.json()["detail"]
    r = c.post("/workspaces/f6api/facts", json={"kind": "amount", "value": "100", "text": "пени", "extracted_by": "agent"})
    assert r.status_code == 400 and "source_path" in r.json()["detail"]
    r = c.get("/workspaces/f6api/facts")
    assert len(r.json()["facts"]) == 1 and r.json()["timeline"][0]["date"] == "2026-03-27"
    assert c.post("/workspaces/f6api/facts/1/confirm?confirmed=false").json()["confirmed"] is False
    assert c.get("/workspaces/f6api/timeline?confirmed_only=true").json() == []
    r = c.post("/workspaces/f6api/deadlines")
    assert r.status_code == 200 and r.json()["deadlines"][0]["due"] == "2026-04-27" and len(r.json()["tasks"]) == 1
    assert c.get("/workspaces/f6api").json()["tasks"][0]["due"] == "2026-04-27"
    assert c.delete("/workspaces/f6api/facts/1").json() == {"deleted": 1}
    assert c.delete("/workspaces/f6api/facts/1").status_code == 404
    assert c.get("/workspaces/f6api/facts").json()["facts"] == []
