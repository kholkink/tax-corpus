"""Термины с определениями (Term, слой 3 плана: связь defines_term).

Точка-словарь — пункт/статья, чей первый абзац объявляет понятия
(«Для целей настоящего Кодекса … используются следующие понятия:»), а дальше
идут строки «термин - определение» — собственными абзацами (ст. 11 п. 2) либо
подпунктами (ст. 11.1, 105.16-1, 346.43, 412, 413). Область действия берётся
из формулировки: настоящего Кодекса / настоящей главы / настоящей статьи /
пункта N настоящей статьи.
"""

from __future__ import annotations

import re

RE_DICT_HEAD = re.compile(r"(понят|термин)[^:]*:\s*$", re.IGNORECASE)
RE_TERM_SPLIT = re.compile(r"\s[-–—]\s")


def split_definition(text: str) -> tuple[str, str] | None:
    """Абзац -> (термин, определение) по первому тире ВНЕ скобок; иначе None."""
    for m in RE_TERM_SPLIT.finditer(text):
        depth = text[:m.start()].count("(") - text[:m.start()].count(")")
        if depth == 0:
            term = text[:m.start()].strip().strip('"«»').strip()
            definition = text[m.end():].strip().rstrip(";.").strip()
            if 1 < len(term) <= 120 and definition:
                return term, definition
            return None
    return None


def dictionary_scope(head: str) -> str:
    """Формулировка заголовка словаря -> code | chapter | article | point."""
    low = head.lower()
    if "настоящего кодекса" in low:
        return "code"
    if "настоящей главы" in low:
        return "chapter"
    if re.search(r"пункт\w*\s+\d", low) and "настоящей статьи" in low:
        return "point"
    if "настоящей статьи" in low:
        return "article"
    return "code"


def _scope_unit(unit_id: str, scope: str) -> str | None:
    """Единица, в пределах которой действует определение."""
    m_chapter = re.match(r"(nk\d\.ch[\w-]+)", unit_id)
    m_article = re.match(r"(nk\d\.(?:ch|r|sub)?[\w-]*\.art[\w-]+)", unit_id)
    if scope == "chapter":
        return m_chapter.group(1) if m_chapter else None
    if scope in ("article", "point"):
        return m_article.group(1) if m_article else None
    return None


def extract_terms(records: list[dict]) -> list[dict]:
    """Единицы -> [{term, term_norm, definition, definition_unit_id, scope, scope_unit_id}]."""
    by_id = {r["unit_id"]: r for r in records}
    children: dict[str, list[dict]] = {}
    for r in records:
        if r.get("parent_unit_id"):
            children.setdefault(r["parent_unit_id"], []).append(r)

    rows: list[dict] = []
    for r in records:
        if r.get("kind") not in ("article", "point") or not r.get("paragraphs"):
            continue
        head = r["paragraphs"][0]
        if not RE_DICT_HEAD.search(head):
            continue
        scope = dictionary_scope(head)
        # строки словаря: собственные абзацы после заголовка, затем подпункты
        entries = [c for c in children.get(r["unit_id"], [])
                   if c["kind"] == "paragraph" and c["number"] != "1"]
        entries += [c for c in children.get(r["unit_id"], []) if c["kind"] == "subpoint"]
        for entry in entries:
            parsed = split_definition(entry["text"])
            if parsed is None:
                continue
            term, definition = parsed
            rows.append({
                "term": term,
                "term_norm": term.lower().replace("ё", "е"),
                "definition": definition,
                "definition_unit_id": entry["unit_id"],
                "scope": scope,
                "scope_unit_id": _scope_unit(entry["unit_id"], scope),
            })
    return rows
