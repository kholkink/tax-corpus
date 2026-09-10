"""Совместная работа (F8): комментарии, журнал, инструменты агента, API."""

import json

import pytest

from taxcorpus.collab import CollabStore
from taxcorpus.workspace import Workspace


def _ws(tmp_path):
    ws = Workspace.create("cb", "Дело", as_of="2026-09-10", root=tmp_path)
    ws.write_file("research/позиция.md", "# Позиция\n\nСрок — три месяца.", {"sources": []})
    return ws


def test_comments_threads_and_activity(tmp_path):
    ws = _ws(tmp_path)
    store = CollabStore(ws)
    c = store.add_comment("research/позиция.md", "Уточните срок по п. 2 ст. 88", "user:anna@firm.ru", anchor="три месяца")
    assert c["version"] == 1 and c["anchor"] == "три месяца" and not c["resolved"]
    with pytest.raises(FileNotFoundError):
        store.add_comment("research/нет.md", "x", "user:anna@firm.ru")
    with pytest.raises(KeyError):
        store.add_comment("research/позиция.md", "x", "agent", parent_id="c_nope")
    r = store.add_comment("research/позиция.md", "Исправлено в v2", "agent", parent_id=c["comment_id"])
    threads = CollabStore(ws).threads("research/позиция.md")
    assert len(threads) == 1 and threads[0]["replies"][0]["text"] == "Исправлено в v2"
    store.resolve(c["comment_id"], "user:anna@firm.ru")
    assert CollabStore(ws).threads("research/позиция.md") == [] and store.list(open_only=True) == []
    assert store.list()[0]["resolved_by"] == "user:anna@firm.ru" and len(store.list("research/позиция.md")) == 2
    acts = store.activity()
    assert [a["action"] for a in acts] == ["resolve", "comment", "comment"] and acts[0]["actor"] == "user:anna@firm.ru"
    assert acts[1]["details"]["reply"] is True and r["parent_id"] == c["comment_id"]


def test_agent_reads_and_replies_to_comments(tmp_path):
    from taxcorpus.agent import TaxAgent
    from taxcorpus.case_session import CaseSession
    from taxcorpus.parser import parse_document
    from taxcorpus.tools import LocalCorpus
    from tests.test_agent import TEXT, FakeClient, _block, _response

    ws = _ws(tmp_path)
    c = CollabStore(ws).add_comment("research/позиция.md", "Добавьте ссылку на п. 2 ст. 88", "user:anna@firm.ru")
    responses = [
        _response([_block(type="tool_use", id="t1", name="list_comments", input={"path": "research/позиция.md"})], "tool_use"),
        _response([_block(type="tool_use", id="t2", name="write_file",
                          input={"path": "research/позиция.md", "content": "# Позиция\n\nСрок — три месяца (п. 2 ст. 88 НК РФ).",
                                 "summary": "добавлена ссылка"}),
                   _block(type="tool_use", id="t3", name="reply_comment",
                          input={"comment_id": c["comment_id"], "text": "Ссылка добавлена в v2."})], "tool_use"),
        _response([_block(type="text", text="Файл обновлён с учётом замечания (п. 2 ст. 88 НК РФ).")], "end_turn"),
    ]
    client = FakeClient(responses)
    _, records, _ = parse_document(TEXT, "nk1")
    agent = TaxAgent(client, LocalCorpus.from_records(records, {"nk1": "2026-08-04"}), fallbacks=False)
    session = CaseSession(ws, agent)
    turn = session.send("Учти замечания к research/позиция.md")
    assert turn.kind == "answer"
    seen = json.loads(session.tool_log[0]["output"])
    assert seen[0]["comment_id"] == c["comment_id"] and seen[0]["text"].startswith("Добавьте")
    threads = CollabStore(ws).threads("research/позиция.md")
    assert threads[0]["replies"][0]["author"] == "agent" and threads[0]["replies"][0]["text"] == "Ссылка добавлена в v2."
    acts = [a["action"] for a in CollabStore(ws).activity()]
    assert "write_file" in acts and acts.count("comment") == 2
    wf = next(a for a in CollabStore(ws).activity() if a["action"] == "write_file")
    assert wf["actor"] == "agent" and wf["details"]["version"] == 2 and wf["details"]["verification_ok"]
    assert "list_comments" in session.system()
    empty = json.loads(session._run_workspace_tool("list_comments", {"path": "research/нет.md"})[0])
    assert empty["comments"] == []


def test_collab_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from taxcorpus import api

    monkeypatch.setattr(api, "WORKSPACES_ROOT", tmp_path)
    monkeypatch.setattr(api, "_sync", lambda *a, **k: None)
    _ws(tmp_path)
    c = TestClient(api.app)
    r = c.post("/workspaces/cb/comments", json={"path": "research/позиция.md", "text": "замечание", "anchor": "три месяца"})
    assert r.status_code == 201 and r.json()["author"] == "user:local"
    cid = r.json()["comment_id"]
    assert c.post("/workspaces/cb/comments", json={"path": "research/нет.md", "text": "x"}).status_code == 404
    assert c.post("/workspaces/cb/comments", json={"path": "research/позиция.md", "text": "x", "parent_id": "c_no"}).status_code == 404
    assert c.post("/workspaces/cb/comments", json={"path": "research/позиция.md", "text": "ответ", "parent_id": cid}).status_code == 201
    assert len(c.get("/workspaces/cb/comments?path=research/позиция.md").json()) == 2
    assert c.post(f"/workspaces/cb/comments/{cid}/resolve").json()["resolved"] is True
    assert c.get("/workspaces/cb/comments?open=true").json() == []
    assert c.post("/workspaces/cb/comments/c_no/resolve").status_code == 404
    r = c.post("/workspaces/cb/drafts", json={"template": "меморандум", "path": "memo", "values": {}})
    assert r.status_code == 201
    acts = c.get("/workspaces/cb/activity?limit=10").json()
    assert acts[0]["action"] == "draft_document" and any(a["action"] == "resolve" for a in acts)
