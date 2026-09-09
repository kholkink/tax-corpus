"""Резолвер ссылок: структурированная цель (target) -> канонический unit_id.

Стратегия (по плану, слой 2c -> слой 5 resolve_citation):
1. Номера резолвятся сначала точно, затем по вариантам написания в подаче
   банка (сплющенные «2211» = 22.1-1, «51» = пункт 5.1); неоднозначные
   варианты отбрасываются.
2. Глубина — до самой глубокой существующей единицы: article -> point ->
   subpoint -> paragraph. Не найден уровень — откат на родителя (partial).
3. Контекстные ссылки (без статьи: «пункт 5», «абзац второй») резолвятся
   относительно статьи-родителя from_unit_id.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import re

from .parser import ID_DOT


@dataclass
class Resolution:
    unit_id: str | None
    status: str            # resolved | partial | unresolved | external
    depth: str | None      # article | point | subpoint | paragraph | chapter | section
    note: str | None = None


def number_variants(number: str) -> list[str]:
    """Все написания номера: «54.1» -> {«54.1», «541», «54-1»} и наоборот."""
    base = str(number)
    candidates = (base,
                  base.replace(".", ID_DOT),
                  base.replace(".", ""),
                  base.replace("-", ID_DOT),
                  base.replace(".", "").replace("-", ""))
    seen: set[str] = set()
    return [v for v in candidates if v and not (v in seen or seen.add(v))]


class UnitIndex:
    """Индекс корпуса для резолва: строится из плоских записей единиц."""

    def __init__(self, records: list[dict]) -> None:
        self.units: dict[str, dict] = {r["unit_id"]: r for r in records}
        self.parent_of: dict[str, str] = {
            uid: r["parent_unit_id"] for uid, r in self.units.items() if r["parent_unit_id"]
        }
        # варианты номеров для единиц каждого вида; неоднозначные отбрасываются
        self._variants: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set))
        for uid, r in self.units.items():
            if r.get("duplicate_of"):
                continue  # повторный маркер источника («@2») не должен делать номер неоднозначным
            if r["kind"] in ("article", "chapter", "section", "point", "subpoint") and r["number"]:
                for v in number_variants(r["number"]):
                    self._variants[(r["kind"], None if r["kind"] in
                                    ("article", "chapter", "section") else r["parent_unit_id"])][v].add(uid)

    def exists(self, unit_id: str) -> bool:
        return unit_id in self.units

    def article_of(self, unit_id: str) -> str | None:
        cur = unit_id
        while cur:
            rec = self.units.get(cur)
            if rec is None:
                return None
            if rec["kind"] == "article":
                return cur
            cur = self.parent_of.get(cur)
        return None

    def _by_number(self, kind: str, number: str,
                   parent: str | None = None) -> tuple[str, str] | None:
        """(вид, номер) -> (unit_id, способ) | None. Точный номер важнее варианта."""
        if parent is None:
            bucket = self._variants.get((kind, None), {})
        else:
            bucket = self._variants.get((kind, parent), {})
        # точное совпадение номера (а не его варианта) — приоритет
        exact = [uid for uid in bucket.get(str(number), set())
                 if self.units[uid]["number"] == str(number)]
        if len(exact) == 1:
            return exact[0], "exact"
        matched: set[str] = set()
        for v in number_variants(number):
            uids = bucket.get(v, set())
            matched |= uids
        if len(matched) == 1:
            return next(iter(matched)), "variant"
        return None

    def resolve_reference(self, target: dict, from_unit_id: str) -> Resolution:
        if target.get("type") == "act":
            return Resolution(None, "external", None, target.get("law_number"))
        if target.get("type") != "unit":
            return Resolution(None, "unresolved", None, "неизвестный тип цели")

        # --- раздел/глава без статьи ---
        if not target.get("article") and target.get("chapter"):
            hit = self._by_number("chapter", str(target["chapter"]))
            if hit:
                return Resolution(hit[0], "resolved", "chapter")
            return Resolution(None, "unresolved", None,
                              f"глава {target['chapter']} не найдена")
        if not target.get("article") and target.get("section"):
            hit = self._by_number("section", str(target["section"]))
            if hit:
                return Resolution(hit[0], "resolved", "section")
            return Resolution(None, "unresolved", None,
                              f"раздел {target['section']} не найден")

        # --- статья: явная или контекстная ---
        if target.get("article"):
            hit = self._by_number("article", str(target["article"]))
            if hit is None:
                return Resolution(None, "unresolved", None,
                                  f"статья {target['article']} не найдена "
                                  "(возможно, другая часть кодекса)")
            unit_id, depth = hit[0], "article"
        else:
            unit_id = self.article_of(from_unit_id)
            if unit_id is None:
                return Resolution(None, "unresolved", None, "у источника нет статьи-родителя")
            depth = "article"

        # --- пункт -> подпункт -> абзац ---
        if target.get("point"):
            hit = self._by_number("point", str(target["point"]), parent=unit_id)
            if hit is None:
                return Resolution(unit_id, "partial", "article",
                                  f"пункт {target['point']} в {unit_id} не найден")
            unit_id, depth = hit[0], "point"
            if target.get("subpoint"):
                hit = self._by_number("subpoint", str(target["subpoint"]), parent=unit_id)
                if hit is None:
                    return Resolution(unit_id, "partial", "point",
                                      f"подпункт {target['subpoint']} в {unit_id} не найден")
                unit_id, depth = hit[0], "subpoint"
        elif target.get("subpoint"):
            return Resolution(unit_id, "partial", "article",
                              "подпункт без пункта не резолвится однозначно")

        if target.get("paragraph_ordinal"):
            candidate = f"{unit_id}.ab{target['paragraph_ordinal']}"
            if self.exists(candidate):
                return Resolution(candidate, "resolved", "paragraph")
            return Resolution(unit_id, "partial", depth,
                              f"абзац {target['paragraph_ordinal']} в {unit_id} не найден")

        return Resolution(unit_id, "resolved", depth)


def resolve_all(records: list[dict], references: list[dict],
                index_records: list[dict] | None = None) -> list[dict]:
    """Обогащает список ссылок полями to_unit_id/status/resolved_depth.

    index_records — единицы всего корпуса (все акты), если они шире records:
    ссылки ч.1 на «главу 25» и «статью 284» живут в ч.2 и резолвятся только
    по общему индексу. Нумерация статей/глав между частями НК не пересекается.
    """
    index = UnitIndex(index_records if index_records is not None else records)
    out: list[dict] = []
    for ref in references:
        enriched = dict(ref)
        resolution = index.resolve_reference(ref["target"], ref["from_unit_id"])
        enriched["to_unit_id"] = resolution.unit_id
        enriched["status"] = resolution.status
        enriched["resolved_depth"] = resolution.depth
        if resolution.note:
            enriched["resolution_note"] = resolution.note
        out.append(enriched)
    return out


# --- инструмент слоя 5: resolve_citation("п. 3 ст. 164") -> unit_id ---

RE_CITATION = re.compile(
    r"(?<!\w)(?:(?:подп|подпункт)\.?\s*(?P<sub>\d+)\s*)?"
    r"(?:п\.?\s*(?P<point>\d+)\s*)?"
    r"(?:ст|статья|статьи|статьей|статье|статью)\.?\s*"
    r"(?P<article>\d+(?:\.\d+)?(?:-\d+)?)",
    re.IGNORECASE,
)
RE_CITATION_PLAIN_ART = re.compile(
    r"(?<!\w)ст\.?\s*(?P<article>\d+(?:\.\d+)?(?:-\d+)?)", re.IGNORECASE,
)
# контекстная цитата без статьи: «п. 2», «подп. 3 п. 1» — относительно статьи-источника
RE_CITATION_CONTEXT = re.compile(
    r"(?<!\w)(?:(?:подп|подпункт)\.?\s*(?P<sub>\d+)\s*)?"
    r"(?:п|пункт)\.?\s*(?P<point>\d+(?:\.\d+)?)", re.IGNORECASE,
)


def resolve_citation(citation: str, index: UnitIndex,
                     from_unit_id: str | None = None) -> Resolution:
    """Короткая юридическая цитата («п. 2 ст. 88», «подп. 4 п. 1 ст. 218») -> единица.

    from_unit_id — контекст для цитат без статьи («п. 2»): резолв относительно
    статьи-родителя; без контекста такие цитаты остаются unresolved.
    """
    target: dict = {"type": "unit"}
    m = RE_CITATION.search(citation)
    if m and m.group("article"):
        target["article"] = m.group("article")
        if m.group("point"):
            target["point"] = m.group("point")
        if m.group("sub") and m.group("point"):
            target["subpoint"] = m.group("sub")
        return index.resolve_reference(target, from_unit_id or "")
    m = RE_CITATION_PLAIN_ART.search(citation)
    if m:
        target["article"] = m.group("article")
        return index.resolve_reference(target, from_unit_id or "")
    m = RE_CITATION_CONTEXT.search(citation)
    if m and from_unit_id:
        target["point"] = m.group("point")
        if m.group("sub"):
            target["subpoint"] = m.group("sub")
        return index.resolve_reference(target, from_unit_id)
    return Resolution(None, "unresolved", None,
                      "цитата не распознана" if not m else "цитата без статьи требует контекста")


RE_ARTICLE_SEG = re.compile(r"^nk\d(?:\.r[\w-]+)?\.art[\w-]+")


def article_of_unit_id(unit_id: str) -> str:
    """Article-часть unit_id: «nk1.ch14.art88.p2.ab1» -> «nk1.ch14.art88»."""
    m = RE_ARTICLE_SEG.match(unit_id)
    return m.group(0) if m else unit_id
