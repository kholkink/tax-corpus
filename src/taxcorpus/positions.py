"""Карта позиций по норме (F4 плана ПО).

Позиция = что документ (письмо Минфина/ФНС, пленум, обзор или определение ВС, акт КС) говорит
о конкретной норме: stance (pro_taxpayer | pro_authority | neutral), краткая формулировка,
дословная цитата из документа. Извлекает дешёвая модель по каждому ребру interprets; цитата
принимается только при точном вхождении в текст документа (детерминизм), иначе позиция
отклоняется. Хранение: data/interpretations/positions.jsonl (источник истины, в git) и таблица
position в БД (зеркало). Карта позиций группирует по stance с приоритетом источников и помечает
позиции, изданные до последней правки нормы («относится к прежней редакции»).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .interpretations import PRIORITY
from .resolver import article_of_unit_id

STANCES = ("pro_taxpayer", "pro_authority", "neutral")
POSITIONS_PATH = Path("data/interpretations/positions.jsonl")

EXTRACT_SYSTEM = """Ты — налоговый юрист-аналитик. Тебе дают документ (письмо Минфина/ФНС, постановление
Пленума, обзор или определение суда) и одну норму Налогового кодекса, на которую он ссылается.
Определи, какую позицию документ занимает ИМЕННО по этой норме, и ответь строго JSON без пояснений:
{"stance": "pro_taxpayer" | "pro_authority" | "neutral" | "none",
 "summary": "одно предложение: что документ говорит о применении нормы",
 "quote": "ДОСЛОВНЫЙ фрагмент документа (1–3 предложения), из которого следует позиция",
 "confidence": 0.0–1.0}
Правила: stance pro_taxpayer — толкование в пользу налогоплательщика (расширяет права, ограничивает
налоговый орган, смягчает); pro_authority — в пользу налогового органа (ограничивает права, расширяет
обязанности, ужесточает); neutral — техническое разъяснение без выгоды одной стороне; none — документ
лишь упоминает норму, позиции по ней нет. quote копируй из текста без изменений и сокращений."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ").replace("«", '"').replace("»", '"')
                  .replace("—", "-").replace("–", "-").replace("ё", "е")).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    return bool(quote) and _norm(quote) in _norm(text)


@dataclass
class Position:
    position_id: str                 # <doc_id>#<unit_id>
    doc_id: str
    unit_id: str
    stance: str
    summary: str
    quote: str
    confidence: float = 1.0
    extracted_by: str = "llm"        # llm | manual
    model: str | None = None
    verified: bool = False           # подтверждена юристом
    verified_by: str | None = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)


class PositionStore:
    """positions.jsonl: добавление/обновление по position_id, чтение по единице/документу."""

    def __init__(self, path: str | Path = POSITIONS_PATH):
        self.path = Path(path)
        self.positions: dict[str, Position] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    p = Position(**{k: v for k, v in d.items() if k in Position.__dataclass_fields__})
                    self.positions[p.position_id] = p

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(p.to_dict(), ensure_ascii=False) + "\n"
                                     for p in self.positions.values()), encoding="utf-8")

    def upsert(self, p: Position) -> None:
        self.positions[p.position_id] = p

    def done_pairs(self) -> set[tuple[str, str]]:
        return {(p.doc_id, p.unit_id) for p in self.positions.values()}

    def for_unit(self, unit_id: str) -> list[Position]:
        article = article_of_unit_id(unit_id)
        out = []
        for p in self.positions.values():
            t = p.unit_id
            if t == unit_id or unit_id.startswith(t + ".") or t.startswith(unit_id + ".") or t == article:
                out.append(p)
        return out

    def for_doc(self, doc_id: str) -> list[Position]:
        return [p for p in self.positions.values() if p.doc_id == doc_id]


def parse_model_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def extract_position(client, model: str, doc, unit: dict, max_doc_chars: int = 12000) -> tuple[Position | None, str]:
    """Одна пара документ×норма -> Position или (None, причина). Цитата проверяется дословно."""
    label = unit.get("label") or unit["unit_id"]
    norm_text = (unit.get("full_text") or unit.get("text") or "")[:1500]
    doc_text = doc.text[:max_doc_chars]
    user = (f"НОРМА: {label} ({unit['unit_id']})\nТекст нормы: {norm_text}\n\n"
            f"ДОКУМЕНТ: {doc.agency or ''} {doc.kind} № {doc.number} от {doc.date}: {doc.title}\n\n{doc_text}")
    resp = client.messages.create(model=model, max_tokens=800, system=EXTRACT_SYSTEM,
                                  messages=[{"role": "user", "content": user}])
    text = "".join(getattr(b, "text", "") for b in resp.content)
    data = parse_model_json(text)
    if not data:
        return None, "модель не вернула JSON"
    stance = data.get("stance")
    if stance == "none" or stance not in STANCES:
        return None, "позиции по норме нет" if stance == "none" else f"неизвестный stance {stance!r}"
    quote = (data.get("quote") or "").strip()
    if not quote_in_text(quote, doc.text):
        return None, "цитата не найдена дословно в документе — позиция отклонена"
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    pos = Position(position_id=f"{doc.doc_id}#{unit['unit_id']}", doc_id=doc.doc_id, unit_id=unit["unit_id"],
                   stance=stance, summary=(data.get("summary") or "").strip()[:600], quote=quote[:1500],
                   confidence=max(0.0, min(1.0, confidence)), extracted_by="llm", model=model)
    return pos, "ok"


