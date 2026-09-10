"""Аудит документа (F1 плана ПО): каждая ссылка в тексте — существует ли, действует ли на
дату, изменилась ли после даты документа, какие письма по ней сняты, чего не хватает.

Собирается из готовых частей: citations (нормы), interpretations (письма/пленумы),
references (федеральные законы), amendments (правки после даты документа), реестр
разъяснений (снятые письма и обязательные письма ФНС, которые документ не упоминает).
Результат детерминирован; модель не участвует.
"""

from __future__ import annotations

import hashlib
import html
from dataclasses import asdict, dataclass, field
from datetime import date

from .citations import extract_citations
from .references import RE_FEDERAL, RE_PAIR_FEDERAL

STATUS_LABEL = {"ok": "OK", "stale": "НЕ ДЕЙСТВУЕТ", "missing": "НЕТ В КОРПУСЕ", "partial": "НЕТОЧНО",
                "unknown_doc": "НЕТ В РЕЕСТРЕ", "outdated_doc": "ПИСЬМО СНЯТО", "external": "ВНЕШНИЙ АКТ"}


@dataclass
class AuditItem:
    raw: str
    kind: str                     # norm | document | federal_law
    start: int
    end: int
    status: str                   # ok | stale | missing | partial | unknown_doc | outdated_doc | external
    target_id: str | None = None
    label: str | None = None
    note: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    changed_since_doc: list[dict] = field(default_factory=list)   # правки после даты документа
    outdated_letters: list[dict] = field(default_factory=list)    # снятые письма по норме


