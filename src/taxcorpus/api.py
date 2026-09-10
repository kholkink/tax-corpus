"""HTTP API (слой 7 плана) над инструментами корпуса и агентом.

Запуск: pip install -e ".[api]" && uvicorn taxcorpus.api:app --reload
Бэкенд: TAXCORPUS_DB=postgresql://… -> PostgreSQL (DbCorpus), иначе офлайн-корпус из
data/processed (LocalCorpus, поиск грубый). Агент (/ask) требует anthropic + ключ.

Каждый ответ несёт as_of и unit_id — юрист видит, на какую дату и какая единица
процитирована; /ask возвращает отчёт проверки цитат и журнал вызовов.
"""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .deadlines import ProductionCalendar, compute_deadline
from .tools import DbCorpus, LocalCorpus

app = FastAPI(title="tax-corpus", version="0.1.0",
              description="Корпус налогового права РФ: нормы на дату, ссылки, параметры, "
                          "разъяснения, сроки, агент с проверкой цитат")


@lru_cache(maxsize=1)
def corpus():
    from . import load_dotenv
    load_dotenv()  # TAXCORPUS_DB / ANTHROPIC_* из .env
    db_url = os.environ.get("TAXCORPUS_DB")
    data_dir = os.environ.get("TAXCORPUS_DATA", "data/processed")
    if db_url:
        from .db import connect
        return DbCorpus(connect(db_url), data_dir)
    return LocalCorpus(data_dir)


def _as_of(value: str | None) -> str:
    return value or date.today().isoformat()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui() -> str:
    """Веб-клиент (фаза B): одна статическая страница без сборки."""
    from pathlib import Path
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    c = corpus()
    return {"status": "ok", "backend": type(c).__name__}


@app.get("/units/{unit_id}")
def get_unit(unit_id: str, as_of: str | None = None) -> dict:
    row = corpus().get_unit(unit_id, _as_of(as_of))
    if row is None:
        raise HTTPException(404, f"единица {unit_id} не существует или не действует на {_as_of(as_of)}")
    return {"as_of": _as_of(as_of), **row}


@app.get("/resolve")
def resolve(citation: str = Query(..., description="«п. 2 ст. 88 НК РФ»"),
            context_unit_id: str | None = None) -> dict:
    return corpus().resolve_citation(citation, context_unit_id)


@app.get("/search")
def search(q: str, as_of: str | None = None, limit: int = Query(10, ge=1, le=50)) -> dict:
    return {"as_of": _as_of(as_of), "query": q,
            "results": corpus().search(q, _as_of(as_of), limit)}


@app.get("/units/{unit_id}/amendments")
def amendments(unit_id: str, since: str | None = None) -> list[dict]:
    return corpus().list_amendments(unit_id, since)


@app.get("/units/{unit_id}/interpretations")
def interpretations(unit_id: str, as_of: str | None = None,
                    limit: int = Query(10, ge=1, le=50)) -> list[dict]:
    return corpus().get_interpretations(unit_id, _as_of(as_of), limit)


@app.get("/interpretations/search")
def search_interpretations(q: str, as_of: str | None = None,
                           limit: int = Query(5, ge=1, le=50)) -> list[dict]:
    return corpus().search_interpretations(q, _as_of(as_of), limit)


@app.get("/parameters/{name}")
def parameter(name: str, as_of: str | None = None) -> dict:
    row = corpus().get_parameter(name, _as_of(as_of))
    if row is None:
        raise HTTPException(404, f"параметр {name} не найден или не действует на {_as_of(as_of)}")
    return {"as_of": _as_of(as_of), **row}


@app.get("/terms")
def terms(q: str, as_of: str | None = None) -> list[dict]:
    return corpus().find_terms(q, _as_of(as_of))


class DeadlineRequest(BaseModel):
    start: date
    amount: int = Field(gt=0)
    unit: str = Field(pattern="^(days|calendar_days|months|quarters|years)$")


@app.post("/deadline")
def deadline(req: DeadlineRequest) -> dict:
    r = compute_deadline(req.start, req.amount, req.unit, ProductionCalendar.load())
    return {"end": r.end, "nominal_end": r.nominal_end, "shifted": r.shifted,
            "steps": r.steps, "applied_units": r.applied, "calendar_note": r.calendar_note}


class AskRequest(BaseModel):
    question: str
    as_of: date | None = None
    model: str | None = None
    effort: str = "high"


@app.post("/ask")
def ask(req: AskRequest) -> dict[str, Any]:
    try:
        import anthropic
    except ImportError as exc:
        raise HTTPException(501, "агент недоступен: pip install -e '.[agent]'") from exc
    from . import load_dotenv
    from .agent import TaxAgent
    load_dotenv()
    model = req.model or os.environ.get("TAXCORPUS_MODEL") or "claude-opus-5"
    agent = TaxAgent(anthropic.Anthropic(), corpus(), model=model, effort=req.effort,
                     fallbacks=not os.environ.get("ANTHROPIC_BASE_URL"))
    return agent.ask(req.question, req.as_of).to_dict()


