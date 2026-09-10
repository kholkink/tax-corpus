"""Рабочее пространство дела и сессия агента с паузой на вопрос (подменный клиент)."""

import json
import zipfile

from taxcorpus.agent import TaxAgent
from taxcorpus.case_session import CaseSession
from taxcorpus.parser import parse_document
from taxcorpus.textract import extract_text
from taxcorpus.tools import LocalCorpus
from taxcorpus.workspace import WORKSPACE_TOOL_NAMES, Workspace
from tests.test_agent import TEXT, FakeClient, _block, _response


def _docx(path, paragraphs):
    doc = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{doc}</w:body></w:document>')


def test_workspace_files_and_extraction(tmp_path):
    ws = Workspace.create("demo-1", "Проверка ООО Ромашка", client="ООО Ромашка",
                          as_of="2026-09-10", root=tmp_path)
    assert (ws.path / "notes" / "задача.md").exists() and ws.manifest.client == "ООО Ромашка"
    src = tmp_path / "акт.docx"
    _docx(src, ["Акт камеральной проверки от 20.03.2026", "Недоимка по НДС 1 200 000 руб."])
    rel = ws.add_file(src)
    assert rel == "inbox/акт.docx"
    text = ws.read_file(rel)["text"]
    assert "Недоимка по НДС" in text and (ws.path / "index").glob("*.txt")
    hits = ws.search_files("недоимка НДС")
    assert hits and hits[0]["path"] == "inbox/акт.docx"
    files = {f["path"]: f for f in ws.list_files()}
    assert files["inbox/акт.docx"]["owner"] == "lawyer"
    # запись агента только в research/drafts, с версиями и шапкой
    r1 = ws.write_file("research/позиция.md", "# Позиция\n\nтекст", {"sources": ["nk1.ch14.art88.p2"]})
    r2 = ws.write_file("research/позиция.md", "# Позиция v2", {"sources": []})
    assert (r1["version"], r2["version"]) == (1, 2)
    assert (ws.path / "research" / ".versions" / "позиция.md.v1").exists()
    head = (ws.path / "research" / "позиция.md").read_text(encoding="utf-8")
    assert head.startswith("---\nas_of: \"2026-09-10\"") and "# Позиция v2" in head
    try:
        ws.write_file("inbox/акт.docx", "x")
        assert False
    except PermissionError:
        pass
    task = ws.create_task("Запросить у клиента книгу покупок", "2026-09-20")
    assert ws.tasks()[0]["id"] == task["id"] and task["status"] == "open"
    assert extract_text(ws.path / "notes" / "задача.md").startswith("# Проверка")
    assert Workspace.list_all(tmp_path)[0]["slug"] == "demo-1"


def _corpus():
    _, records, _ = parse_document(TEXT, "nk1")
    return LocalCorpus.from_records(records, {"nk1": "2026-08-04"})


def test_case_session_pauses_on_question_and_verifies_files(tmp_path):
    ws = Workspace.create("demo-2", "Срок камеральной проверки", as_of="2026-09-10", root=tmp_path)
    (ws.path / "notes" / "задача.md").write_text("Декларация подана 20.03.2026. Когда истекает срок проверки?",
                                                 encoding="utf-8")
    responses = [
        _response([_block(type="tool_use", id="t1", name="list_files", input={}),
                   _block(type="tool_use", id="t2", name="read_file", input={"path": "notes/задача.md"})],
                  "tool_use"),
        _response([_block(type="tool_use", id="t3", name="ask_user",
                          input={"question": "Декларация по НДС или иная?", "options": ["НДС", "иная"]})],
                  "tool_use"),
        # после ответа: файл с плохой ссылкой, затем исправленный, затем итог
        _response([_block(type="tool_use", id="t4", name="write_file",
                          input={"path": "research/срок.md", "summary": "первая версия",
                                 "content": "**Вывод** три месяца — п. 2 ст. 88 НК РФ и ст. 999 НК РФ."})],
                  "tool_use"),
        _response([_block(type="tool_use", id="t5", name="write_file",
                          input={"path": "research/срок.md", "summary": "убрана ссылка",
                                 "content": "**Вывод** три месяца — п. 2 ст. 88 НК РФ."}),
                   _block(type="tool_use", id="t6", name="create_task",
                          input={"title": "Уточнить дату получения декларации", "due": None, "details": ""})],
                  "tool_use"),
        _response([_block(type="text", text="Готово: research/срок.md, срок по п. 2 ст. 88 НК РФ — три месяца.")],
                  "end_turn"),
    ]
    client = FakeClient(responses)
    agent = TaxAgent(client, _corpus(), fallbacks=False)
    session = CaseSession(ws, agent)

    turn = session.send("Подготовь позицию по сроку проверки")
    assert turn.kind == "question" and session.status == "waiting_user"
    assert turn.question["options"] == ["НДС", "иная"]
    # инструменты дела вызывались; сессия сохранена и восстанавливается
    assert [t["name"] for t in session.tool_log] == ["list_files", "read_file"]
    assert json.loads(session.tool_log[1]["output"])["text"].startswith("Декларация подана")
    reloaded = CaseSession.load(ws, agent, session.session_id)
    assert reloaded.status == "waiting_user" and reloaded.pending["question"].startswith("Декларация")
    # все инструменты (корпус + дело) переданы модели
    assert {t["name"] for t in client.requests[0]["tools"]} >= WORKSPACE_TOOL_NAMES | {"search", "get_unit"}

    turn = reloaded.send("иная")   # ответ на вопрос агента
    assert turn.kind == "answer" and reloaded.status == "active"
    # ответ подан как tool_result вопроса вместе с результатами других инструментов
    answered = client.requests[2]["messages"][-1]["content"]
    assert answered[-1]["tool_use_id"] == "t3" and "иная" in answered[-1]["content"]
    # первый write_file вернул проблемы проверки, второй — чистый; файл версии 2
    out1 = json.loads(reloaded.tool_log[2]["output"])
    assert out1["verification"]["ok"] is False and "ст. 999 НК РФ" in out1["verification"]["problems"][0]
    out2 = json.loads(reloaded.tool_log[3]["output"])
    assert out2["verification"]["ok"] is True and out2["version"] == 2
    head = (ws.path / "research" / "срок.md").read_text(encoding="utf-8")
    assert '"nk1.ch14.art88.p2"' in head and "убрана ссылка" in head
    assert turn.verification.ok and ws.tasks()[0]["title"].startswith("Уточнить")
    assert reloaded.questions[0]["answer"] == "иная"
    assert CaseSession.list_sessions(ws)[0]["status"] == "active"
