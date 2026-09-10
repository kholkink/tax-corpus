"""Проверка цитат в тексте ответа (слой 6 плана, шаг 5: «цитируй или откажись»).

Каждая ссылка на норму в свободном тексте («подп. 4 п. 1 ст. 218 НК РФ»,
«пункт 2 статьи 88 Кодекса», «ст. 6.1 НК») извлекается, резолвится в канонический
unit_id и проверяется на действие на дату. Ссылки, которые не резолвятся или не
действуют, помечаются — ответ с ними отправляется на переработку.

Работает без БД: индекс строится из записей парсера (UnitIndex), интервалы
действия — из правок (repeal_dates), как в загрузчике.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .amendments import amendments_from_records, repeal_dates
from .resolver import Resolution, UnitIndex, resolve_citation

_NUM = r"\d+(?:\.\d+)?(?:-\d+)?"
_ORD = (r"(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят|одиннадцат|двенадцат"
        r"|тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат|восемнадцат|девятнадцат"
        r"|двадцат)\w*")

# короткая или полная цитата: [абзац N] [подп./подпункт N] [п./пункт N] ст./статья N [НК РФ]
RE_CITATION_IN_TEXT = re.compile(
    rf"(?<![\w.])"
    rf"(?:(?:абз\.?|абзац\w*)\s*(?P<par>{_ORD}|\d+)\s*)?"
    rf"(?:(?:подп\.?|пп\.?|подпункт\w*)\s*(?P<sub>{_NUM})\s*)?"
    rf"(?:(?:п\.?|пункт\w*)\s*(?P<point>{_NUM})\s*)?"
    rf"(?:ст\.?|стать\w+)\s*(?P<article>{_NUM})"
    r"(?P<act>\s*(?:НК\s*РФ|НК|Налогового\s+кодекса(?:\s+Российской\s+Федерации|\s+РФ)?|Кодекса))?",
    re.IGNORECASE,
)

_ORD_VALUES = {
    "перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8,
    "девят": 9, "десят": 10, "одиннадцат": 11, "двенадцат": 12, "тринадцат": 13,
    "четырнадцат": 14, "пятнадцат": 15, "шестнадцат": 16, "семнадцат": 17,
    "восемнадцат": 18, "девятнадцат": 19, "двадцат": 20,
}


@dataclass
class CitationCheck:
    raw: str
    unit_id: str | None
    status: str          # ok | not_in_force | unresolved | partial
    resolution: str      # resolved | partial | unresolved
    depth: str | None
    note: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@dataclass
class VerificationReport:
    as_of: str
    checks: list[CitationCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status == "ok" for c in self.checks)

    @property
    def problems(self) -> list[CitationCheck]:
        return [c for c in self.checks if c.status != "ok"]

    def render(self) -> str:
        lines = [f"проверка цитат на {self.as_of}: {len(self.checks)} ссылок, "
                 f"проблем {len(self.problems)}"]
        for c in self.checks:
            mark = {"ok": "OK  ", "not_in_force": "STALE", "partial": "PART",
                    "unresolved": "MISS"}[c.status]
            lines.append(f"  {mark} «{c.raw}» -> {c.unit_id or '—'}"
                         + (f" ({c.note})" if c.note else ""))
        return "\n".join(lines)


def _ordinal(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    w = word.lower().replace("ё", "е")
    for stem, value in sorted(_ORD_VALUES.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(stem):
            return value
    return None


def extract_citations(text: str) -> list[dict]:
    """Текст -> [{raw, article, point?, subpoint?, paragraph_ordinal?}] в порядке вхождения."""
    out: list[dict] = []
    for m in RE_CITATION_IN_TEXT.finditer(text):
        item: dict = {"raw": m.group(0).strip(), "article": m.group("article")}
        if m.group("point"):
            item["point"] = m.group("point")
        if m.group("sub"):
            item["subpoint"] = m.group("sub")
        if m.group("par"):
            ordinal = _ordinal(m.group("par"))
            if ordinal:
                item["paragraph_ordinal"] = ordinal
        out.append(item)
    return out


class CitationVerifier:
    """Резолв и проверка действия цитат по записям парсера (обе части кодекса)."""

    def __init__(self, records: list[dict], edition_valid_from: dict[str, str] | None = None):
        self.index = UnitIndex(records)
        self.units = {r["unit_id"]: r for r in records}
        self.repealed = repeal_dates(amendments_from_records(records))
        # act -> дата редакции (начало интервала для неотменённых единиц)
        self.edition_from = edition_valid_from or {}

    def interval(self, unit_id: str) -> tuple[str | None, str | None]:
        if unit_id in self.repealed:
            act = self.units[unit_id]["act"]
            return None, self.repealed[unit_id] or self.edition_from.get(act)
        return self.edition_from.get(self.units[unit_id]["act"]), None

    def in_force(self, unit_id: str, as_of: str) -> bool:
        vf, vt = self.interval(unit_id)
        return (vf is None or vf <= as_of) and (vt is None or vt > as_of)

    def resolve(self, item: dict) -> Resolution:
        target = {"type": "unit", "article": item["article"]}
        for key in ("point", "subpoint", "paragraph_ordinal"):
            if key in item:
                target[key] = item[key]
        if "subpoint" in item and "point" not in item:
            return Resolution(None, "unresolved", None, "подпункт без пункта")
        return self.index.resolve_reference(target, "")

    def verify(self, text: str, as_of: str | date) -> VerificationReport:
        as_of = as_of.isoformat() if isinstance(as_of, date) else as_of
        report = VerificationReport(as_of=as_of)
        for item in extract_citations(text):
            res = self.resolve(item)
            if res.unit_id is None:
                report.checks.append(CitationCheck(item["raw"], None, "unresolved",
                                                   res.status, res.depth, res.note))
                continue
            vf, vt = self.interval(res.unit_id)
            if res.status == "partial":
                status, note = "partial", res.note
            elif not self.in_force(res.unit_id, as_of):
                status, note = "not_in_force", f"не действует на {as_of}: [{vf or '?'}, {vt or '∞'})"
            else:
                status, note = "ok", res.note
            report.checks.append(CitationCheck(item["raw"], res.unit_id, status, res.status,
                                               res.depth, note, vf, vt))
        return report


def verify_short(citation: str, index: UnitIndex) -> Resolution:
    """Совместимость: одна короткая цитата -> Resolution (как resolve_citation)."""
    return resolve_citation(citation, index)
