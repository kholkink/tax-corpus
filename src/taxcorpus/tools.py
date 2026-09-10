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
from .embeddings import DenseIndex, rrf
from .interpretations import InterpretationIndex, load_documents
from .positions import PositionStore, position_map, positions_for_documents
from .resolver import UnitIndex, resolve_citation
from .terms import extract_terms


def matched_terms(query: str, text: str) -> list[str]:
    """Слова запроса, основы которых встречаются в тексте единицы (объяснимость поиска, F12)."""
    words = [w for w in re.findall(r"[а-яёa-z0-9]+", query.lower()) if len(w) > 2]
    text_stems = {_stem(w) for w in re.findall(r"[а-яёa-z0-9]+", text.lower())}
    out, seen = [], set()
    for w in words:
        st = _stem(w)
        if st in text_stems and st not in seen:
            seen.add(st)
            out.append(w)
    return out


def explain_hit(row: dict, query: str, text: str) -> dict:
    """Добавляет к результату поиска matched_terms и why: чем найдено и какие слова совпали."""
    terms = matched_terms(query, text)
    sources = row.get("sources") or ([row["pass"]] if row.get("pass") in ("strict", "loose", "dense") else ["lexical"])
    parts = []
    if "lexical" in sources or row.get("pass") in ("strict", "loose"):
        parts.append("все слова запроса" if row.get("pass") == "strict" else "часть слов запроса")
    if "dense" in sources:
        parts.append("близко по смыслу")
    why = "; ".join(parts) or "лексическое совпадение"
    if terms:
        why += " — совпали: " + ", ".join(terms)
    elif "dense" in sources:
        why += " (лексических совпадений нет)"
    row["matched_terms"] = terms
    row["why"] = why
    return row


class HybridSearch:
    """Слияние лексического и семантического поиска (RRF), слой 4 плана.

    lexical(query, as_of, n) -> [row]; dense — DenseIndex (если индекс построен).
    Метаданные dense-хитов берутся из записей корпуса; действие на дату — через verifier.
    """

    def __init__(self, records: dict[str, dict], verifier, dense: DenseIndex | None = None,
                 depth: int = 30):
        self.records = records
        self.verifier = verifier
        self.dense = dense
        self.depth = depth

    @property
    def enabled(self) -> bool:
        return self.dense is not None and self.dense.ready

    def _text(self, uid: str) -> str:
        r = self.records.get(uid) or {}
        return r.get("full_text") or r.get("text") or ""

    def search(self, query: str, as_of: str, limit: int, lexical) -> list[dict]:
        lex_rows = lexical(query, as_of, self.depth)
        if not self.enabled:
            return [explain_hit(dict(r), query, self._text(r["unit_id"])) for r in lex_rows[:limit]]
        allowed = {uid for uid, r in self.records.items()
                   if r.get("is_chunk") and self.verifier.in_force(uid, as_of)}
        dense_hits = self.dense.search(query, limit=self.depth, allowed=allowed)
        fused = rrf([[r["unit_id"] for r in lex_rows], [uid for uid, _ in dense_hits]])
        by_lex = {r["unit_id"]: r for r in lex_rows}
        dense_score = dict(dense_hits)
        out = []
        for uid, score in fused[:limit]:
            row = by_lex.get(uid)
            if row is None:
                r = self.records[uid]
                row = {"unit_id": uid, "kind": r["kind"], "label": r["label"], "title": r.get("title"),
                       "context": r.get("context"), "pass": "dense",
                       "snippet": (r.get("full_text") or r["text"])[:300]}
            else:
                row = dict(row)
            row["rank"] = round(score, 4)
            row["sources"] = [s for s, ok in (("lexical", uid in by_lex), ("dense", uid in dense_score)) if ok]
            out.append(explain_hit(row, query, self._text(uid)))
        return out


