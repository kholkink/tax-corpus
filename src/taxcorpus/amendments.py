"""Извлечение атомарных правок (Amendment) из пометок редакции банка.

Пометки банка — единственный машиночитаемый источник истории правок:
«<В новой ред. Федерального закона от 27 июля 2006 г. N 137-ФЗ>»,
«<Введена Федеральным законом от 24 ноября 2014 N 376-ФЗ>»,
«<Утратил силу с 1 января 2023 г.: Федеральный закон от 14 июля 2022 N 263-ФЗ>».
Даты в них словесные, номер с латинской «N» — обе особенности конвертируются.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# «от 27 июля 2006 г.» / «от 8 июня 2015» (год может быть без «г.»)
RE_WORD_DATE = re.compile(
    rf"от\s+(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(\d{{4}})(?:\s*г(?:ода|\.?))?", re.IGNORECASE)

# «от 23.11.2020» / «от 31.07.98» (двузначный год = 20xx)
RE_NUMERIC_DATE = re.compile(r"от\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})")

# «N 374-ФЗ», «№ 137-ФЗ»
RE_LAW_NUMBER = re.compile(r"(?:№|N)\s*(\d+(?:-\d+)*)-ФЗ", re.IGNORECASE)

# операции по началу пометки; более специфичные — раньше общих
OPERATIONS: tuple[tuple[str, str], ...] = (
    ("наименование в ред", "title_change"),
    ("наименование изложено", "title_change"),
    ("в новой ред", "replace"),
    ("изложен", "replace"),
    ("в ред", "replace"),
    ("введен", "insert"),
    ("дополнен", "insert"),
    ("изменен", "modify"),
    ("утратил силу", "repeal"),
)


def _norm_number(raw: str) -> str | None:
    m = re.search(r"(\d+(?:-\d+)*)-ФЗ", raw, re.IGNORECASE)
    return f"{m.group(1)}-ФЗ" if m else None


def parse_word_date(text: str) -> date | None:
    """«27 июля 2006 г.» -> date(2006, 7, 27); без совпадения — None."""
    m = RE_WORD_DATE.search(text)
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    return date(year, _MONTHS[month_name], day)


def _numeric_date(text: str) -> date | None:
    m = RE_NUMERIC_DATE.search(text)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000 if year < 50 else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


RE_LAW_FRAGMENT = re.compile(r"от[^,>]{0,70}?(?:№|N)\s*\d+(?:-\d+)*-ФЗ", re.IGNORECASE)


def law_pairs(note: str) -> list[tuple[str, str]]:
    """Пометка -> [(номер закона, дата ISO), ...] в порядке упоминания.

    Разделитель законов — запятая/конец пометки, поэтому «от … г. N 137-ФЗ»
    (точка после «г») не рвёт пару.
    """
    return [(number, law_date) for number, law_date, _ in law_entries(note)]


def law_entries(note: str) -> list[tuple[str, str, str | None]]:
    """Пометка -> [(номер закона, дата закона ISO, дата вступления ISO | None), ...].

    Дата вступления берётся из хвоста ПОСЛЕ конкретного закона и до следующего:
    «…N 176-ФЗ (изменения вступают в силу с 1 января 2025 г.), …N 564-ФЗ , …»;
    для пометок «Утратил силу с 1 января 2023 г.: ФЗ …» — из головы пометки.
    """
    matches = list(RE_LAW_FRAGMENT.finditer(note))
    head_effective = effective_date_of(note[:matches[0].start()]) if matches else None
    entries: list[tuple[str, str, str | None]] = []
    for idx, match in enumerate(matches):
        number = _norm_number(match.group(0))
        if not number:
            continue
        d = parse_word_date(match.group(0)) or _numeric_date(match.group(0))
        tail_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(note)
        effective = effective_date_of(note[match.end():tail_end]) or head_effective
        entries.append((number, d.isoformat() if d else "",
                        effective.isoformat() if effective else None))
    return entries


# дата вступления правки в силу: «Утратил силу с 1 января 2023 г.», «со 2 августа 2021 г.»
RE_EFFECTIVE_DATE = re.compile(
    rf"\bсо?\s+(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(\d{{4}})", re.IGNORECASE)


def effective_date_of(note: str) -> date | None:
    """«… с 1 января 2023 г.: Федеральный закон …» -> date(2023, 1, 1); иначе None."""
    m = RE_EFFECTIVE_DATE.search(note)
    if not m:
        return None
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None


# пометка о ЧАСТИ единицы: «<Абзац пятый утратил силу: …>», «<Пункт 3 в ред. …>» —
# висит на родителе, но описывает вложенную единицу; отменять родителя по ней нельзя
RE_CHILD_SCOPE = re.compile(r"^<?\s*(?:абзац|пункт|подпункт)\w*\s", re.IGNORECASE)


def scope_of(note: str, unit_kind: str | None = None) -> str:
    """'unit' — пометка о самой единице; 'child' — о её абзаце/пункте/подпункте.

    Пометка «<Абзац … утратил силу>», привязанная парсером к самому абзацу
    (unit_kind == paragraph), — о самой единице.
    """
    if unit_kind == "paragraph" and re.match(r"^<?\s*абзац", note, re.IGNORECASE):
        return "unit"
    return "child" if RE_CHILD_SCOPE.match(note) else "unit"


def operation_of(note: str) -> str | None:
    """Операция по ключевому слову, встретившемуся в пометке РАНЬШЕ других:
    «<Утратил силу …; в ред. …>» — repeal, а не replace."""
    low = note.lower()
    best: tuple[int, int, str] | None = None
    for priority, (keyword, op) in enumerate(OPERATIONS):
        pos = low.find(keyword)
        if pos >= 0 and (best is None or (pos, priority) < best[:2]):
            best = (pos, priority, op)
    return best[2] if best else None


def amendments_from_note(unit_id: str, note: str | None, unit_kind: str | None = None) -> list[dict]:
    """edit_note единицы -> список правок (по одному на каждый закон каждой пометки).

    Единица может нести несколько пометок (разделитель — перевод строки, см.
    parser._add_edit_note); каждая разбирается отдельно.
    """
    rows: list[dict] = []
    for single in (note or "").split("\n"):
        single = single.strip()
        if not single or single.lower().lstrip("< ").startswith("изменения:"):
            continue  # шапка-перечень изменений всего акта — не правка единицы
        operation = operation_of(single)
        if operation is None:
            continue
        for number, law_date, effective in law_entries(single):
            rows.append({
                "target_unit_id": unit_id,
                "scope": scope_of(single, unit_kind),
                "operation": operation,
                "amending_act_number": number,
                "amending_act_date": law_date or None,
                "effective_date": effective,
                "raw_note": single,
            })
    return rows


def amendments_from_records(records: list[dict]) -> list[dict]:
    """Все единицы -> все правки (порядок документа)."""
    out: list[dict] = []
    for record in records:
        out.extend(amendments_from_note(record["unit_id"], record.get("edit_note"),
                                        record.get("kind")))
    return out


def repeal_dates(amendments: list[dict]) -> dict[str, str | None]:
    """unit_id -> дата утраты силы (ISO или None, если дата в пометке не распознана).

    Используется загрузчиком: у отменённой единицы valid_to = дата утраты силы,
    чтобы get_unit(as_of) не выдавал её как действующую (принцип «цитируй или откажись»).
    """
    out: dict[str, str | None] = {}
    for a in amendments:
        if a["operation"] == "repeal" and a.get("scope", "unit") == "unit":
            uid = a["target_unit_id"]
            if uid not in out or (out[uid] is None and a.get("effective_date")):
                out[uid] = a.get("effective_date")
    return out
