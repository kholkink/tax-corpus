"""Факты и таймлайн дела (F6 плана ПО).

Таблица фактов дела в workspaces/<slug>/facts.json: каждый факт — вид (date | amount | party |
event | period | regime | other), нормализованное значение, подпись, источник (файл дела, страница,
дословная цитата), кто извлёк (agent | lawyer) и подтверждён ли юристом. Факт агента принимается
только если цитата дословно есть в тексте файла-источника (с точностью до пробелов и переносов) —
иначе отклоняется. Роли (act_received, decision_received, decision_date, complaint_decision_date,
tax_due, tax_paid, offense, period_end) связывают факты с калькуляторами F5: derive_deadlines()
считает сроки процедуры и срок давности и ставит юристу задачи с датами.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .workspace import Workspace

KINDS = ("date", "amount", "party", "event", "period", "regime", "other")
ROLES = ("act_received", "decision_received", "decision_date", "complaint_decision_date",
         "tax_due", "tax_paid", "offense", "period_end", "declaration_due", "declaration_filed",
         "check_started", "check_ended")
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
          "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
RE_DATE_DOT = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)")
RE_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
RE_DATE_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|"
                           r"октября|ноября|декабря)\s+(\d{4})", re.I)
RE_AMOUNT = re.compile(r"(?<![\d,.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?!\d)")


class FactError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ").replace("«", '"').replace("»", '"')
                  .replace("—", "-").replace("–", "-")).strip().lower()


def parse_date(value) -> str:
    """'20.03.2026' | '20 марта 2026 г.' | '2026-03-20' | date -> 'YYYY-MM-DD'."""
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if m := RE_DATE_ISO.search(s):
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    if m := RE_DATE_DOT.search(s):
        y = int(m[3])
        y = y + 2000 if y < 100 else y
        return date(y, int(m[2]), int(m[1])).isoformat()
    if m := RE_DATE_WORDS.search(s):
        return date(int(m[3]), MONTHS[m[2].lower()], int(m[1])).isoformat()
    raise FactError(f"не распознана дата: {value!r}")


def parse_amount(value) -> float:
    """'1 200 000 руб.' | '1200000,50' | 1200000 -> 1200000.0 (рубли)."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    m = RE_AMOUNT.search(s)
    if not m:
        raise FactError(f"не распознана сумма: {value!r}")
    whole = re.sub(r"[   ]", "", m[1])
    amount = float(whole + ("." + m[2] if m[2] else ""))
    low = s.lower()
    if "млн" in low:
        amount *= 1_000_000
    elif "тыс" in low:
        amount *= 1_000
    return amount


def parse_period(value) -> str:
    """'2024' | '1 квартал 2025' | '2024-01-01..2024-12-31' -> 'YYYY-MM-DD..YYYY-MM-DD'."""
    s = str(value).strip().lower()
    if m := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*(?:\.\.|—|-|–)\s*(\d{4}-\d{2}-\d{2})", s):
        return f"{m[1]}..{m[2]}"
    if m := re.fullmatch(r"(\d{4})(?:\s*г\.?|\s*год)?", s):
        return f"{m[1]}-01-01..{m[1]}-12-31"
    if m := re.search(r"([1-4])\s*(?:-?й\s*)?кв(?:артал)?\.?\s*(\d{4})", s):
        q, y = int(m[1]), int(m[2])
        last = date(y, q * 3, 1).replace(day=[31, 30, 30, 31][q - 1])
        return f"{y}-{q * 3 - 2:02d}-01..{last.isoformat()}"
    if m := re.search(r"(\d{4})\s*(?:год|г\.?)", s):
        return f"{m[1]}-01-01..{m[1]}-12-31"
    raise FactError(f"не распознан период: {value!r}")


def normalize(kind: str, value) -> str | float:
    if kind not in KINDS:
        raise FactError(f"вид факта должен быть одним из {', '.join(KINDS)}")
    if kind in ("date", "event"):
        return parse_date(value)
    if kind == "amount":
        return parse_amount(value)
    if kind == "period":
        return parse_period(value)
    return str(value).strip()