@dataclass
class AuditReport:
    as_of: str
    doc_date: str | None
    text_sha256: str
    items: list[AuditItem]
    suggestions: list[dict]        # обязательные письма ФНС по цитируемым нормам, не упомянутые

    @property
    def counts(self) -> dict:
        out: dict[str, int] = {}
        for it in self.items:
            out[it.status] = out.get(it.status, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return all(it.status in ("ok", "external") for it in self.items)

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "doc_date": self.doc_date, "text_sha256": self.text_sha256,
                "counts": self.counts, "ok": self.ok, "items": [asdict(i) for i in self.items],
                "suggestions": self.suggestions}


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def audit_text(corpus, text: str, as_of: str, doc_date: str | None = None) -> AuditReport:
    """corpus — LocalCorpus/DbCorpus (tools.py). as_of — дата, на которую проверяется действие."""
    verifier = corpus.verifier()
    interp = corpus.interpretations()
    items: list[AuditItem] = []
    cited_units: set[str] = set()
    cited_doc_numbers: set[str] = set()

    # --- нормы -------------------------------------------------------------------------
    for c in extract_citations(text):
        res = verifier.resolve(c)
        item = AuditItem(raw=c["raw"], kind="norm", start=c["start"], end=c["end"], status="missing",
                         note=res.note)
        if res.unit_id is None:
            items.append(item)
            continue
        item.target_id = res.unit_id
        item.label = verifier.units.get(res.unit_id, {}).get("label")
        vf, vt = verifier.interval(res.unit_id)
        item.valid_from, item.valid_to = vf, vt
        if res.status == "partial":
            item.status = "partial"
        elif not verifier.in_force(res.unit_id, as_of):
            item.status = "stale"
            item.note = f"не действует на {as_of}: интервал [{vf or '?'}, {vt or '∞'})"
        else:
            item.status = "ok"
        cited_units.add(res.unit_id)
        # правки после даты документа — норма могла измениться с момента написания
        if doc_date:
            for a in corpus.list_amendments(res.unit_id, None) or []:
                eff = _iso(a.get("effective_date")) or _iso(a.get("amending_act_date"))
                if eff and eff > doc_date and a.get("scope", "unit") == "unit":
                    item.changed_since_doc.append({"operation": a.get("operation"),
                                                   "law": a.get("amending_act_number"),
                                                   "effective_date": eff})
        # письма по норме, снятые с применения
        for d in corpus.get_interpretations(res.unit_id, as_of, 20) or []:
            if d.get("status") == "outdated":
                item.outdated_letters.append({"doc_id": d.get("doc_id"), "number": d.get("number"),
                                              "date": _iso(d.get("date")), "title": d.get("title")})
        items.append(item)

    # --- письма и постановления ---------------------------------------------------------
    for d in interp.verify_doc_citations(text):
        pos = text.find(d["raw"])
        item = AuditItem(raw=d["raw"], kind="document", start=pos, end=pos + len(d["raw"]),
                         status="ok" if d["status"] == "ok" else "unknown_doc", target_id=d.get("doc_id"),
                         note=None if d["status"] == "ok" else "документа нет в реестре разъяснений")
        if d.get("doc_id"):
            doc = interp.docs.get(d["doc_id"])
            if doc is not None:
                item.label = f"{doc.agency} {doc.number} от {doc.date}"
                cited_doc_numbers.add(doc.number)
                if doc.status == "outdated":
                    item.status = "outdated_doc"
                    item.note = "письмо снято с применения налоговыми органами"
        items.append(item)

    # --- федеральные законы (только фиксируем как внешние) ----------------------------
    for m in RE_FEDERAL.finditer(text):
        pairs = RE_PAIR_FEDERAL.findall(m.group(0))
        if pairs:
            items.append(AuditItem(raw=m.group(0), kind="federal_law", start=m.start(), end=m.end(),
                                   status="external", note=", ".join(f"{n} от {d}" for d, n in pairs)))

    items.sort(key=lambda i: i.start)

    # --- чего не хватает: обязательные письма ФНС по цитируемым нормам --------------------
    suggestions: list[dict] = []
    seen_docs: set[str] = set()
    for unit_id in sorted(cited_units):
        for d in corpus.get_interpretations(unit_id, as_of, 10) or []:
            if d.get("mandatory") and d.get("status") != "outdated" and d.get("number") not in cited_doc_numbers \
                    and d.get("doc_id") not in seen_docs:
                seen_docs.add(d["doc_id"])
                suggestions.append({"for_unit": unit_id, "doc_id": d["doc_id"], "number": d.get("number"),
                                    "date": _iso(d.get("date")), "title": d.get("title")})
    return AuditReport(as_of=as_of, doc_date=doc_date,
                       text_sha256="sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       items=items, suggestions=suggestions[:20])


def render_markdown(report: AuditReport) -> str:
    c = report.counts
    lines = [f"# Аудит документа (нормы на {report.as_of}"
             + (f", документ от {report.doc_date}" if report.doc_date else "") + ")", "",
             f"Ссылок: {len(report.items)}; " + ", ".join(f"{STATUS_LABEL[k]}: {v}" for k, v in c.items()), "",
             "| # | ссылка | статус | цель | примечание |", "|---|---|---|---|---|"]
    for i, it in enumerate(report.items, 1):
        note = it.note or ""
        if it.changed_since_doc:
            note += (" " if note else "") + "менялась после даты документа: " + "; ".join(
                f"{a['operation']} {a['law']} с {a['effective_date']}" for a in it.changed_since_doc)
        if it.outdated_letters:
            note += (" " if note else "") + "сняты письма: " + "; ".join(
                f"{d['number']} от {d['date']}" for d in it.outdated_letters)
        lines.append(f"| {i} | {it.raw} | {STATUS_LABEL[it.status]} | {it.label or it.target_id or '—'} | {note} |")
    if report.suggestions:
        lines += ["", "## Не упомянуты обязательные письма ФНС по цитируемым нормам", ""]
        for s in report.suggestions:
            lines.append(f"- {s['number']} от {s['date']} — {s['title']} (по {s['for_unit']})")
    return "\n".join(lines) + "\n"


def render_html(report: AuditReport, text: str) -> str:
    """Исходный текст с подсветкой ссылок по статусу (для страницы «Аудит»)."""
    parts, pos = [], 0
    for it in report.items:
        if it.start < pos:
            continue
        parts.append(html.escape(text[pos:it.start]))
        title = f"{STATUS_LABEL[it.status]}" + (f": {it.label}" if it.label else "") + (f" — {it.note}" if it.note else "")
        parts.append(f'<mark class="audit {it.status}" title="{html.escape(title)}">{html.escape(text[it.start:it.end])}</mark>')
        pos = it.end
    parts.append(html.escape(text[pos:]))
    return "".join(parts).replace("\n", "<br>")
