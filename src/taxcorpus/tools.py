"""Инструменты агента (слой 5 плана): детерминированные функции над корпусом.

Каждый инструмент возвращает структурированный результат с unit_id и цитируемым
текстом. Два бэкенда с одним интерфейсом:
  DbCorpus     — PostgreSQL (основной: полнотекстовый поиск, интервалы, параметры);
  LocalCorpus  — JSONL парсера без БД (тесты, отладка; поиск — грубый, по
                 совпадению слов, помечен approximate).
TOOL_DEFINITIONS — описания для function calling; execute_tool — диспетчер.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .amendments import amendments_from_records, repeal_dates
from .citations import CitationVerifier
from .deadlines import ProductionCalendar, compute_deadline
from .interpretations import InterpretationIndex, load_documents
from .resolver import UnitIndex, resolve_citation
from .terms import extract_terms


class Corpus(Protocol):
    def get_unit(self, unit_id: str, as_of: str) -> dict | None: ...
    def resolve_citation(self, citation: str, context_unit_id: str | None) -> dict: ...
    def search(self, query: str, as_of: str, limit: int) -> list[dict]: ...
    def list_amendments(self, unit_id: str, since: str | None) -> list[dict]: ...
    def get_parameter(self, name: str, as_of: str) -> dict | None: ...
    def find_terms(self, query: str, as_of: str) -> list[dict]: ...
    def get_interpretations(self, unit_id: str, as_of: str, limit: int) -> list[dict]: ...
    def verifier(self) -> CitationVerifier: ...
    def interpretations(self) -> InterpretationIndex: ...


# --- офлайн-бэкенд -----------------------------------------------------------------

def _stem(word: str) -> str:
    return word.lower().replace("ё", "е")[:5]


class LocalCorpus:
    """Корпус из data/processed/*_units.jsonl (+ amendments, meta, сид параметров)."""

    def __init__(self, data_dir: str | Path = "data/processed",
                 parameters_path: str | Path | None = "data/parameters/parameters_v0.json",
                 interpretations_dir: str | Path | None = "data/interpretations"):
        self.data_dir = Path(data_dir)
        self.records: list[dict] = []
        self.editions: dict[str, str] = {}
        for path in sorted(self.data_dir.glob("*_units.jsonl")):
            act = path.name.split("_")[0]
            meta_path = self.data_dir / f"{act}_meta.json"
            if meta_path.exists():
                self.editions[act] = json.loads(meta_path.read_text(encoding="utf-8")).get("valid_from")
            with path.open(encoding="utf-8") as fh:
                self.records.extend(json.loads(line) for line in fh if line.strip())
        self.units = {r["unit_id"]: r for r in self.records}
        self.index = UnitIndex(self.records)
        self.amendments = amendments_from_records(self.records)
        self.repealed = repeal_dates(self.amendments)
        self._verifier = CitationVerifier(self.records, self.editions)
        self.parameters: list[dict] = []
        if parameters_path and Path(parameters_path).exists():
            self.parameters = json.loads(Path(parameters_path).read_text(encoding="utf-8"))["parameters"]
        self.terms = extract_terms(self.records)
        docs = []
        if interpretations_dir and Path(interpretations_dir).is_dir():
            docs = load_documents(interpretations_dir)
        self._interpretations = InterpretationIndex(docs, self.index)

    @classmethod
    def from_records(cls, records: list[dict], editions: dict[str, str] | None = None,
                     parameters: list[dict] | None = None,
                     documents: list | None = None) -> "LocalCorpus":
        self = cls.__new__(cls)
        self.data_dir = Path(".")
        self.records = records
        self.editions = editions or {}
        self.units = {r["unit_id"]: r for r in records}
        self.index = UnitIndex(records)
        self.amendments = amendments_from_records(records)
        self.repealed = repeal_dates(self.amendments)
        self._verifier = CitationVerifier(records, self.editions)
        self.parameters = parameters or []
        self.terms = extract_terms(records)
        self._interpretations = InterpretationIndex(documents or [], self.index)
        return self

    def verifier(self) -> CitationVerifier:
        return self._verifier

    def interpretations(self) -> InterpretationIndex:
        return self._interpretations

    def get_interpretations(self, unit_id: str, as_of: str, limit: int = 10) -> list[dict]:
        return self._interpretations.get_interpretations(unit_id, as_of, limit)

    def _interval(self, unit_id: str) -> tuple[str | None, str | None]:
        return self._verifier.interval(unit_id)

    def get_unit(self, unit_id: str, as_of: str) -> dict | None:
        r = self.units.get(unit_id)
        if r is None or not self._verifier.in_force(unit_id, as_of):
            return None
        vf, vt = self._interval(unit_id)
        return {"unit_id": unit_id, "kind": r["kind"], "label": r["label"], "title": r["title"],
                "context": r.get("context"), "valid_from": vf, "valid_to": vt,
                "text": r["text"], "full_text": r.get("full_text") or r["text"],
                "edit_note": r.get("edit_note")}

    def resolve_citation(self, citation: str, context_unit_id: str | None = None) -> dict:
        res = resolve_citation(citation, self.index, context_unit_id)
        out = {"citation": citation, "unit_id": res.unit_id, "status": res.status,
               "depth": res.depth, "note": res.note}
        if res.unit_id and res.unit_id in self.units:
            out["label"] = self.units[res.unit_id]["label"]
        return out

    def search(self, query: str, as_of: str, limit: int = 10) -> list[dict]:
        stems = {_stem(w) for w in re.findall(r"[а-яёa-z0-9]+", query.lower()) if len(w) > 2}
        scored = []
        for r in self.records:
            if not r.get("is_chunk") or not self._verifier.in_force(r["unit_id"], as_of):
                continue
            text = (r.get("full_text") or r["text"]).lower()
            words = {_stem(w) for w in re.findall(r"[а-яёa-z0-9]+", text)}
            hit = len(stems & words)
            if hit:
                scored.append((hit / (1 + len(text) / 4000), r))
        scored.sort(key=lambda x: -x[0])
        return [{"unit_id": r["unit_id"], "label": r["label"], "context": r.get("context"),
                 "rank": round(s, 3), "snippet": (r.get("full_text") or r["text"])[:300],
                 "approximate": True} for s, r in scored[:limit]]

    def list_amendments(self, unit_id: str, since: str | None = None) -> list[dict]:
        rows = [a for a in self.amendments if a["target_unit_id"] == unit_id
                and (not since or (a.get("amending_act_date") or "") >= since)]
        return [{k: a.get(k) for k in ("operation", "scope", "amending_act_number",
                                       "amending_act_date", "effective_date", "raw_note")}
                for a in rows]

    def get_parameter(self, name: str, as_of: str) -> dict | None:
        for p in self.parameters:
            if p["name"] != name:
                continue
            if (p.get("valid_from") or "") <= as_of and (not p.get("valid_to") or p["valid_to"] > as_of):
                unit = self.get_unit(p["source_unit_id"], as_of)
                return {**p, "source_text": unit["full_text"] if unit else None,
                        "label": self.units[p["source_unit_id"]]["label"]}
        return None

    def find_terms(self, query: str, as_of: str) -> list[dict]:
        norm = query.lower().replace("ё", "е")
        rows = [t for t in self.terms if norm in t["term_norm"]]
        rows.sort(key=lambda t: (t["scope"] != "code", len(t["term"])))
        return [{**t, "label": self.units[t["definition_unit_id"]]["label"]} for t in rows]


# --- бэкенд PostgreSQL --------------------------------------------------------------

class DbCorpus:
    """Инструменты поверх PostgreSQL (db.py); проверка цитат — по записям JSONL
    того же корпуса (нужен data_dir с *_units.jsonl), чтобы не тянуть из БД 30 тыс. строк."""

    def __init__(self, conn, data_dir: str | Path = "data/processed"):
        from . import db as _db
        self.db = _db
        self.conn = conn
        self._local = LocalCorpus(data_dir, parameters_path=None)

    def verifier(self) -> CitationVerifier:
        return self._local.verifier()

    def get_unit(self, unit_id: str, as_of: str) -> dict | None:
        return self.db.get_unit(self.conn, unit_id, as_of)

    def resolve_citation(self, citation: str, context_unit_id: str | None = None) -> dict:
        return self._local.resolve_citation(citation, context_unit_id)

    def search(self, query: str, as_of: str, limit: int = 10) -> list[dict]:
        return self.db.search_units(self.conn, query, as_of, limit=limit)

    def list_amendments(self, unit_id: str, since: str | None = None) -> list[dict]:
        return self.db.list_amendments(self.conn, unit_id, since)

    def get_parameter(self, name: str, as_of: str) -> dict | None:
        return self.db.get_parameter(self.conn, name, as_of)

    def find_terms(self, query: str, as_of: str) -> list[dict]:
        return self.db.find_terms(self.conn, query, as_of)

    def interpretations(self) -> InterpretationIndex:
        return self._local.interpretations()

    def get_interpretations(self, unit_id: str, as_of: str, limit: int = 10) -> list[dict]:
        return self.db.get_interpretations(self.conn, unit_id, as_of, limit)


# --- описания инструментов для function calling --------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": "Полнотекстовый поиск норм НК РФ, действующих на дату. Возвращает "
                       "чанки (пункты/подпункты) с unit_id, ярлыком, контекстом заголовков и "
                       "сниппетом. Формулируй запрос словами кодекса (не аббревиатурами).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "поисковый запрос"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "resolve_citation",
        "description": "Короткая цитата («п. 2 ст. 88», «подп. 4 п. 1 ст. 218») -> канонический "
                       "unit_id. Статус partial значит, что найден только родитель.",
        "input_schema": {
            "type": "object",
            "properties": {"citation": {"type": "string"}},
            "required": ["citation"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_unit",
        "description": "Текст единицы (статьи/пункта/подпункта) в редакции, действующей на дату. "
                       "Пусто — единица не существует или не действует на дату.",
        "input_schema": {
            "type": "object",
            "properties": {"unit_id": {"type": "string", "description": "например nk1.ch14.art88.p2"}},
            "required": ["unit_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "list_amendments",
        "description": "История правок единицы: каким законом и с какой даты изменена/введена/отменена.",
        "input_schema": {
            "type": "object",
            "properties": {
                "unit_id": {"type": "string"},
                "since": {"type": ["string", "null"], "description": "YYYY-MM-DD или null"},
            },
            "required": ["unit_id", "since"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_parameter",
        "description": "Ставка/срок/лимит на дату из таблицы параметров с текстом-источником. "
                       "Имена: vat_rate_general, vat_rate_reduced, profit_tax_rate_general, "
                       "ndfl_rate_base, usn_rate_income, usn_rate_income_minus_expenses, psn_rate, "
                       "insurance_unified_tariff, desk_audit_duration, field_audit_duration, "
                       "field_audit_duration_max, late_filing_fine_rate, underpayment_fine_rate, "
                       "underpayment_fine_rate_willful, liability_limitation_period.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "find_terms",
        "description": "Определение термина из ст. 11 НК и отраслевых словарей (подстрока термина).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_interpretations",
        "description": "Разъяснения и практика по норме (письма Минфина/ФНС, постановления Пленума, "
                       "обзоры ВС), изданные не позже даты: номер, дата, обязательность, что "
                       "цитируют, выдержка. Ненормативные: показывай как позицию ведомства/суда с "
                       "датой; более авторитетные идут первыми (КС > ВС > пленум > ФНС > Минфин).",
        "input_schema": {
            "type": "object",
            "properties": {
                "unit_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["unit_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "compute_deadline",
        "description": "Срок по ст. 6.1 НК от даты события: дни (рабочие или календарные), "
                       "месяцы, кварталы, годы; учитывает перенос с выходного. Возвращает дату "
                       "окончания, шаги расчёта и unit_id применённых пунктов ст. 6.1.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "дата события YYYY-MM-DD"},
                "amount": {"type": "integer", "minimum": 1},
                "unit": {"type": "string", "enum": ["days", "calendar_days", "months", "quarters", "years"]},
            },
            "required": ["start", "amount", "unit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def execute_tool(corpus: Corpus, name: str, args: dict, as_of: str,
                 calendar: ProductionCalendar | None = None) -> tuple[str, bool]:
    """Выполняет инструмент; возвращает (JSON-строка результата, is_error)."""
    try:
        if name == "search":
            result = corpus.search(args["query"], as_of, int(args.get("limit") or 8))
        elif name == "resolve_citation":
            result = corpus.resolve_citation(args["citation"], None)
        elif name == "get_unit":
            result = corpus.get_unit(args["unit_id"], as_of)
            if result is None:
                result = {"unit_id": args["unit_id"], "found": False,
                          "note": f"единица не существует или не действует на {as_of}"}
        elif name == "list_amendments":
            result = corpus.list_amendments(args["unit_id"], args.get("since"))
        elif name == "get_parameter":
            result = corpus.get_parameter(args["name"], as_of)
            if result is None:
                result = {"name": args["name"], "found": False}
        elif name == "find_terms":
            result = corpus.find_terms(args["query"], as_of)
        elif name == "get_interpretations":
            result = corpus.get_interpretations(args["unit_id"], as_of, int(args.get("limit") or 5))
            if not result:
                result = {"unit_id": args["unit_id"], "documents": [],
                          "note": "в корпусе нет разъяснений по этой норме на дату"}
        elif name == "compute_deadline":
            r = compute_deadline(date.fromisoformat(args["start"]), int(args["amount"]),
                                 args["unit"], calendar)
            result = {"end": r.end.isoformat(), "nominal_end": r.nominal_end.isoformat(),
                      "shifted": r.shifted, "steps": r.steps, "applied_units": r.applied,
                      "calendar_note": r.calendar_note}
        else:
            return json.dumps({"error": f"неизвестный инструмент {name}"}, ensure_ascii=False), True
        return json.dumps(result, ensure_ascii=False, default=str), False
    except Exception as exc:  # инструмент не должен ронять цикл агента
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), True