# --- рабочее пространство дела (docs/workspace-plan.md, фаза A4) ---------------------------

import base64  # noqa: E402

from .case_session import CaseSession  # noqa: E402
from .workspace import Workspace  # noqa: E402

WORKSPACES_ROOT = os.environ.get("TAXCORPUS_WORKSPACES", "workspaces")


def make_agent():
    """Агент для сессий дела (подменяется в тестах)."""
    import anthropic
    from . import load_dotenv
    from .agent import TaxAgent
    load_dotenv()
    return TaxAgent(anthropic.Anthropic(max_retries=4), corpus(),
                    model=os.environ.get("TAXCORPUS_MODEL") or "claude-opus-5",
                    fallbacks=not os.environ.get("ANTHROPIC_BASE_URL"),
                    calendar=ProductionCalendar.load())


def _ws(slug: str) -> Workspace:
    try:
        return Workspace.open(slug, WORKSPACES_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _turn_dict(session: CaseSession, turn) -> dict:
    return {"session_id": session.session_id, "status": session.status, "kind": turn.kind,
            "text": turn.text, "question": turn.question,
            "verification": ({"ok": turn.verification.ok,
                              "checks": [c.__dict__ for c in turn.verification.checks]}
                             if turn.verification else None),
            "files_written": turn.files_written, "tool_calls": len(session.tool_log)}


class WorkspaceCreate(BaseModel):
    slug: str
    title: str
    client: str = ""
    as_of: date | None = None
    jurisdiction: str | None = None


@app.get("/workspaces")
def list_workspaces() -> list[dict]:
    return Workspace.list_all(WORKSPACES_ROOT)


@app.post("/workspaces", status_code=201)
def create_workspace(req: WorkspaceCreate) -> dict:
    try:
        ws = Workspace.create(req.slug, req.title, client=req.client,
                              as_of=req.as_of.isoformat() if req.as_of else None,
                              root=WORKSPACES_ROOT, jurisdiction=req.jurisdiction)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return ws.manifest.to_dict()


@app.get("/workspaces/{slug}")
def get_workspace(slug: str) -> dict:
    ws = _ws(slug)
    return {"manifest": ws.manifest.to_dict(), "files": ws.list_files(), "tasks": ws.tasks(),
            "sessions": CaseSession.list_sessions(ws)}


class FileUpload(BaseModel):
    name: str
    content_base64: str
    dest: str = Field("inbox", pattern="^(inbox|notes)$")


@app.post("/workspaces/{slug}/files", status_code=201)
def upload_file(slug: str, req: FileUpload) -> dict:
    ws = _ws(slug)
    name = os.path.basename(req.name)
    if not name:
        raise HTTPException(400, "пустое имя файла")
    target = ws.path / req.dest / name
    target.write_bytes(base64.b64decode(req.content_base64))
    rel = f"{req.dest}/{name}"
    return {"path": rel, "size": target.stat().st_size}


@app.get("/workspaces/{slug}/files/{path:path}")
def read_workspace_file(slug: str, path: str, offset: int = 0, max_chars: int = 20000) -> dict:
    ws = _ws(slug)
    try:
        return ws.read_file(path, offset, max_chars)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"нет файла {path}") from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/workspaces/{slug}/search")
def search_workspace_files(slug: str, q: str, limit: int = Query(5, ge=1, le=20)) -> list[dict]:
    return _ws(slug).search_files(q, limit)


class SessionMessage(BaseModel):
    message: str


@app.post("/workspaces/{slug}/sessions", status_code=201)
def start_session(slug: str, req: SessionMessage) -> dict:
    ws = _ws(slug)
    session = CaseSession(ws, make_agent())
    turn = session.send(req.message)
    return _turn_dict(session, turn)


@app.get("/workspaces/{slug}/sessions/{session_id}")
def get_session(slug: str, session_id: str) -> dict:
    ws = _ws(slug)
    try:
        session = CaseSession.load(ws, make_agent(), session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "нет такой сессии") from exc
    # для клиента: только видимые реплики (тексты пользователя и ответы агента), вопросы, журнал
    visible = []
    for m in session.messages:
        if m["role"] == "user" and isinstance(m["content"], str):
            visible.append({"role": "user", "text": m["content"]})
        elif m["role"] == "assistant":
            text = "\n".join(b.get("text", "") for b in m["content"] if isinstance(b, dict) and b.get("type") == "text")
            if text.strip():
                visible.append({"role": "agent", "text": text})
    return {"session_id": session.session_id, "status": session.status, "pending": session.pending,
            "questions": session.questions, "messages": visible, "tool_log": session.tool_log,
            "files_written": session.files_written}


@app.post("/workspaces/{slug}/sessions/{session_id}/messages")
def continue_session(slug: str, session_id: str, req: SessionMessage) -> dict:
    ws = _ws(slug)
    try:
        session = CaseSession.load(ws, make_agent(), session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "нет такой сессии") from exc
    turn = session.send(req.message)  # при waiting_user это ответ на вопрос агента
    return _turn_dict(session, turn)
