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
from pydantic import BaseModel, Field

from .deadlines import ProductionCalendar, compute_deadline
from .tools import DbCorpus, LocalCorpus

app = FastAPI(title="tax-corpus", version="0.1.0",
              description="Корпус налогового права РФ: нормы на дату, ссылки, параметры, "
                          "разъяснения, сроки, агент с проверкой цитат")


@lru_cache(maxsize=1)
def corpus():
    db_url = os.environ.get("TAXCORPUS_DB")
    data_dir = os.environ.get("TAXCORPUS_DATA", "data/processed")
    if db_url:
        from .db import connect
        return DbCorpus(connect(db_url), data_dir)
    return LocalCorpus(data_dir)


def _as_of(value: str | None) -> str:
    return value or date.today().isoformat()


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
    model: str = "claude-opus-5"
    effort: str = "high"


@app.post("/ask")
def ask(req: AskRequest) -> dict[str, Any]:
    try:
        import anthropic
    except ImportError as exc:
        raise HTTPException(501, "агент недоступен: pip install -e '.[agent]'") from exc
    from .agent import TaxAgent
    agent = TaxAgent(anthropic.Anthropic(), corpus(), model=req.model, effort=req.effort)
    return agent.ask(req.question, req.as_of).to_dict()