@dataclass
class Fact:
    fact_id: int
    kind: str
    value: str | float
    text: str
    source_path: str | None = None
    page: int | None = None
    quote: str | None = None
    role: str | None = None
    extracted_by: str = "agent"
    confirmed: bool = False
    created_at: str = field(default_factory=_now)
    confirmed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class FactStore:
    """facts.json дела: добавление с проверкой цитаты, подтверждение, таймлайн, вывод сроков."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.path = ws.path / "facts.json"
        self.facts: list[Fact] = []
        if self.path.exists():
            self.facts = [Fact(**{k: v for k, v in d.items() if k in Fact.__dataclass_fields__})
                          for d in json.loads(self.path.read_text(encoding="utf-8"))]

    def save(self) -> None:
        self.path.write_text(json.dumps([f.to_dict() for f in self.facts], ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def list(self, kind: str | None = None, confirmed: bool | None = None, role: str | None = None) -> list[dict]:
        out = [f for f in self.facts if (kind is None or f.kind == kind)
               and (confirmed is None or f.confirmed == confirmed) and (role is None or f.role == role)]
        return [f.to_dict() for f in out]

    def get(self, fact_id: int) -> Fact:
        for f in self.facts:
            if f.fact_id == fact_id:
                return f
        raise FactError(f"нет факта #{fact_id}")

    def verify_quote(self, source_path: str, quote: str) -> bool:
        try:
            text = self.ws.text_of(source_path)
        except FileNotFoundError as exc:
            raise FactError(f"файла-источника нет в деле: {source_path}") from exc
        return _norm_ws(quote) in _norm_ws(text)

    def add(self, kind: str, value, text: str, source_path: str | None = None, quote: str | None = None,
            page: int | None = None, role: str | None = None, extracted_by: str = "agent",
            confirmed: bool | None = None) -> Fact:
        """Факт агента обязан иметь источник и дословную цитату; факт юриста — нет (он и есть источник)."""
        if role is not None and role not in ROLES:
            raise FactError(f"роль должна быть одной из {', '.join(ROLES)} или null")
        if role is not None and kind not in ("date", "event", "period"):
            raise FactError("роль задаётся только фактам-датам (kind date | event | period); "
                            "для суммы или стороны укажите role = null")
        if extracted_by == "agent":
            if not source_path or not quote:
                raise FactError("факт агента должен ссылаться на файл дела (source_path) и дословную цитату (quote)")
            if not self.verify_quote(source_path, quote):
                raise FactError(f"цитата не найдена дословно в {source_path}: факт отклонён; "
                                "скопируйте фрагмент из файла без изменений")
        elif source_path and quote and not self.verify_quote(source_path, quote):
            raise FactError(f"цитата не найдена дословно в {source_path}")
        norm = normalize(kind, value)
        if confirmed is None:
            confirmed = extracted_by == "lawyer"
        fact = Fact(fact_id=1 + max((f.fact_id for f in self.facts), default=0), kind=kind, value=norm,
                    text=text.strip(), source_path=source_path, page=page, quote=quote, role=role,
                    extracted_by=extracted_by, confirmed=confirmed,
                    confirmed_at=_now() if confirmed else None)
        self.facts.append(fact)
        self.save()
        return fact

    def confirm(self, fact_id: int, confirmed: bool = True) -> Fact:
        f = self.get(fact_id)
        f.confirmed, f.confirmed_at = confirmed, (_now() if confirmed else None)
        self.save()
        return f

    def remove(self, fact_id: int) -> None:
        self.get(fact_id)
        self.facts = [f for f in self.facts if f.fact_id != fact_id]
        self.save()

    # --- производные -------------------------------------------------------------------
    def timeline(self, confirmed_only: bool = False) -> list[dict]:
        """События и даты по порядку; периоды — по началу, с концом."""
        items = []
        for f in self.facts:
            if confirmed_only and not f.confirmed:
                continue
            if f.kind in ("date", "event"):
                items.append({"date": f.value, "end": None, "text": f.text, "kind": f.kind, "role": f.role,
                              "fact_id": f.fact_id, "confirmed": f.confirmed, "source_path": f.source_path})
            elif f.kind == "period":
                start, end = str(f.value).split("..")
                items.append({"date": start, "end": end, "text": f.text, "kind": f.kind, "role": f.role,
                              "fact_id": f.fact_id, "confirmed": f.confirmed, "source_path": f.source_path})
        items.sort(key=lambda i: (i["date"], i["end"] or ""))
        return items

    def by_role(self, confirmed_only: bool = False) -> dict[str, Fact]:
        out: dict[str, Fact] = {}
        for f in self.facts:
            if f.role and f.kind in ("date", "event", "period") and (f.confirmed or not confirmed_only) \
                    and f.role not in out:
                out[f.role] = f
        return out

    def derive_deadlines(self, calendar=None, confirmed_only: bool = False, create_tasks: bool = True) -> dict:
        """Сроки процедуры (ст. 100/101/139/139.1) и давность (ст. 113) из фактов с ролями -> задачи."""
        from .calculators import appeal_deadlines, limitation_status

        roles = self.by_role(confirmed_only)
        def as_date(r: str) -> date | None:
            if r not in roles:
                return None
            v = str(roles[r].value)
            return date.fromisoformat(v.split("..")[-1])  # для периода роль period_end — его последний день
        out: dict = {"deadlines": [], "applied": [], "steps": [], "warnings": [], "tasks": [], "missing": []}
        kwargs = {k: as_date(k) for k in ("act_received", "decision_received", "decision_date", "complaint_decision_date")}
        if any(kwargs.values()):
            res = appeal_deadlines(cal=calendar, **kwargs)
            labels = {"objections_until": "возражения на акт проверки (п. 6 ст. 100 НК)",
                      "decision_in_force": "решение вступает в силу (п. 9 ст. 101 НК)",
                      "appeal_until": "апелляционная жалоба (п. 2 ст. 139.1 НК)",
                      "complaint_until": "жалоба на вступившее в силу решение (п. 2 ст. 139 НК)",
                      "fns_complaint_until": "жалоба в ФНС России (п. 2 ст. 139 НК)"}
            for key, due in res.value.items():
                out["deadlines"].append({"key": key, "due": due, "title": labels.get(key, key),
                                         "basis": [r for r in ("act_received", "decision_received", "decision_date",
                                                               "complaint_decision_date") if kwargs[r]]})
            out["applied"] += res.applied
            out["steps"] += res.steps
            out["warnings"] += res.warnings
        else:
            out["missing"].append("даты с ролями act_received / decision_received / decision_date для сроков процедуры")
        if "decision_date" in roles and ("offense" in roles or "period_end" in roles):
            period_end = as_date("period_end")
            res = limitation_status(as_date("offense"), as_date("decision_date"),
                                    article="122" if period_end and "offense" not in roles else None,
                                    period_end=period_end, cal=calendar)
            out["limitation"] = res.value
            out["applied"] += res.applied
            out["steps"] += res.steps
            out["warnings"] += res.warnings
        out["applied"] = sorted(set(out["applied"]))
        if create_tasks:
            existing = {t["title"] for t in self.ws.tasks()}
            for d in out["deadlines"]:
                if d["key"] == "decision_in_force":
                    continue  # это не действие юриста, а событие
                title = f"Срок: {d['title']} — до {d['due']}"
                if title not in existing:
                    task = self.ws.create_task(title, d["due"], "выведено из фактов дела: " + ", ".join(d["basis"]))
                    out["tasks"].append(task)
        return out


FACT_TOOLS: list[dict] = [
    {"name": "list_facts",
     "description": "Таблица фактов дела (даты, суммы, стороны, события, периоды, режимы) с источниками и "
                    "признаком подтверждения юристом. В выводах опирайтесь на подтверждённые факты или на "
                    "текст файлов дела; неподтверждённые факты — только как гипотезы.",
     "input_schema": {"type": "object",
                      "properties": {"kind": {"type": ["string", "null"], "enum": [*KINDS, None]},
                                     "confirmed_only": {"type": "boolean"}},
                      "required": ["kind", "confirmed_only"], "additionalProperties": False},
     "strict": True},
    {"name": "add_fact",
     "description": "Добавить факт, извлечённый из файла дела. quote — ДОСЛОВНЫЙ фрагмент файла source_path "
                    "(скопируйте без изменений; иначе факт будет отклонён). value: дата (ДД.ММ.ГГГГ или "
                    "ГГГГ-ММ-ДД), сумма в рублях, период ('2024', '1 квартал 2025'), название стороны и т.п. "
                    "role связывает факт со сроками: act_received (получен акт проверки), decision_received "
                    "(вручено решение), decision_date (вынесено решение), complaint_decision_date, tax_due, "
                    "tax_paid, offense, period_end, declaration_due, declaration_filed, check_started, "
                    "check_ended — только для дат/событий/периодов; иначе null. Факт остаётся "
                    "неподтверждённым до проверки юристом.",
     "input_schema": {"type": "object",
                      "properties": {"kind": {"type": "string", "enum": list(KINDS)},
                                     "value": {"type": "string"},
                                     "text": {"type": "string", "description": "короткая подпись факта"},
                                     "source_path": {"type": "string"},
                                     "quote": {"type": "string"},
                                     "page": {"type": ["integer", "null"]},
                                     "role": {"type": ["string", "null"], "enum": [*ROLES, None]}},
                      "required": ["kind", "value", "text", "source_path", "quote", "page", "role"],
                      "additionalProperties": False},
     "strict": True},
    {"name": "derive_deadlines",
     "description": "Рассчитать по фактам с ролями сроки процедуры (возражения на акт, вступление решения "
                    "в силу, апелляция, жалобы) и срок давности ст. 113 через калькуляторы со ссылками на "
                    "нормы; поставить юристу задачи с датами. Сначала добавьте факты-даты с ролями.",
     "input_schema": {"type": "object", "properties": {"confirmed_only": {"type": "boolean"}},
                      "required": ["confirmed_only"], "additionalProperties": False},
     "strict": True},
]
FACT_TOOL_NAMES = {t["name"] for t in FACT_TOOLS}
