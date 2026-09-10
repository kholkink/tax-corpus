"""Метрики качества ответов агента (план, §6): по одному ответу и по набору.

  citation_precision — доля ссылок ответа, попавших в ожидаемые единицы (по статье
                       и по единице);
  citation_recall    — доля ожидаемых единиц, на которые ответ сослался;
  hallucination      — ссылки со статусом MISS (нет в корпусе) или STALE (не действует
                       на дату) — должно быть ~0 благодаря проверке;
  temporal_ok        — ни одной STALE-ссылки;
  abstained          — агент честно отказался («нет достаточных оснований»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .resolver import article_of_unit_id

ABSTAIN_MARKERS = ("нет достаточных оснований", "не могу ответить", "недостаточно оснований",
                   "нет документа", "отсутствует в реестре", "не могу подтвердить",
                   "ответить по существу не могу")


@dataclass
class QuestionScore:
    qid: str
    expected: list[str]
    cited: list[str]                 # unit_id из проверенных OK-ссылок (уникальные)
    precision_unit: float | None
    recall_unit: float | None
    precision_article: float | None
    recall_article: float | None
    hallucinations: int
    temporal_ok: bool
    abstained: bool
    expected_abstain: bool
    reworked: bool
    tool_calls: int

    @property
    def abstain_correct(self) -> bool | None:
        return None if not self.expected_abstain and not self.abstained else \
            self.abstained == self.expected_abstain


def _ratio(hits: int, total: int) -> float | None:
    return None if total == 0 else hits / total


def score_answer(qid: str, expected: list[str], answer: str, checks: list[dict],
                 reworked: bool = False, tool_calls: int = 0,
                 expected_abstain: bool = False) -> QuestionScore:
    """checks — VerificationReport.checks в виде словарей (raw, unit_id, status)."""
    # в precision/recall участвуют только нормы; письма/пленумы (depth == document) — отдельный слой
    ok_units = sorted({c["unit_id"] for c in checks
                       if c.get("status") == "ok" and c.get("unit_id") and c.get("depth") != "document"})
    hallucinations = sum(1 for c in checks if c.get("status") in ("unresolved", "not_in_force"))
    temporal_ok = not any(c.get("status") == "not_in_force" for c in checks)
    abstained = any(m in answer.lower() for m in ABSTAIN_MARKERS)

    exp_units = set(expected)
    exp_articles = {article_of_unit_id(u) for u in expected}
    cited_articles = {article_of_unit_id(u) for u in ok_units}

    def covers(cited: str, exp: str) -> bool:
        # ссылка на ожидаемую единицу или на её предка/потомка в пределах статьи
        return cited == exp or cited.startswith(exp + ".") or exp.startswith(cited + ".")

    p_unit_hits = sum(1 for c in ok_units if any(covers(c, e) for e in exp_units))
    r_unit_hits = sum(1 for e in exp_units if any(covers(c, e) for c in ok_units))
    # без ожидаемых норм (ловушки на отказ) precision не определена
    p_unit = _ratio(p_unit_hits, len(ok_units)) if exp_units else None
    p_art = (_ratio(sum(1 for a in cited_articles if a in exp_articles), len(cited_articles))
             if exp_units else None)
    return QuestionScore(
        qid=qid, expected=sorted(exp_units), cited=ok_units,
        precision_unit=p_unit,
        recall_unit=_ratio(r_unit_hits, len(exp_units)),
        precision_article=p_art,
        recall_article=_ratio(sum(1 for a in exp_articles if a in cited_articles), len(exp_articles)),
        hallucinations=hallucinations, temporal_ok=temporal_ok, abstained=abstained,
        expected_abstain=expected_abstain, reworked=reworked, tool_calls=tool_calls,
    )


@dataclass
class EvalSummary:
    scores: list[QuestionScore] = field(default_factory=list)

    def _mean(self, attr: str) -> float | None:
        values = [getattr(s, attr) for s in self.scores if getattr(s, attr) is not None]
        return sum(values) / len(values) if values else None

    def as_dict(self) -> dict:
        n = len(self.scores)
        abst = [s for s in self.scores if s.abstain_correct is not None]
        return {
            "questions": n,
            "citation_precision_unit": self._mean("precision_unit"),
            "citation_recall_unit": self._mean("recall_unit"),
            "citation_precision_article": self._mean("precision_article"),
            "citation_recall_article": self._mean("recall_article"),
            "hallucination_rate": (sum(s.hallucinations for s in self.scores)
                                   / max(1, sum(len(s.cited) + s.hallucinations for s in self.scores))),
            "temporal_correctness": _ratio(sum(1 for s in self.scores if s.temporal_ok), n),
            "abstention_quality": _ratio(sum(1 for s in abst if s.abstain_correct), len(abst)),
            "reworked_share": _ratio(sum(1 for s in self.scores if s.reworked), n),
            "avg_tool_calls": (sum(s.tool_calls for s in self.scores) / n) if n else None,
        }

    def render(self) -> str:
        d = self.as_dict()
        fmt = lambda v: "—" if v is None else f"{v:.2f}"
        lines = ["| метрика | значение |", "|---|---|"]
        for key in ("citation_precision_unit", "citation_recall_unit", "citation_precision_article",
                    "citation_recall_article", "hallucination_rate", "temporal_correctness",
                    "abstention_quality", "reworked_share", "avg_tool_calls"):
            lines.append(f"| {key} | {fmt(d[key])} |")
        lines.append("")
        lines.append("| вопрос | ожидалось | процитировано | P/R (unit) | галлюц. | отказ |")
        lines.append("|---|---|---|---|---|---|")
        for s in self.scores:
            lines.append(f"| {s.qid} | {', '.join(s.expected)} | {', '.join(s.cited) or '—'} | "
                         f"{fmt(s.precision_unit)}/{fmt(s.recall_unit)} | {s.hallucinations} | "
                         f"{'да' if s.abstained else 'нет'} |")
        return "\n".join(lines)


def strip_markdown(text: str) -> str:
    return re.sub(r"[*_`#]+", "", text)
