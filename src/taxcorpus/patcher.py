"""История редакций (F3 плана ПО): грамматика изменяющих законов и применитель к дереву единиц.

Текст федерального закона «О внесении изменений в … Налогового кодекса» разбирается на
инструкции: адрес (статья / пункт / подпункт / абзац) + операция + полезная нагрузка:
  replace_words   — слова "X" заменить словами "Y"
  insert_words    — после слов "X" дополнить словами "Y" / дополнить словами "Y"
  delete_words    — слова "X" исключить
  replace         — изложить в следующей редакции: "…"
  insert_unit     — дополнить пунктом N (подпунктом N, статьёй N) следующего содержания: "…"
  insert_paragraph— дополнить абзацем следующего содержания: "…"
  delete_paragraph— абзац N исключить
  repeal          — признать утратившим силу
Применение детерминировано: инструкция либо однозначно применяется к тексту (слова найдены
ровно один раз, единица существует), либо помечается failed и уходит в очередь сверки.
Round-trip: base + патчи должны давать текущий текст банка; расхождения — тоже в очередь.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

ORDINALS = {"первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "четвёртый": 4, "пятый": 5, "шестой": 6,
            "седьмой": 7, "восьмой": 8, "девятый": 9, "десятый": 10, "одиннадцатый": 11, "двенадцатый": 12,
            "тринадцатый": 13, "четырнадцатый": 14, "пятнадцатый": 15, "шестнадцатый": 16, "семнадцатый": 17,
            "восемнадцатый": 18, "девятнадцатый": 19, "двадцатый": 20}
ORD_RE = "|".join(sorted((k[:-2] for k in ORDINALS), key=len, reverse=True))   # основы: перв, втор …
NUM = r"\d+(?:[.,]\d+)*(?:-\d+)?"
QUOTE = r"[\"«„“”]"
RE_QUOTED = re.compile(QUOTE + r"(.*?)" + r"[\"»“”]", re.S)
RE_INTRO = re.compile(r"следующ\w+\s+изменени\w+:", re.I)
RE_ITEM = re.compile(r"(?m)^\s*(\d+)\)\s+")
RE_SUBITEM = re.compile(r"(?m)^\s*([а-я])\)\s+")
RE_EFFECTIVE = [
    (re.compile(r"вступает в силу с (\d{1,2}) (января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря) (\d{4})", re.I), "date"),
    (re.compile(r"вступает в силу по истечении (одного месяца|десяти дней|(\d+) дней) со дня (?:его )?официального опубликования", re.I), "after_publication"),
    (re.compile(r"вступает в силу со дня (?:его )?официального опубликования", re.I), "publication"),
    (re.compile(r"не ранее 1-го числа очередного налогового периода", re.I), "next_period"),
]
MONTHS = {m: i + 1 for i, m in enumerate(("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                                            "сентября", "октября", "ноября", "декабря"))}


def _article_uid(records: dict[str, dict], act: str, article: str) -> str | None:
    """unit_id статьи по номеру: идентификаторы несут главу (nk1.ch14.art88), поэтому ищем по суффиксу."""
    art = article.replace(".", "-")
    pat = re.compile(rf"^{re.escape(act)}\.(?:(?:r|ch|sub)[\w-]+\.)*art{re.escape(art)}$")
    for uid in records:
        if pat.match(uid):
            return uid
    return None


def _uid(records: dict[str, dict], act: str, article: str, point: str | None = None, sub: str | None = None) -> str | None:
    base = _article_uid(records, act, article)
    if base is None:
        return None
    uid = base
    if point:
        uid += f".p{point.replace('.', '-')}"
    if sub:
        uid += f".sp{sub.replace('.', '-')}"
    return uid


def _norm_quotes(text: str) -> str:
    return text.replace("„", '"').replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')


@dataclass
class Address:
    article: str | None = None
    point: str | None = None
    subpoint: str | None = None
    paragraph: int | None = None          # номер абзаца (1-based) внутри пункта/статьи
    title: bool = False


@dataclass
class Instruction:
    raw: str
    address: Address
    operation: str
    old: str | None = None
    new: str | None = None
    new_number: str | None = None         # номер добавляемого пункта/подпункта/статьи
    new_kind: str | None = None           # point | subpoint | article


@dataclass
class PatchResult:
    instruction: Instruction
    status: str                            # ok | failed | skipped
    unit_id: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    reason: str | None = None
    diff: str | None = None


# --- грамматика --------------------------------------------------------------------------------

def parse_address(text: str, ctx: Address | None = None) -> Address:
    """Адрес из фрагмента до операции: «в абзаце втором пункта 3 статьи 170», «пункт 6 статьи 100»,
    «подпункт 4 пункта 1 статьи 218», «статью 89», «наименование статьи 88»."""
    a = Address(**(ctx.__dict__ if ctx else {}))
    a.paragraph = None
    low = text.lower()
    m = re.search(r"стать[юиея]\s+(" + NUM + r")", low)
    if m:
        a.article, a.point, a.subpoint = m.group(1), None, None
    m = re.search(r"пункт[аеу]?\s+(" + NUM + r")", low)
    if m:
        a.point, a.subpoint = m.group(1), None
    m = re.search(r"подпункт[аеу]?\s+(" + NUM + r")", low)
    if m:
        a.subpoint = m.group(1)
    m = re.search(r"абзац[аеу]?\s+(" + ORD_RE + r")\w*", low)
    if m:
        stem = m.group(1)
        a.paragraph = next(v for k, v in ORDINALS.items() if k.startswith(stem))
    a.title = "наименовани" in low
    return a


def parse_instruction(item: str, ctx: Address | None = None) -> Instruction | None:
    text = _norm_quotes(re.sub(r"\s+", " ", item.strip()))
    low = text.lower()
    quoted = [q.strip() for q in RE_QUOTED.findall(text)]
    head = text.split('"')[0]
    addr = parse_address(head, ctx)
    if "утратив" in low and "силу" in low:
        return Instruction(text, addr, "repeal")
    if "слова" not in low and re.search(r"абзац\w*\s+(" + ORD_RE + r")\w*.*?\bисключить", low):
        return Instruction(text, addr, "delete_paragraph")
    if "изложить в следующей редакции" in low and quoted:
        return Instruction(text, addr, "replace", new=quoted[-1])
    m = re.search(r"дополнить (пунктом|подпунктом|статьей|статьёй) (" + NUM + r") следующего содержания", low)
    if m and quoted:
        kind = {"пунктом": "point", "подпунктом": "subpoint"}.get(m.group(1), "article")
        return Instruction(text, addr, "insert_unit", new=quoted[-1], new_number=m.group(2), new_kind=kind)
    if re.search(r"дополнить абзацем следующего содержания", low) and quoted:
        return Instruction(text, addr, "insert_paragraph", new=quoted[-1])
    if re.search(r"слова .*? заменить словами", low) and len(quoted) >= 2:
        return Instruction(text, addr, "replace_words", old=quoted[0], new=quoted[1])
    if re.search(r"после слов .*? дополнить словами", low) and len(quoted) >= 2:
        return Instruction(text, addr, "insert_words", old=quoted[0], new=quoted[1])
    if re.search(r"дополнить словами", low) and quoted:
        return Instruction(text, addr, "insert_words", old=None, new=quoted[-1])
    if re.search(r"слова .*? исключить", low) and quoted:
        return Instruction(text, addr, "delete_words", old=quoted[0])
    return None


def parse_law(text: str) -> list[Instruction]:
    """Инструкции закона: пункты «N)» и подпункты «а)» с наследованием адреса («в статье 100:»)."""
    text = _norm_quotes(text)
    m = RE_INTRO.search(text)
    body = text[m.end():] if m else text
    # хвост «Статья 2. Настоящий Федеральный закон вступает в силу …» не содержит инструкций
    tail = re.search(r"(?m)^\s*Статья \d+\.?\s*(?:\n|\s)?\s*(?:1\.\s*)?Настоящий Федеральный закон", body)
    if tail:
        body = body[:tail.start()]
    out: list[Instruction] = []
    items = RE_ITEM.split(body)
    for i in range(1, len(items), 2):
        item = items[i + 1].strip()
        if RE_SUBITEM.search(item) and re.match(r"^в\s+(стать|пункт|подпункт)", item.strip(), re.I):
            head, *subs = RE_SUBITEM.split(item)
            ctx = parse_address(head)
            for j in range(0, len(subs) - 1, 2):
                ins = parse_instruction(subs[j + 1], ctx)
                if ins:
                    out.append(ins)
            continue
        ins = parse_instruction(item)
        if ins:
            out.append(ins)
    return out


def effective_date_of_law(text: str, publish_date: date | None = None) -> tuple[date | None, str]:
    """Дата вступления по заключительной статье; (None, причина) — если нужна ручная оценка."""
    low = _norm_quotes(text).lower()
    tail = low[low.rfind("вступает в силу") - 200:] if "вступает в силу" in low else ""
    if not tail:
        return None, "не найдена формулировка о вступлении в силу"
    if "не ранее 1-го числа очередного налогового периода" in tail:
        return None, "дата зависит от налогового периода (п. 1 ст. 5 НК) — вручную"
    m = RE_EFFECTIVE[0][0].search(tail)
    if m:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))), "по тексту закона"
    m = RE_EFFECTIVE[1][0].search(tail)
    if m and publish_date:
        from datetime import timedelta
        span = m.group(1)
        if span.startswith("одного месяца"):
            month = publish_date.month % 12 + 1
            year = publish_date.year + (publish_date.month == 12)
            return date(year, month, min(publish_date.day, 28)), "месяц со дня опубликования"
        days = 10 if span.startswith("десяти") else int(m.group(2))
        return publish_date + timedelta(days=days), f"{days} дней со дня опубликования"
    if RE_EFFECTIVE[2][0].search(tail) and publish_date:
        return publish_date, "со дня опубликования"
    return None, "формулировка не распознана — вручную"


# --- применение --------------------------------------------------------------------------------

def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _target_unit(records: dict[str, dict], act: str, a: Address) -> str | None:
    if not a.article:
        return None
    uid = _uid(records, act, a.article, a.point, a.subpoint)
    return uid if uid in records else None


def _split_paragraphs(rec: dict) -> list[str]:
    paras = rec.get("paragraphs")
    if paras:
        return list(paras)
    return [p for p in (rec.get("text") or "").split("\n") if p.strip()]


def _diff(old: str, new: str) -> str:
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), "было", "стало", lineterm="", n=1))


def apply_instruction(records: dict[str, dict], act: str, ins: Instruction) -> PatchResult:
    """Применяет инструкцию к записям (in place). records: unit_id -> запись парсера (text, paragraphs …)."""
    a = ins.address
    if ins.operation == "insert_unit":
        if ins.new_kind == "article":
            parent = None
            chapter = _article_uid(records, act, a.article) if a.article else None
            prefix = chapter.rsplit(".art", 1)[0] if chapter else act
            new_uid = f"{prefix}.art{ins.new_number.replace('.', '-')}"
        else:
            parent = _uid(records, act, a.article, a.point) if ins.new_kind == "subpoint" else _uid(records, act, a.article)
            if parent is None or parent not in records:
                return PatchResult(ins, "failed", reason=f"нет родителя для {ins.new_kind} {ins.new_number}: {a}")
            new_uid = parent + (".sp" if ins.new_kind == "subpoint" else ".p") + ins.new_number.replace(".", "-")
        if new_uid in records:
            return PatchResult(ins, "failed", unit_id=new_uid, reason="единица уже существует")
        label = {"point": f"пункт {ins.new_number}", "subpoint": f"подпункт {ins.new_number}", "article": f"статья {ins.new_number}"}[ins.new_kind]
        text = ins.new.strip()
        records[new_uid] = {"unit_id": new_uid, "act": act, "kind": ins.new_kind, "parent_unit_id": parent,
                            "number": ins.new_number, "label": label + (f" статьи {a.article}" if parent else ""),
                            "text": text, "full_text": text, "paragraphs": [p for p in text.split("\n") if p.strip()],
                            "text_hash": _hash(text), "is_chunk": True, "inserted_by_patch": True}
        return PatchResult(ins, "ok", unit_id=new_uid, old_text="", new_text=text, diff=_diff("", text))
    uid = _target_unit(records, act, a)
    if uid is None:
        return PatchResult(ins, "failed", reason=f"единица не найдена: {a}")
    rec = records[uid]
    old_text = rec.get("text") or ""
    paras = _split_paragraphs(rec)
    if ins.operation == "repeal":
        rec["repealed_by_patch"] = True
        new_text = ""
    elif ins.operation == "replace":
        if a.paragraph:
            if a.paragraph > len(paras):
                return PatchResult(ins, "failed", unit_id=uid, reason=f"нет абзаца {a.paragraph}")
            paras[a.paragraph - 1] = ins.new.strip()
        else:
            paras = [p for p in ins.new.strip().split("\n") if p.strip()]
        new_text = "\n".join(paras)
    elif ins.operation == "insert_paragraph":
        paras.append(ins.new.strip())
        new_text = "\n".join(paras)
    elif ins.operation == "delete_paragraph":
        if not a.paragraph or a.paragraph > len(paras):
            return PatchResult(ins, "failed", unit_id=uid, reason=f"нет абзаца {a.paragraph}")
        del paras[a.paragraph - 1]
        new_text = "\n".join(paras)
    elif ins.operation in ("replace_words", "insert_words", "delete_words"):
        idx = (a.paragraph - 1) if a.paragraph else None
        if idx is not None and idx >= len(paras):
            return PatchResult(ins, "failed", unit_id=uid, reason=f"нет абзаца {a.paragraph}")
        scope = paras[idx] if idx is not None else "\n".join(paras)
        if ins.old is not None:
            n = scope.count(ins.old)
            if n != 1:
                return PatchResult(ins, "failed", unit_id=uid,
                                   reason=f"слова «{ins.old}» встречаются {n} раз (нужно ровно один)")
            if ins.operation == "replace_words":
                scope = scope.replace(ins.old, ins.new)
            elif ins.operation == "insert_words":
                scope = scope.replace(ins.old, ins.old + " " + ins.new)
            else:
                scope = re.sub(r"\s*" + re.escape(ins.old), "", scope, count=1)
        else:  # дополнить словами — в конец (перед завершающей точкой)
            scope = re.sub(r"([.;])?\s*$", lambda m: " " + ins.new + (m.group(1) or ""), scope, count=1)
        if idx is not None:
            paras[idx] = scope
            new_text = "\n".join(paras)
        else:
            new_text = scope
    else:
        return PatchResult(ins, "skipped", unit_id=uid, reason=f"операция {ins.operation} не поддержана")
    rec["text"] = new_text
    rec["paragraphs"] = [p for p in new_text.split("\n") if p.strip()]
    rec["text_hash"] = _hash(new_text)
    rec["patched"] = True
    return PatchResult(ins, "ok", unit_id=uid, old_text=old_text, new_text=new_text, diff=_diff(old_text, new_text))


def rebuild_full_text(records: dict[str, dict], order: list[str] | None = None) -> None:
    """full_text = собственный текст + full_text потомков (в исходном порядке)."""
    order = order or list(records)
    pos = {uid: i for i, uid in enumerate(order)}
    children: dict[str, list[str]] = {}
    for uid, rec in records.items():
        p = rec.get("parent_unit_id")
        if p:
            children.setdefault(p, []).append(uid)
    for p, lst in children.items():
        # исходный порядок; вставленная патчем единица — по номеру среди соседей
        lst.sort(key=lambda u: (pos.get(u, len(order)), _numkey(records[u].get("number")), u)
                 if u in pos else (min((pos[x] for x in children[p] if x in pos and _numkey(records[x].get("number")) > _numkey(records[u].get("number"))),
                                      default=len(order)) - 0.5, _numkey(records[u].get("number")), u))

    def build(uid: str) -> str:
        rec = records[uid]
        own = rec.get("text") or ""
        parts = [own] + [build(c) for c in children.get(uid, []) if not records[c].get("repealed_by_patch")]
        rec["full_text"] = "\n\n".join(p for p in parts if p)
        return rec["full_text"]

    for uid in order:
        if not records[uid].get("parent_unit_id"):
            build(uid)


def apply_law(records: dict[str, dict], act: str, law_text: str) -> list[PatchResult]:
    results = [apply_instruction(records, act, ins) for ins in parse_law(law_text)]
    rebuild_full_text(records)
    return results


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("ё", "е")).strip().lower()


def roundtrip(base: dict[str, dict], laws: list[str], act: str, current: dict[str, dict]) -> dict:
    """base + все законы = current? Возвращает расхождения (очередь сверки) и статистику."""
    import copy
    work = copy.deepcopy(base)
    results = []
    for law in laws:
        results.extend(apply_law(work, act, law))
    mismatches = []

    def repealed(rec: dict | None) -> bool:
        if rec is None:
            return True
        text = (rec.get("text") or "").strip().lower()
        return bool(rec.get("repealed_by_patch")) or not text or text.startswith("<утратил")

    for uid, rec in work.items():
        if rec.get("repealed_by_patch"):
            if not repealed(current.get(uid)):
                mismatches.append({"unit_id": uid, "kind": "expected_repealed", "current": current[uid].get("text", "")[:200]})
            continue
        cur = current.get(uid)
        if cur is None:
            mismatches.append({"unit_id": uid, "kind": "missing_in_current"})
        elif _norm_text(cur.get("text") or "") != _norm_text(rec.get("text") or ""):
            mismatches.append({"unit_id": uid, "kind": "text_differs", "diff": _diff(rec.get("text") or "", cur.get("text") or "")[:2000]})
    for uid in current:
        if uid not in work:
            mismatches.append({"unit_id": uid, "kind": "unexpected_in_current"})
    return {"patches": len(results), "ok": sum(1 for r in results if r.status == "ok"),
            "failed": [r for r in results if r.status != "ok"], "mismatches": mismatches}


# --- применение к БД -----------------------------------------------------------------------------

def records_from_db(conn, act_code: str, on: str | None = None) -> tuple[dict[str, dict], list[str]]:
    """Текущие (или на дату) записи единиц акта в формате парсера: text, paragraphs, parent, full_text."""
    on = on or date.today().isoformat()
    rows = conn.execute(
        """
        SELECT u.unit_id, u.parent_unit_id, u.kind, u.number, u.label, u.is_chunk, t.text, t.full_text, t.text_hash
        FROM unit u JOIN act a ON a.act_id = u.act_id
        JOIN unit_text t ON t.unit_id = u.unit_id
        WHERE a.act_code = %s AND (t.valid_from IS NULL OR t.valid_from <= %s) AND (t.valid_to IS NULL OR t.valid_to > %s)
        """, (act_code, on, on)).fetchall()
    records = {}
    for r in rows:
        rec = dict(r)
        rec["act"] = act_code
        rec["paragraphs"] = [p for p in (rec["text"] or "").split("\n") if p.strip()]
        records[rec["unit_id"]] = rec
    return records, structural_order(records)


def _numkey(number: str | None) -> tuple:
    """«2», «2.1», «18.1-1», «п» -> ключ сортировки по номеру единицы."""
    parts = re.split(r"[.\-]", number or "")
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in parts)


def structural_order(records: dict[str, dict]) -> list[str]:
    """Обход дерева: родители раньше детей, дети — по номеру (в БД нет позиции единицы)."""
    children: dict[str | None, list[str]] = {}
    for uid, rec in records.items():
        children.setdefault(rec.get("parent_unit_id"), []).append(uid)
    for lst in children.values():
        lst.sort(key=lambda u: (_numkey(records[u].get("number")), u))
    out: list[str] = []

    def walk(parent: str | None) -> None:
        for uid in children.get(parent, []):
            out.append(uid)
            walk(uid)

    walk(None)
    for uid in records:           # сироты (родитель вне выборки) — в конец
        if uid not in out:
            out.append(uid)
    return out


def apply_law_to_db(conn, act_code: str, law_text: str, number: str, adoption_date: str | None,
                    effective_date: str | None = None, publish_date: str | None = None,
                    dry_run: bool = False, source_url: str | None = None, eo_number: str | None = None) -> dict:
    """Закон -> патчи -> новые версии unit_text с effective_date (или очередь сверки при failed)."""
    from .db import add_unit_version, close_unit
    from psycopg.types.json import Json  # noqa: F401  (совместимость сигнатур)
    eff, note = (date.fromisoformat(effective_date), "задано вручную") if effective_date else \
        effective_date_of_law(law_text, date.fromisoformat(publish_date) if publish_date else None)
    records, order = records_from_db(conn, act_code)
    instructions = parse_law(law_text)
    results = [apply_instruction(records, act_code, ins) for ins in instructions]
    rebuild_full_text(records, order)
    status = "dry_run" if dry_run or eff is None else "auto"
    with conn.transaction():
        act_id = None
        if not dry_run:
            act_id = conn.execute(
                "INSERT INTO amending_act (number, adoption_date, publication_date, eo_number, source_url, text, effective_date, effective_note, retrieved_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (number, adoption_date) DO UPDATE SET text = EXCLUDED.text, "
                "effective_date = EXCLUDED.effective_date, effective_note = EXCLUDED.effective_note RETURNING amending_act_id",
                (number, adoption_date, publish_date, eo_number, source_url, law_text, eff, note)).fetchone()["amending_act_id"]
        applied = 0
        for r in results:
            st = status if r.status == "ok" else "failed"
            if not dry_run:
                conn.execute(
                    "INSERT INTO patch (amending_act_id, act_code, instruction, operation, target_unit_id, effective_date, applied_status, reason, diff) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (act_id, act_code, r.instruction.raw[:4000], r.instruction.operation, r.unit_id, eff, st, r.reason, (r.diff or "")[:20000]))
            if st != "auto" or r.unit_id is None:
                continue
            rec = records[r.unit_id]
            edit_note = f"<В ред. Федерального закона от {adoption_date or '?'} N {number} (с {eff.isoformat()})>"
            if r.instruction.operation == "repeal":
                close_unit(conn, r.unit_id, eff)
            elif r.instruction.operation == "insert_unit":
                parent_act = conn.execute("SELECT act_id FROM act WHERE act_code = %s", (act_code,)).fetchone()["act_id"]
                conn.execute(
                    "INSERT INTO unit (unit_id, act_id, parent_unit_id, kind, number, label, is_chunk) VALUES (%s, %s, %s, %s, %s, %s, true) "
                    "ON CONFLICT (unit_id) DO NOTHING",
                    (r.unit_id, parent_act, rec.get("parent_unit_id"), rec["kind"], rec["number"], rec["label"]))
                add_unit_version(conn, r.unit_id, rec["text"], rec["full_text"], eff, edit_note, {"patch": number})
            else:
                add_unit_version(conn, r.unit_id, rec["text"], rec["full_text"], eff, edit_note, {"patch": number})
            applied += 1
        # предки изменённых единиц получают новый full_text с той же даты
        if status == "auto":
            touched = {r.unit_id for r in results if r.status == "ok" and r.unit_id}
            ancestors = set()
            for uid in touched:
                p = records.get(uid, {}).get("parent_unit_id")
                while p:
                    ancestors.add(p)
                    p = records.get(p, {}).get("parent_unit_id")
            for uid in ancestors - touched:
                rec = records[uid]
                add_unit_version(conn, uid, rec["text"], rec["full_text"], eff, None, {"patch": number, "reason": "full_text предка"})
    return {"number": number, "effective_date": eff.isoformat() if eff else None, "effective_note": note,
            "instructions": len(instructions), "ok": sum(1 for r in results if r.status == "ok"),
            "failed": [{"instruction": r.instruction.raw[:200], "reason": r.reason} for r in results if r.status != "ok"],
            "applied": applied if status == "auto" else 0, "status": status,
            "results": [{"operation": r.instruction.operation, "unit_id": r.unit_id, "status": r.status, "reason": r.reason,
                         "diff": r.diff} for r in results]}
