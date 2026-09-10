"""Разъяснительный и судебный слой (план, §3.2–3.3, слой 3: ребро interprets).

Документ — письмо Минфина/ФНС, решение по жалобе, постановление Пленума, обзор ВС.
Хранится целиком с провенансом; ссылки на нормы НК извлекаются тем же регэкспом,
что и внутри кодекса (references.extract_references), и резолвятся в unit_id по
индексу корпуса. Каждая такая связь — ребро interprets с датой документа: письмо
может быть привязано к устаревшей редакции, это показывается явно.

Формат входного JSONL (data/interpretations/*.jsonl), по документу на строку:
  {"doc_id": "minfin-2024-03-11-03-07-11-21467", "kind": "letter",
   "agency": "Минфин", "number": "03-07-11/21467", "date": "2024-03-11",
   "title": "...", "text": "...", "source_url": "...", "mandatory": false,
   "retrieved_at": "...", "sha256": "..."}
kind: letter | appeal_decision | plenum | review | ruling | constitutional.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .references import extract_references
from .resolver import UnitIndex, article_of_unit_id

KINDS = ("letter", "appeal_decision", "plenum", "review", "ruling", "constitutional")

# приоритет источников при конфликте (план, §9): КС > ВС > пленум > ФНС «обязательное» > Минфин
PRIORITY = {"constitutional": 0, "review": 1, "ruling": 1, "plenum": 2,
            "appeal_decision": 4, "letter": 5}

# «письмо ФНС России от 11.03.2024 № БС-4-11/2702@», «письмом Минфина от 1 марта 2024 г. N 03-07-11/1»
RE_DOC_CITATION = re.compile(
    r"(?<!\w)(?P<kind>письм\w*|постановлени\w*\s+Пленума|обзор\w*|определени\w*|решени\w*)"
    r"[^\n№N]{0,80}?от\s+(?P<date>\d{1,2}[.\s]\d{1,2}[.\s]\d{4}|\d{1,2}\s+[а-я]+\s+\d{4})"
    r"(?:\s*г\.?)?\s*(?:№|N)\s*(?P<number>[0-9А-Яа-яA-Za-z@/\-.]+)",
    re.IGNORECASE,
)

_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
           "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}


def normalize_date(raw: str) -> str | None:
    m = re.match(r"(\d{1,2})[.\s](\d{1,2})[.\s](\d{4})", raw)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", raw, re.IGNORECASE)
    if m and m.group(2).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def normalize_number(raw: str) -> str:
    return raw.strip().rstrip(".,;)").upper().replace(" ", "")


@dataclass
class Document:
    doc_id: str
    kind: str
    agency: str
    number: str
    date: str
    title: str
    text: str
    source_url: str | None = None
    mandatory: bool = False
    retrieved_at: str | None = None
    sha256: str | None = None
    status: str | None = None          # actual | outdated (по пометке источника) | None
    tags: list[str] | None = None      # теги источника: «Статья 200 НК РФ», категория
    category: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        if d.get("kind") not in KINDS:
            raise ValueError(f"{d.get('doc_id')}: kind должен быть одним из {KINDS}")
        text = d["text"]
        return cls(
            doc_id=d["doc_id"], kind=d["kind"], agency=d.get("agency", ""),
            number=d["number"], date=d["date"], title=d.get("title", ""), text=text,
            source_url=d.get("source_url"), mandatory=bool(d.get("mandatory", False)),
            retrieved_at=d.get("retrieved_at"),
            sha256=d.get("sha256") or "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
            status=d.get("status"), tags=d.get("tags"), category=d.get("category"),
        )

    def summary(self) -> dict:
        return {"doc_id": self.doc_id, "kind": self.kind, "agency": self.agency,
                "number": self.number, "date": self.date, "title": self.title,
                "mandatory": self.mandatory, "status": self.status, "category": self.category,
                "tags": self.tags, "source_url": self.source_url}


def load_documents(path_or_dir: str | Path) -> list[Document]:
    p = Path(path_or_dir)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    docs: list[Document] = []
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    docs.append(Document.from_dict(json.loads(line)))
    return docs


def link_document(doc: Document, index: UnitIndex) -> list[dict]:
    """Документ -> рёбра interprets: [{doc_id, to_unit_id, raw_citation, status, confidence}]."""
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ref in extract_references(doc.doc_id, doc.text):
        if ref["kind"] != "internal_citation":
            continue
        res = index.resolve_reference(ref["target"], doc.doc_id)
        if res.unit_id is None:
            continue
        key = (res.unit_id, ref["raw_citation"])
        if key in seen:
            continue
        seen.add(key)
        edges.append({"doc_id": doc.doc_id, "to_unit_id": res.unit_id, "kind": "interprets",
                      "raw_citation": ref["raw_citation"], "status": res.status,
                      "confidence": 1.0 if res.status == "resolved" else 0.7})
    # теги источника («Статья 200 НК РФ») — явная привязка от ведомства
    for tag in doc.tags or []:
        m = re.match(r"Стать[яи]\s+(\d+(?:\.\d+)?(?:-\d+)?)", tag)
        if not m:
            continue
        res = index.resolve_reference({"type": "unit", "article": m.group(1)}, doc.doc_id)
        if res.unit_id and (res.unit_id, tag) not in seen:
            seen.add((res.unit_id, tag))
            edges.append({"doc_id": doc.doc_id, "to_unit_id": res.unit_id, "kind": "interprets",
                          "raw_citation": tag, "status": "tag", "confidence": 1.0})
    return edges


class InterpretationIndex:
    """Реестр документов + рёбра interprets; get_interpretations(unit_id, as_of)."""

    def __init__(self, docs: list[Document], index: UnitIndex):
        self.docs = {d.doc_id: d for d in docs}
        self.edges: list[dict] = []
        for d in docs:
            self.edges.extend(link_document(d, index))
        self.by_number: dict[tuple[str, str], Document] = {
            (normalize_number(d.number), d.date): d for d in docs}
        self.numbers: dict[str, list[Document]] = {}
        for d in docs:
            self.numbers.setdefault(normalize_number(d.number), []).append(d)

    def get_interpretations(self, unit_id: str, as_of: str, limit: int = 10) -> list[dict]:
        """Документы, ссылающиеся на единицу, её предков в статье или её потомков,
        изданные не позже as_of; сначала более авторитетные и обязательные, затем новые."""
        article = article_of_unit_id(unit_id)
        hits: dict[str, list[str]] = {}
        for e in self.edges:
            t = e["to_unit_id"]
            related = (t == unit_id or unit_id.startswith(t + ".") or t.startswith(unit_id + ".")
                       or t == article)
            if related and self.docs[e["doc_id"]].date <= as_of:
                hits.setdefault(e["doc_id"], []).append(e["raw_citation"])
        docs = sorted(hits, key=lambda i: (PRIORITY[self.docs[i].kind], not self.docs[i].mandatory,
                                            self.docs[i].date), reverse=False)
        docs.sort(key=lambda i: (PRIORITY[self.docs[i].kind], not self.docs[i].mandatory))
        out = []
        for doc_id in docs[:limit]:
            d = self.docs[doc_id]
            out.append({**d.summary(), "cites": sorted(set(hits[doc_id])),
                        "excerpt": d.text[:600]})
        return out

    def search(self, query: str, as_of: str, limit: int = 5) -> list[dict]:
        """Грубый офлайн-поиск по документам (совпадение основ слов; заголовок весомее)."""
        stems = {w.lower().replace("ё", "е")[:5] for w in re.findall(r"[а-яёa-z0-9]+", query.lower())
                 if len(w) > 2}
        scored = []
        for d in self.docs.values():
            if d.date > as_of:
                continue
            title_words = {w.lower().replace("ё", "е")[:5] for w in re.findall(r"[а-яёa-z0-9]+", d.title.lower())}
            text_words = {w.lower().replace("ё", "е")[:5] for w in re.findall(r"[а-яёa-z0-9]+", d.text.lower())}
            score = 2 * len(stems & title_words) + len(stems & text_words)
            if score:
                scored.append((score, d))
        scored.sort(key=lambda x: (-x[0], x[1].date), reverse=False)
        return [{**d.summary(), "rank": s, "snippet": d.text[:300], "approximate": True}
                for s, d in scored[:limit]]

    def verify_doc_citations(self, text: str) -> list[dict]:
        """Ссылки на письма/постановления в ответе -> [{raw, number, date, status}];
        status ok — документ есть в реестре; unknown — нет (агент не имел права цитировать)."""
        out = []
        for m in RE_DOC_CITATION.finditer(text):
            number = normalize_number(m.group("number"))
            d = normalize_date(m.group("date"))
            found = self.by_number.get((number, d)) if d else None
            if found is None and number in self.numbers:
                found = self.numbers[number][0] if len(self.numbers[number]) == 1 else None
            out.append({"raw": m.group(0).strip(), "number": number, "date": d,
                        "status": "ok" if found else "unknown",
                        "doc_id": found.doc_id if found else None})
        return out
