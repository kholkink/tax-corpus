"""HTTP API (слой 7 плана) над инструментами корпуса и агентом.

Запуск: pip install -e ".[api]" && uvicorn taxcorpus.api:app --reload
Бэкенд: TAXCORPUS_DB=postgresql://… -> PostgreSQL (DbCorpus), иначе офлайн-корпус из
data/processed (LocalCorpus, поиск грубый). Агент (/ask) требует anthropic + ключ.

Каждый ответ несёт as_of и unit_id — юрист видит, на какую дату и какая единица
процитирована; /ask возвращает отчёт проверки цитат и журнал вызовов.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    out = {"status": "ok", "backend": type(c).__name__}
    try:
        from .providers import choose
        p = choose("standard")
        out["provider"] = {"name": p.name, "model": p.model, "location": p.location, "badge": p.badge()}
    except Exception as exc:  # noqa: BLE001
        out["provider"] = {"error": str(exc)}
    return out


ACCURACY_PATH = Path(__file__).resolve().parents[2] / "reports" / "accuracy.json"


@app.get("/accuracy")
def accuracy(format: str = "json"):
    """Публичная карта точности (F11): метрики агента по темам и качество поиска на эталоне."""
    if not ACCURACY_PATH.exists():
        raise HTTPException(404, "карта точности ещё не собрана: python scripts/eval_agent.py --accuracy")
    if format == "md":
        md = ACCURACY_PATH.with_suffix(".md")
        return HTMLResponse(f"<pre style='white-space:pre-wrap;font:14px system-ui'>{md.read_text(encoding='utf-8')}</pre>")
    return json.loads(ACCURACY_PATH.read_text(encoding="utf-8"))


@app.get("/units/{unit_id}")
def get_unit(unit_id: str, as_of: str | None = None) -> dict:
    row = corpus().get_unit(unit_id, _as_of(as_of))
    if row is None:
        raise HTTPException(404, f"единица {unit_id} не существует или не действует на {_as_of(as_of)}")
    return {"as_of": _as_of(as_of), **row}


@app.get("/units/{unit_id}/card")
def unit_card(unit_id: str, as_of: str | None = None) -> dict:
    """Карточка нормы (F12): текст на дату, лента правок, письма и практика, версии текста, параметры."""
    c = corpus()
    d = _as_of(as_of)
    unit = c.get_unit(unit_id, d)
    versions: list[dict] = []
    parameters: list[dict] = []
    conn = getattr(c, "conn", None)
    if conn is not None:
        from .db import parameters_for_unit, unit_versions
        versions = unit_versions(conn, unit_id)
        parameters = parameters_for_unit(conn, unit_id, d)
    if unit is None and not versions:
        raise HTTPException(404, f"единица {unit_id} не существует")
    amendments = c.list_amendments(unit_id, None)
    interpretations = c.get_interpretations(unit_id, d, 20)
    return {"as_of": d, "unit_id": unit_id, "unit": unit, "in_force": unit is not None,
            "amendments": amendments, "interpretations": interpretations, "versions": versions,
            "parameters": parameters,
            "explain": {"amendments": len(amendments), "interpretations": len(interpretations),
                        "versions": len(versions), "mandatory_letters": sum(1 for i in interpretations if i.get("mandatory"))}}


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


@app.post("/calc/{name}")
def calc(name: str, args: dict[str, Any]) -> dict:
    """Калькуляторы с цитатами (F5): compute_penalty, compute_fine, appeal_deadlines, limitation_status."""
    from .tools import run_calculator
    try:
        return run_calculator(name, args, ProductionCalendar.load())
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


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


def make_agent(ws: Workspace | None = None):
    """Агент для сессий дела по профилю провайдера (P4/F9); подменяется в тестах."""
    from .providers import ProviderError, choose, make_agent as _make
    manifest = ws.manifest if ws is not None else None
    try:
        provider = choose(getattr(manifest, "confidentiality", "standard"), getattr(manifest, "provider", None))
    except ProviderError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _make(corpus(), provider, calendar=ProductionCalendar.load())


def _ws(slug: str) -> Workspace:
    try:
        return Workspace.open(slug, WORKSPACES_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _sync(ws: Workspace, session: CaseSession | None = None) -> None:
    """Зеркало метаданных дела в БД (P1), если бэкенд — PostgreSQL."""
    c = corpus()
    conn = getattr(c, "conn", None)
    if conn is None:
        return
    try:
        from .workspace_store import sync_workspace, upsert_session
        info = sync_workspace(conn, ws)
        if session is not None:
            upsert_session(conn, info["workspace_id"], session)
    except Exception as exc:  # noqa: BLE001 — зеркало не должно ломать работу с файлами
        import logging
        logging.getLogger(__name__).warning("workspace sync failed: %s", exc)


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
    confidentiality: str = Field("standard", pattern="^(standard|sensitive)$")
    provider: str | None = None


@app.get("/workspaces")
def list_workspaces() -> list[dict]:
    return Workspace.list_all(WORKSPACES_ROOT)


@app.post("/workspaces", status_code=201)
def create_workspace(req: WorkspaceCreate) -> dict:
    try:
        ws = Workspace.create(req.slug, req.title, client=req.client,
                              as_of=req.as_of.isoformat() if req.as_of else None,
                              root=WORKSPACES_ROOT, jurisdiction=req.jurisdiction,
                              confidentiality=req.confidentiality, provider=req.provider)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return ws.manifest.to_dict()


@app.get("/workspaces/{slug}")
def get_workspace(slug: str) -> dict:
    ws = _ws(slug)
    _sync(ws)
    provider = None
    try:
        from .providers import choose
        provider = choose(ws.manifest.confidentiality, ws.manifest.provider).badge()
    except Exception as exc:  # noqa: BLE001
        provider = f"недоступен: {exc}"
    return {"manifest": ws.manifest.to_dict(), "files": ws.list_files(), "tasks": ws.tasks(),
            "sessions": CaseSession.list_sessions(ws), "provider": provider}


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
    session = CaseSession(ws, make_agent(ws))
    turn = session.send(req.message)
    _sync(ws, session)
    return _turn_dict(session, turn)


@app.get("/workspaces/{slug}/sessions/{session_id}")
def get_session(slug: str, session_id: str) -> dict:
    ws = _ws(slug)
    try:
        session = CaseSession.load(ws, make_agent(ws), session_id)
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
        session = CaseSession.load(ws, make_agent(ws), session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "нет такой сессии") from exc
    turn = session.send(req.message)  # при waiting_user это ответ на вопрос агента
    _sync(ws, session)
    return _turn_dict(session, turn)


# --- аудит документа (F1) --------------------------------------------------------------

class AuditRequest(BaseModel):
    text: str | None = None
    content_base64: str | None = None
    name: str | None = None
    as_of: date | None = None
    doc_date: date | None = None


def _audit_from_request(req: AuditRequest) -> tuple[str, str]:
    from .textract import extract_text
    if req.text:
        return req.text, "text"
    if req.content_base64 and req.name:
        import tempfile
        from pathlib import Path as _P
        suffix = _P(req.name).suffix or ".txt"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(base64.b64decode(req.content_base64))
            tmp_path = tmp.name
        try:
            return extract_text(tmp_path), req.name
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            os.unlink(tmp_path)
    raise HTTPException(400, "нужен text или content_base64 с name")


def _run_audit(text: str, source: str, as_of: date | None, doc_date: date | None,
               workspace_id: int | None = None) -> dict:
    from .audit import audit_text, render_html, render_markdown
    as_of_s = _as_of(as_of.isoformat() if as_of else None)
    report = audit_text(corpus(), text, as_of_s, doc_date.isoformat() if doc_date else None)
    conn = getattr(corpus(), "conn", None)
    audit_id = None
    if conn is not None:
        try:
            from psycopg.types.json import Json
            row = conn.execute(
                "INSERT INTO audit (workspace_id, source, text_sha256, as_of, doc_date, report) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING audit_id",
                (workspace_id, source, report.text_sha256, as_of_s,
                 doc_date.isoformat() if doc_date else None, Json(report.to_dict()))).fetchone()
            audit_id = row["audit_id"]
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("audit save failed: %s", exc)
    return {"audit_id": audit_id, **report.to_dict(), "markdown": render_markdown(report),
            "html": render_html(report, text)}


@app.post("/audit")
def audit(req: AuditRequest) -> dict:
    """Аудит текста или файла: статус каждой ссылки на дату, правки после даты документа, письма."""
    text, source = _audit_from_request(req)
    return _run_audit(text, source, req.as_of, req.doc_date)


# --- факты и таймлайн дела (F6) -------------------------------------------------------------
class FactCreate(BaseModel):
    kind: str = Field(pattern="^(date|amount|party|event|period|regime|other)$")
    value: str
    text: str
    source_path: str | None = None
    quote: str | None = None
    page: int | None = None
    role: str | None = None
    extracted_by: str = Field("lawyer", pattern="^(lawyer|agent)$")


@app.get("/workspaces/{slug}/facts")
def list_facts(slug: str, kind: str | None = None, confirmed: bool | None = None) -> dict:
    from .facts import FactStore
    store = FactStore(_ws(slug))
    return {"facts": store.list(kind=kind, confirmed=confirmed), "timeline": store.timeline()}


@app.post("/workspaces/{slug}/facts", status_code=201)
def add_fact(slug: str, req: FactCreate) -> dict:
    from .facts import FactError, FactStore
    ws = _ws(slug)
    try:
        fact = FactStore(ws).add(req.kind, req.value, req.text, req.source_path, req.quote, req.page, req.role,
                                 extracted_by=req.extracted_by)
    except FactError as exc:
        raise HTTPException(400, str(exc)) from exc
    _sync(ws)
    return fact.to_dict()


@app.post("/workspaces/{slug}/facts/{fact_id}/confirm")
def confirm_fact(slug: str, fact_id: int, confirmed: bool = True) -> dict:
    from .facts import FactError, FactStore
    ws = _ws(slug)
    try:
        fact = FactStore(ws).confirm(fact_id, confirmed)
    except FactError as exc:
        raise HTTPException(404, str(exc)) from exc
    _sync(ws)
    return fact.to_dict()


@app.delete("/workspaces/{slug}/facts/{fact_id}")
def delete_fact(slug: str, fact_id: int) -> dict:
    from .facts import FactError, FactStore
    ws = _ws(slug)
    try:
        FactStore(ws).remove(fact_id)
    except FactError as exc:
        raise HTTPException(404, str(exc)) from exc
    _sync(ws)
    return {"deleted": fact_id}


@app.get("/workspaces/{slug}/timeline")
def timeline(slug: str, confirmed_only: bool = False) -> list[dict]:
    from .facts import FactStore
    return FactStore(_ws(slug)).timeline(confirmed_only)


@app.post("/workspaces/{slug}/deadlines")
def derive_deadlines(slug: str, confirmed_only: bool = False, create_tasks: bool = True) -> dict:
    from .facts import FactStore
    ws = _ws(slug)
    out = FactStore(ws).derive_deadlines(ProductionCalendar.load(), confirmed_only, create_tasks)
    _sync(ws)
    return out


class WorkspaceAudit(BaseModel):
    path: str
    doc_date: date | None = None


@app.post("/workspaces/{slug}/audit")
def audit_workspace_file(slug: str, req: WorkspaceAudit) -> dict:
    ws = _ws(slug)
    try:
        text = ws.text_of(req.path)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"нет файла {req.path}") from exc
    result = _run_audit(text, req.path, date.fromisoformat(ws.manifest.as_of), req.doc_date)
    from pathlib import Path as _P
    out_path = "research/аудит-" + _P(req.path).stem + ".md"
    ws.write_file(out_path, result["markdown"], {"summary": f"аудит {req.path}", "audit": result["counts"]})
    _sync(ws)
    return {**result, "report_path": out_path}