class Corpus(Protocol):
    def get_unit(self, unit_id: str, as_of: str) -> dict | None: ...
    def resolve_citation(self, citation: str, context_unit_id: str | None) -> dict: ...
    def search(self, query: str, as_of: str, limit: int) -> list[dict]: ...
    def list_amendments(self, unit_id: str, since: str | None) -> list[dict]: ...
    def get_parameter(self, name: str, as_of: str) -> dict | None: ...
    def find_terms(self, query: str, as_of: str) -> list[dict]: ...
    def get_interpretations(self, unit_id: str, as_of: str, limit: int) -> list[dict]: ...
    def search_interpretations(self, query: str, as_of: str, limit: int) -> list[dict]: ...
    def verifier(self) -> CitationVerifier: ...
    def interpretations(self) -> InterpretationIndex: ...
    def get_position_map(self, unit_id: str, as_of: str) -> dict: ...


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
        self.hybrid = HybridSearch(self.units, self._verifier, DenseIndex())

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
        self.hybrid = HybridSearch(self.units, self._verifier, None)
        return self

    def verifier(self) -> CitationVerifier:
        return self._verifier

    def interpretations(self) -> InterpretationIndex:
        return self._interpretations

    @property
    def positions(self) -> PositionStore:
        store = getattr(self, "_position_store", None)
        if store is None:
            store = PositionStore(self.data_dir.parent / "interpretations" / "positions.jsonl")
            self._position_store = store
        return store

    def get_position_map(self, unit_id: str, as_of: str) -> dict:
        docs = {d.doc_id: d.summary() for d in self.interpretations().docs.values()}
        return position_map(unit_id, as_of, self.positions.for_unit(unit_id), docs,
                            self.list_amendments(unit_id, None))

    def _with_stances(self, rows: list[dict]) -> list[dict]:
        stances = positions_for_documents(self.positions, [r["doc_id"] for r in rows])
        for r in rows:
            r["positions"] = stances.get(r["doc_id"], {})
        return rows

    def get_interpretations(self, unit_id: str, as_of: str, limit: int = 10) -> list[dict]:
        return self._with_stances(self._interpretations.get_interpretations(unit_id, as_of, limit))

    def search_interpretations(self, query: str, as_of: str, limit: int = 5) -> list[dict]:
        return self._interpretations.search(query, as_of, limit)

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
        return self.hybrid.search(query, as_of, limit, self.search_lexical)

    def search_lexical(self, query: str, as_of: str, limit: int = 10) -> list[dict]:
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
        self.hybrid = self._local.hybrid

    def verifier(self) -> CitationVerifier:
        return self._local.verifier()

    def get_unit(self, unit_id: str, as_of: str) -> dict | None:
        return self.db.get_unit(self.conn, unit_id, as_of)

    def resolve_citation(self, citation: str, context_unit_id: str | None = None) -> dict:
        return self._local.resolve_citation(citation, context_unit_id)

    def search(self, query: str, as_of: str, limit: int = 10) -> list[dict]:
        return self.hybrid.search(query, as_of, limit, self.search_lexical)

    def search_lexical(self, query: str, as_of: str, limit: int = 10) -> list[dict]:
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
        rows = self.db.get_interpretations(self.conn, unit_id, as_of, limit)
        stances = self.db.positions_by_docs(self.conn, [r["doc_id"] for r in rows])
        for r in rows:
            r["positions"] = stances.get(r["doc_id"], {})
        return rows

    def get_position_map(self, unit_id: str, as_of: str) -> dict:
        from .positions import Position
        rows = self.db.get_positions(self.conn, unit_id)
        positions = [Position(**{k: v for k, v in r.items() if k in Position.__dataclass_fields__}) for r in rows]
        docs = self.db.documents_by_ids(self.conn, sorted({r["doc_id"] for r in rows}))
        return position_map(unit_id, as_of, positions, docs, self.list_amendments(unit_id, None))

    def search_interpretations(self, query: str, as_of: str, limit: int = 5) -> list[dict]:
        return self.db.search_documents(self.conn, query, as_of, limit)


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
        "name": "get_position_map",
        "description": "Карта позиций по норме: что говорят о ней письма Минфина/ФНС, пленумы, обзоры и "
                       "определения ВС, акты КС — сгруппировано по stance (pro_taxpayer / pro_authority / "
                       "neutral), с приоритетом источников, датами, дословными цитатами и пометкой, если "
                       "документ старше последней правки нормы. conflict = true — позиции расходятся: "
                       "покажи обе стороны. Вызывай после get_unit, когда вопрос спорный.",
        "input_schema": {
            "type": "object",
            "properties": {"unit_id": {"type": "string"}},
            "required": ["unit_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "search_interpretations",
        "description": "Поиск по разъяснениям (письма ФНС/Минфина, пленумы) по теме, а не по номеру "
                       "статьи: заголовок и текст. Возвращает номер, дату, статус актуальности, "
                       "теги по статьям НК. Дальше — get_interpretations по найденной норме.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "compute_penalty",
        "description": "Пени по ст. 75 НК на сумму недоимки за период просрочки (по день уплаты "
                       "включительно) с ключевой ставкой ЦБ по дням; для организаций — правила "
                       "1/300 и 1/150 по периодам (п. 4, 5, 5.1 ст. 75). Возвращает сумму, шаги по "
                       "сегментам ставки и unit_id применённых пунктов.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "недоимка, руб."},
                "due_date": {"type": "string", "description": "срок уплаты YYYY-MM-DD"},
                "paid_date": {"type": "string", "description": "дата уплаты YYYY-MM-DD"},
                "taxpayer": {"type": "string", "enum": ["organization", "individual"]},
            },
            "required": ["amount", "due_date", "paid_date", "taxpayer"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "compute_fine",
        "description": "Штраф: ст. 119 (5 % в месяц, 30 % max, 1000 руб. min; нужны due_date и "
                       "actual_date), ст. 122 (20 %), 122_willful (40 %), ст. 126 (200 руб. × documents); "
                       "смягчающие — уменьшение не менее чем вдвое (п. 3 ст. 114), повторность — +100 % "
                       "(п. 4 ст. 114).",
        "input_schema": {
            "type": "object",
            "properties": {
                "article": {"type": "string", "enum": ["119", "122", "122_willful", "126"]},
                "base": {"type": "number", "description": "неуплаченная сумма, руб. (для 119/122)"},
                "due_date": {"type": ["string", "null"]},
                "actual_date": {"type": ["string", "null"]},
                "documents": {"type": "integer", "minimum": 0},
                "mitigating": {"type": "array", "items": {"type": "string"}},
                "aggravating": {"type": "boolean"},
                "reduction_factor": {"type": "number", "minimum": 2, "maximum": 10},
            },
            "required": ["article", "base", "due_date", "actual_date", "documents", "mitigating",
                         "aggravating", "reduction_factor"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "appeal_deadlines",
        "description": "Сроки процедуры: возражения на акт (месяц, п. 6 ст. 100), вступление решения в "
                       "силу и апелляция (п. 9 ст. 101, п. 2 ст. 139.1), жалоба на вступившее решение "
                       "(год, ст. 139), жалоба в ФНС (3 месяца). Укажи известные даты, остальные — null.",
        "input_schema": {
            "type": "object",
            "properties": {
                "act_received": {"type": ["string", "null"]},
                "decision_received": {"type": ["string", "null"]},
                "decision_date": {"type": ["string", "null"]},
                "complaint_decision_date": {"type": ["string", "null"]},
            },
            "required": ["act_received", "decision_received", "decision_date", "complaint_decision_date"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "limitation_status",
        "description": "Истёк ли срок давности привлечения к ответственности (3 года, ст. 113) к дате "
                       "решения. Для ст. 120, 122, 129.3, 129.5 срок идёт со следующего дня после "
                       "окончания налогового периода (period_end), иначе — со дня правонарушения.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_date": {"type": "string"},
                "offense_date": {"type": ["string", "null"]},
                "article": {"type": ["string", "null"], "description": "например 122"},
                "period_end": {"type": ["string", "null"]},
            },
            "required": ["decision_date", "offense_date", "article", "period_end"],
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


def _d(value):
    return date.fromisoformat(value) if value else None


def run_calculator(name: str, args: dict, calendar: ProductionCalendar | None = None) -> dict:
    """Калькуляторы F5 (calculators.py) как инструменты агента / API."""
    from . import calculators as C
    if name == "compute_penalty":
        r = C.compute_penalty(float(args["amount"]), _d(args["due_date"]), _d(args["paid_date"]),
                              args.get("taxpayer") or "organization")
    elif name == "compute_fine":
        r = C.compute_fine(args["article"], float(args.get("base") or 0), _d(args.get("due_date")),
                           _d(args.get("actual_date")), int(args.get("documents") or 0),
                           args.get("mitigating") or None, bool(args.get("aggravating")),
                           float(args.get("reduction_factor") or 2.0))
    elif name == "appeal_deadlines":
        r = C.appeal_deadlines(_d(args.get("act_received")), _d(args.get("decision_received")),
                               _d(args.get("decision_date")), _d(args.get("complaint_decision_date")), calendar)
    elif name == "limitation_status":
        r = C.limitation_status(_d(args.get("offense_date")), _d(args["decision_date"]), args.get("article"),
                                _d(args.get("period_end")), calendar)
    else:
        raise ValueError(f"неизвестный калькулятор {name}")
    return r.to_dict()


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
        elif name == "search_interpretations":
            result = corpus.search_interpretations(args["query"], as_of, int(args.get("limit") or 5))
        elif name == "get_interpretations":
            result = corpus.get_interpretations(args["unit_id"], as_of, int(args.get("limit") or 5))
            if not result:
                result = {"unit_id": args["unit_id"], "documents": [],
                          "note": "в корпусе нет разъяснений по этой норме на дату"}
        elif name == "get_position_map":
            result = corpus.get_position_map(args["unit_id"], as_of)
            if not any(result["counts"].values()):
                result["note"] = "позиций по этой норме в реестре нет (документы не извлекались или не найдены)"
        elif name in ("compute_penalty", "compute_fine", "appeal_deadlines", "limitation_status"):
            result = run_calculator(name, args, calendar)
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
        conn = getattr(corpus, "conn", None)
        if conn is not None:
            try:
                conn.rollback()  # снять прерванную транзакцию, если соединение не autocommit
            except Exception:  # noqa: BLE001
                pass
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), True
