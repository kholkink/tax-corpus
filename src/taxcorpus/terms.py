"""Термины с определениями (Term, слой 3 плана: связь defines_term).

v0 — только ст. 11 п. 2 НК РФ: каждый абзац вида «термин - определение;».
Отраслевые определения («для целей настоящей главы … понимается») — следующий шаг,
они требуют привязки области действия (глава/статья).
"""

from __future__ import annotations

import re

DEFINITION_POINTS = ("nk1.ch1.art11.p2",)

# «организации - юридические лица, …», «коэффициент-дефлятор - коэффициент, …»,
# «"Инвестиционный проект" - ограниченный …»: разделитель — тире, обособленное пробелами
RE_TERM_SPLIT = re.compile(r"\s[-–—]\s")


def split_definition(text: str) -> tuple[str, str] | None:
    """Абзац -> (термин, определение) по первому тире ВНЕ скобок; иначе None."""
    depth = 0
    for m in RE_TERM_SPLIT.finditer(text):
        depth = text[:m.start()].count("(") - text[:m.start()].count(")")
        if depth == 0:
            term = text[:m.start()].strip().strip('"«»').strip()
            definition = text[m.end():].strip().rstrip(";.").strip()
            if 1 < len(term) <= 120 and definition:
                return term, definition
            return None
    return None


def extract_terms(records: list[dict]) -> list[dict]:
    """Единицы -> [{term, definition, definition_unit_id, scope}] из точек-словарей."""
    rows: list[dict] = []
    for r in records:
        if r.get("kind") != "paragraph" or r.get("parent_unit_id") not in DEFINITION_POINTS:
            continue
        parsed = split_definition(r["text"])
        if parsed is None:
            continue
        term, definition = parsed
        rows.append({
            "term": term,
            "term_norm": term.lower().replace("ё", "е"),
            "definition": definition,
            "definition_unit_id": r["unit_id"],
            "scope": "code",  # ст. 11: для целей всего Кодекса
        })
    return rows
