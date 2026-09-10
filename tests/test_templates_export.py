"""Шаблоны документов и экспорт DOCX (F7)."""

import io
import json
import zipfile

from taxcorpus.export import markdown_to_docx, parse_header
from taxcorpus.facts import FactStore
from taxcorpus.templates import TEMPLATE_TOOLS, draft_from_template, list_templates, load_template, render
from taxcorpus.workspace import Workspace


def test_templates_load_and_render():
    names = [t.name for t in list_templates()]
    assert {"возражения-на-акт", "апелляционная-жалоба", "ответ-на-требование", "меморандум"} <= set(names)
    tpl = load_template("возражения-на-акт")
    assert "act_received" in tpl.facts and tpl.summary()["agent_sections"] >= 4
    facts = [{"role": "act_received", "kind": "event", "value": "2026-03-27", "confirmed": True},
             {"role": "act_number", "kind": "other", "value": "№ 12-34", "confirmed": True},
             {"role": "act_date", "kind": "date", "value": "2026-03-20", "confirmed": False},   # не подтверждён
             {"role": "period_end", "kind": "period", "value": "2024-01-01..2024-12-31", "confirmed": True}]
    r = render(tpl, {"client": "ООО «Ромашка»", "as_of": "2026-09-10"}, facts,
               {"deadlines": [{"key": "objections_until", "due": "2026-04-27"}]}, {"authority": "ИФНС № 1"})
    assert r.filled["facts.act_received"] == "27.03.2026" and r.filled["deadlines.objections_until"] == "27.04.2026"
    assert r.filled["facts.period_end"] == "01.01.2024 — 31.12.2024" and "ИФНС № 1" in r.text
    assert "facts.act_date" in r.missing and "от ________" in r.text          # запасное значение
    assert "{{" not in r.text and len(r.agent_sections) >= 4
    r2 = render(tpl, {"client": "X"}, facts, None, {}, confirmed_only=False)
    assert r2.filled["facts.act_date"] == "20.03.2026"
    amount = render(load_template("меморандум"), {"client": "X", "as_of": "2026-09-10"}, [], None, {"topic": "НДС"})
    assert "Меморандум: НДС" in amount.text and "10.09.2026" in amount.text


def test_draft_from_template_writes_versioned_file(tmp_path):
    ws = Workspace.create("tpl", "Дело", client="ООО «Ромашка»", as_of="2026-09-10", root=tmp_path)
    FactStore(ws).add("event", "27.03.2026", "акт вручён", role="act_received", extracted_by="lawyer")
    r = draft_from_template(ws, "возражения-на-акт", "возражения", {"authority": "ИФНС № 1"})
    assert r["path"] == "drafts/возражения.md" and r["version"] == 1
    assert r["filled"]["deadlines.objections_until"] == "27.04.2026" and "facts.inn" in r["missing"]
    text = (ws.path / "drafts" / "возражения.md").read_text(encoding="utf-8")
    head, body = parse_header(text)
    assert head["template"] == "возражения-на-акт" and "27.04.2026" in body and "<!-- agent:" in body
    assert draft_from_template(ws, "возражения-на-акт", "drafts/возражения.md", {})["version"] == 2
    try:
        draft_from_template(ws, "нет-такого", "x", {})
    except FileNotFoundError as exc:
        assert "нет шаблона" in str(exc)
    assert TEMPLATE_TOOLS[0]["name"] == "draft_document" and "возражения-на-акт" in TEMPLATE_TOOLS[0]["input_schema"]["properties"]["template"]["enum"]


def test_markdown_to_docx_structure():
    md = ('---\nas_of: "2026-09-10"\ncorpus_snapshot: 31\nversion: 2\nsources: ["nk1.ch14.art100.p6"]\n'
          'verification: {"ok": true, "problems": []}\n---\n\n# Возражения\n\nАбзац с **жирным** и *курсивом* '
          'и `кодом`.\nПродолжение абзаца.\n\n## Раздел\n\n- первый\n- второй\n\n1. раз\n2. два\n\n'
          '| эпизод | сумма |\n|---|---|\n| НДС | 1 200 000 |\n\n> цитата\n\n<!-- agent: секретная инструкция -->\n')
    data = markdown_to_docx(md)
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("word/document.xml").decode("utf-8")
    core = z.read("docProps/core.xml").decode("utf-8")
    assert "Возражения" in xml and "<w:tbl>" in xml and "1 200 000" in xml and "Продолжение абзаца" in xml
    assert "секретная инструкция" not in xml and "Провенанс" in xml and "nk1.ch14.art100.p6" in xml
    assert "замечаний нет" in xml and "<w:b/>" in xml and "2026-09-10" in core
    assert "Возражения" in core                                   # title из первого заголовка
    long_head = "---\n" + "\n".join(f"k{i}: \"{'x' * 60}\"" for i in range(8)) + "\n---\n\n# Т\n\nабзац\n"
    assert zipfile.ZipFile(io.BytesIO(markdown_to_docx(long_head))).read("word/document.xml")   # длинная шапка не ломает экспорт


def test_api_templates_drafts_and_docx(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from taxcorpus import api

    monkeypatch.setattr(api, "WORKSPACES_ROOT", tmp_path)
    monkeypatch.setattr(api, "_sync", lambda *a, **k: None)
    c = TestClient(api.app)
    assert any(t["name"] == "меморандум" for t in c.get("/templates").json())
    assert c.post("/workspaces", json={"slug": "d1", "title": "Дело", "client": "ООО «Ромашка»"}).status_code == 201
    r = c.post("/workspaces/d1/drafts", json={"template": "меморандум", "path": "memo", "values": {"topic": "НДС"}})
    assert r.status_code == 201 and r.json()["path"] == "drafts/memo.md"
    assert c.post("/workspaces/d1/drafts", json={"template": "нет", "path": "x"}).status_code == 404
    r = c.get("/workspaces/d1/files/drafts/memo.md?format=docx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "memo.docx" in r.headers["content-disposition"]
    xml = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode("utf-8")
    assert "Меморандум: НДС" in xml
    assert c.get("/workspaces/d1/files/drafts/memo.md").json()["text"].startswith("---")


def test_session_draft_tool(tmp_path):
    from taxcorpus.agent import TaxAgent
    from taxcorpus.case_session import CaseSession
    from taxcorpus.parser import parse_document
    from taxcorpus.tools import LocalCorpus
    from tests.test_agent import TEXT, FakeClient, _block, _response

    ws = Workspace.create("s1", "Дело", client="ООО «Ромашка»", as_of="2026-09-10", root=tmp_path)
    responses = [
        _response([_block(type="tool_use", id="t1", name="draft_document",
                          input={"template": "меморандум", "path": "memo", "values": {"topic": "срок проверки"}})], "tool_use"),
        _response([_block(type="text", text="Черновик создан, секции заполню далее.")], "end_turn"),
    ]
    client = FakeClient(responses)
    _, records, _ = parse_document(TEXT, "nk1")
    agent = TaxAgent(client, LocalCorpus.from_records(records, {"nk1": "2026-08-04"}), fallbacks=False)
    session = CaseSession(ws, agent)
    turn = session.send("Подготовь меморандум")
    out = json.loads(session.tool_log[0]["output"])
    assert out["path"] == "drafts/memo.md" and out["agent_sections"] and "drafts/memo.md" in turn.files_written
    assert "draft_document" in session.system() and any(t["name"] == "draft_document" for t in session.tools())