def extract_positions(client, model: str, docs: list, edges: list[dict], units: dict[str, dict],
                      store: PositionStore, limit: int | None = None, log=print) -> dict:
    """По рёбрам interprets (документ -> единица) извлекает позиции; пропускает уже сделанные пары.
    Один вызов модели на пару; limit — бюджет вызовов за запуск."""
    by_doc = {d.doc_id: d for d in docs}
    done = store.done_pairs()
    stats = {"calls": 0, "added": 0, "rejected": 0, "skipped": 0, "errors": 0}
    seen: set[tuple[str, str]] = set()
    for e in edges:
        key = (e["doc_id"], e["to_unit_id"])
        if key in seen or key in done or e["doc_id"] not in by_doc or e["to_unit_id"] not in units:
            stats["skipped"] += 1
            continue
        seen.add(key)
        if limit is not None and stats["calls"] >= limit:
            break
        stats["calls"] += 1
        try:
            pos, reason = extract_position(client, model, by_doc[e["doc_id"]], units[e["to_unit_id"]])
        except Exception as exc:  # noqa: BLE001 — сбой одной пары не роняет прогон
            stats["errors"] += 1
            log(f"  [error] {e['doc_id']} × {e['to_unit_id']}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if pos is None:
            stats["rejected"] += 1
            # запоминаем отказ, чтобы не платить повторно: stance=neutral не пишем, пишем метку none
            store.upsert(Position(position_id=f"{e['doc_id']}#{e['to_unit_id']}", doc_id=e["doc_id"],
                                  unit_id=e["to_unit_id"], stance="none", summary=reason, quote="",
                                  confidence=0.0, extracted_by="llm", model=model))
            log(f"  – {e['doc_id']} × {e['to_unit_id']}: {reason}")
            continue
        store.upsert(pos)
        stats["added"] += 1
        log(f"  + {e['doc_id']} × {e['to_unit_id']}: {pos.stance} ({pos.confidence:.2f}) {pos.summary[:80]}")
        if stats["calls"] % 10 == 0:
            store.save()
    store.save()
    return stats


SOURCE_RANK = PRIORITY  # КС > обзор/определение ВС > пленум > решение по жалобе > письмо


def position_map(unit_id: str, as_of: str, positions: list[Position], docs: dict[str, dict],
                 amendments: list[dict]) -> dict:
    """Карта позиций: по stance, с приоритетом источников, датами, статусом документа и пометкой
    «относится к прежней редакции» (документ старше последней правки нормы)."""
    last_change = max((a.get("effective_date") or "" for a in amendments if (a.get("effective_date") or "") <= as_of),
                      default=None) or None
    groups: dict[str, list[dict]] = {s: [] for s in STANCES}
    for p in positions:
        if p.stance not in STANCES:
            continue
        d = docs.get(p.doc_id) or {}
        doc_date = str(d.get("date") or d.get("doc_date") or "")
        if doc_date and doc_date > as_of:
            continue
        item = {"doc_id": p.doc_id, "kind": d.get("kind"), "agency": d.get("agency"), "number": d.get("number"),
                "date": doc_date or None, "title": d.get("title"), "mandatory": bool(d.get("mandatory")),
                "doc_status": d.get("status"), "unit_id": p.unit_id, "summary": p.summary, "quote": p.quote,
                "confidence": p.confidence, "verified": p.verified,
                "older_than_last_amendment": bool(last_change and doc_date and doc_date < last_change),
                "priority": SOURCE_RANK.get(d.get("kind") or "", 9)}
        groups[p.stance].append(item)
    for items in groups.values():
        items.sort(key=lambda i: (i["priority"], not i["mandatory"], i["date"] or ""), reverse=False)
        items.sort(key=lambda i: (i["priority"], not i["mandatory"]))
    conflict = bool(groups["pro_taxpayer"] and groups["pro_authority"])
    leading = None
    for stance in ("pro_taxpayer", "pro_authority"):
        if groups[stance]:
            top = groups[stance][0]
            if leading is None or top["priority"] < leading[1]:
                leading = (stance, top["priority"])
    return {"unit_id": unit_id, "as_of": as_of, "last_amendment": last_change,
            "counts": {s: len(v) for s, v in groups.items()}, "conflict": conflict,
            "leading_stance": leading[0] if leading else None,
            "note": ("позиции расходятся: выше в каждой группе — более авторитетный источник (КС > пленум > "
                     "обзор ВС > определение > письмо); проверьте дату документа и редакцию нормы"
                     if conflict else None),
            "positions": groups}


def positions_for_documents(store: PositionStore, doc_ids: list[str]) -> dict[str, dict]:
    """doc_id -> {unit_id: stance} для обогащения get_interpretations."""
    out: dict[str, dict] = {}
    for p in store.positions.values():
        if p.doc_id in doc_ids and p.stance in STANCES:
            out.setdefault(p.doc_id, {})[p.unit_id] = p.stance
    return out
