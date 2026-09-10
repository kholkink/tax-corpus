"""HTTP API над офлайн-корпусом (пропускается без fastapi)."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from taxcorpus import api  # noqa: E402
from taxcorpus.parser import parse_document  # noqa: E402
from taxcorpus.tools import LocalCorpus  # noqa: E402
from tests.test_agent import TEXT  # noqa: E402


@pytest.fixture(autouse=True)
def offline_corpus(monkeypatch):
    _, records, _ = parse_document(TEXT, "nk1")
    c = LocalCorpus.from_records(records, {"nk1": "2026-08-04"})
    monkeypatch.setattr(api, "corpus", lambda: c)


def test_endpoints():
    client = TestClient(api.app)
    assert client.get("/health").json()["backend"] == "LocalCorpus"
    r = client.get("/units/nk1.ch14.art88.p2", params={"as_of": "2026-09-10"})
    assert r.status_code == 200 and "трех месяцев" in r.json()["full_text"]
    assert client.get("/units/nk1.ch14.art88.p3", params={"as_of": "2026-09-10"}).status_code == 404
    assert client.get("/resolve", params={"citation": "п. 2 ст. 88"}).json()["unit_id"] == "nk1.ch14.art88.p2"
    assert client.get("/search", params={"q": "камеральная проверка"}).json()["results"]
    d = client.post("/deadline", json={"start": "2025-03-20", "amount": 3, "unit": "months"}).json()
    assert d["end"] == "2025-06-20"
    assert client.post("/deadline", json={"start": "2025-03-20", "amount": 0, "unit": "months"}).status_code == 422


def test_workspace_endpoints(tmp_path, monkeypatch):
    import base64

    from taxcorpus.agent import TaxAgent
    from tests.test_agent import FakeClient, _block, _response

    monkeypatch.setattr(api, "WORKSPACES_ROOT", str(tmp_path))
    responses = [
        _response([_block(type="tool_use", id="t1", name="ask_user",
                          input={"question": "Какой режим налогообложения?", "options": ["УСН", "ОСНО"]})],
                  "tool_use"),
        _response([_block(type="tool_use", id="t2", name="write_file",
                          input={"path": "research/итог.md", "summary": "итог",
                                 "content": "**Вывод** три месяца — п. 2 ст. 88 НК РФ."})], "tool_use"),
        _response([_block(type="text", text="Готово: research/итог.md (п. 2 ст. 88 НК РФ).")], "end_turn"),
    ]
    fake = FakeClient(responses)  # одна очередь ответов на все запросы сессии
    monkeypatch.setattr(api, "make_agent", lambda: TaxAgent(fake, api.corpus(), fallbacks=False))
    client = TestClient(api.app)

    r = client.post("/workspaces", json={"slug": "case-1", "title": "Дело 1", "client": "ООО", "as_of": "2026-09-10"})
    assert r.status_code == 201 and r.json()["slug"] == "case-1"
    assert client.post("/workspaces", json={"slug": "case-1", "title": "x"}).status_code == 400
    assert client.get("/workspaces").json()[0]["slug"] == "case-1"

    r = client.post("/workspaces/case-1/files", json={
        "name": "факты.md", "content_base64": base64.b64encode("Декларация подана 20.03.2026".encode()).decode()})
    assert r.status_code == 201 and r.json()["path"] == "inbox/факты.md"
    assert "20.03.2026" in client.get("/workspaces/case-1/files/inbox/факты.md").json()["text"]
    assert client.get("/workspaces/case-1/search", params={"q": "декларация"}).json()[0]["path"] == "inbox/факты.md"
    assert client.get("/workspaces/case-1/files/inbox/нет.md").status_code == 404

    r = client.post("/workspaces/case-1/sessions", json={"message": "Подготовь позицию"})
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "question" and body["status"] == "waiting_user"
    assert body["question"]["options"] == ["УСН", "ОСНО"]
    sid = body["session_id"]
    assert client.get(f"/workspaces/case-1/sessions/{sid}").json()["pending"]["question"].startswith("Какой")

    r = client.post(f"/workspaces/case-1/sessions/{sid}/messages", json={"message": "УСН"})
    body = r.json()
    assert body["kind"] == "answer" and body["verification"]["ok"] and "research/итог.md" in body["files_written"]
    ws = client.get("/workspaces/case-1").json()
    assert any(f["path"] == "research/итог.md" for f in ws["files"])
    assert ws["sessions"][0]["status"] == "active"
    hist = client.get(f"/workspaces/case-1/sessions/{sid}").json()
    assert [m["role"] for m in hist["messages"]] == ["user", "agent"] and hist["questions"][0]["answer"] == "УСН"
    assert client.get("/workspaces/nope").status_code == 404


def test_ui_page_served():
    client = TestClient(api.app)
    r = client.get("/")
    assert r.status_code == 200 and "рабочее пространство" in r.text and "/workspaces" in r.text


def test_audit_endpoint():
    client = TestClient(api.app)
    r = client.post("/audit", json={"text": "См. п. 2 ст. 88 НК РФ и п. 3 ст. 88 НК РФ.", "as_of": "2026-09-10"})
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"ok": 1, "stale": 1} and "<mark" in body["html"] and "НЕ ДЕЙСТВУЕТ" in body["markdown"]
    assert client.post("/audit", json={}).status_code == 400
