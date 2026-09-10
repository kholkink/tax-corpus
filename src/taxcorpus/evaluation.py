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
    topic: str | None = None
    rep: int = 0                     # номер повтора прогона (разброс модели)

    @property
    def abstain_correct(self) -> bool | None:
        return None if not self.expected_abstain and not self.abstained else \
            self.abstained == self.expected_abstain


def _ratio(hits: int, total: int) -> float | None:
    return None if total == 0 else hits / total


def score_answer(qid: str, expected: list[str], answer: str, checks: list[dict],
                 reworked: bool = False, tool_calls: int = 0,
                 expected_abstain: bool = False, topic: str | None = None, rep: int = 0) -> QuestionScore:
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
        expected_abstain=expected_abstain, reworked=reworked, tool_calls=tool_calls, topic=topic, rep=rep,
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

    def by_topic(self) -> dict[str, dict]:
        """Метрики по темам эталона (тег topic вопроса); без тега — «без темы»."""
        groups: dict[str, list[QuestionScore]] = {}
        for sc in self.scores:
            groups.setdefault(sc.topic or "без темы", []).append(sc)
        return {topic: EvalSummary(scores).as_dict() for topic, scores in sorted(groups.items())}

    def by_rep(self) -> dict[int, dict]:
        """Метрики по повторам прогона — оценка разброса модели на эталоне."""
        groups: dict[int, list[QuestionScore]] = {}
        for sc in self.scores:
            groups.setdefault(sc.rep, []).append(sc)
        return {rep: EvalSummary(scores).as_dict() for rep, scores in sorted(groups.items())}

    def spread(self) -> dict[str, tuple[float, float] | None]:
        """min/max ключевых метрик по повторам (None, если повтор один)."""
        reps = self.by_rep()
        if len(reps) < 2:
            return {}
        out = {}
        for key in ("citation_precision_unit", "citation_recall_unit", "hallucination_rate", "abstention_quality"):
            vals = [r[key] for r in reps.values() if r[key] is not None]
            out[key] = (min(vals), max(vals)) if vals else None
        return out

    def render(self, topics: bool = True) -> str:
        d = self.as_dict()
        fmt = lambda v: "—" if v is None else f"{v:.2f}"
        lines = ["| метрика | значение |", "|---|---|"]
        spread = self.spread()
        for key in ("citation_precision_unit", "citation_recall_unit", "citation_precision_article",
                    "citation_recall_article", "hallucination_rate", "temporal_correctness",
                    "abstention_quality", "reworked_share", "avg_tool_calls"):
            rng = spread.get(key)
            lines.append(f"| {key} | {fmt(d[key])}" + (f" ({fmt(rng[0])}–{fmt(rng[1])} по повторам)" if rng else "") + " |")
        lines.append("")
        if topics and any(sc.topic for sc in self.scores):
            lines.append("| тема | вопросов | P unit | R unit | галлюц. | temporal | отказы |")
            lines.append("|---|---|---|---|---|---|---|")
            for topic, m in self.by_topic().items():
                lines.append(f"| {topic} | {m['questions']} | {fmt(m['citation_precision_unit'])} | "
                             f"{fmt(m['citation_recall_unit'])} | {fmt(m['hallucination_rate'])} | "
                             f"{fmt(m['temporal_correctness'])} | {fmt(m['abstention_quality'])} |")
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


def accuracy_report(agent: dict | None, search: dict | None, golden: dict, snapshot: int | None = None,
                    generated_at: str | None = None) -> tuple[dict, str]:
    """Публичная карта точности (F11): сводка агента по темам + качество поиска -> (json, markdown).

    agent  — содержимое reports/eval_agent.json (summary, runs со score/topic);
    search — содержимое reports/eval_search.json (variants -> per-question hits, by_topic).
    """
    from datetime import datetime, timezone
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    questions = golden.get("questions", [])
    topics_total: dict[str, int] = {}
    for q in questions:
        topics_total[q.get("topic", "без темы")] = topics_total.get(q.get("topic", "без темы"), 0) + 1
    out: dict = {"generated_at": generated_at, "golden_version": golden.get("version", "v0"),
                 "golden_questions": len(questions), "golden_topics": topics_total, "corpus_snapshot": snapshot,
                 "agent": None, "search": None}
    fmt = lambda v: "—" if v is None else f"{v:.2f}"
    md = [f"# Карта точности tax-corpus", "",
          f"Эталон {out['golden_version']}: {len(questions)} вопросов, {len(topics_total)} тем. "
          f"Снимок корпуса: {snapshot if snapshot is not None else '—'}. Обновлено {generated_at}.", "",
          "Метрики считаются детерминированно по проверенным цитатам ответа (OK / STALE / MISS): "
          "precision — доля ссылок ответа среди ожидаемых норм, recall — доля ожидаемых норм в ответе, "
          "hallucination — доля ссылок на несуществующие или недействующие на дату нормы, "
          "temporal — ответы без ссылок на недействующие нормы, abstention — честные отказы на ловушках.", ""]
    if agent:
        summary = EvalSummary([QuestionScore(**{k: v for k, v in r["score"].items()
                                              if k in QuestionScore.__dataclass_fields__})
                               for r in agent.get("runs", [])])
        out["agent"] = {"model": agent.get("model"), "as_of": agent.get("as_of"), "summary": summary.as_dict(),
                        "by_topic": summary.by_topic(), "spread": {k: list(v) if v else None for k, v in summary.spread().items()},
                        "reps": len(summary.by_rep())}
        md += [f"## Агент ({agent.get('model')}, нормы на {agent.get('as_of')}, "
               f"вопросов {summary.as_dict()['questions']}, повторов {len(summary.by_rep())})", "",
               summary.render(topics=True).split("\n\n| вопрос")[0].rstrip("\n"), ""]
    else:
        md += ["## Агент", "", "прогон эталона ещё не выполнялся (scripts/eval_agent.py)", ""]
    if search:
        out["search"] = search
        md += ["## Поиск (unit@5 / article@5 по вопросам kind=search)", "",
               "| вариант | unit@5 | article@5 |", "|---|---|---|"]
        for name, v in search.get("variants", {}).items():
            md.append(f"| {name} | {v['unit_hits']}/{v['questions']} | {v['article_hits']}/{v['questions']} |")
        if search.get("by_topic"):
            md += ["", "| тема | вопросов | " + " | ".join(f"{n} unit@5" for n in search["variants"]) + " |",
                   "|---|---|" + "---|" * len(search["variants"])]
            for topic, row in search["by_topic"].items():
                md.append(f"| {topic} | {row['questions']} | " + " | ".join(str(row.get(n, 0)) for n in search["variants"]) + " |")
        md.append("")
    else:
        md += ["## Поиск", "", "оценка поиска ещё не выполнялась (scripts/eval_search.py)", ""]
    md += ["## Как читать", "",
           "- Эталон открыт: tests/golden/golden_v0.json — каждый ожидаемый ID сверен с текстом корпуса по якорной фразе.",
           "- Ловушки (kind agent_abstain) проверяют отказ, а не цитаты; для них precision не определена.",
           "- Разброс по повторам показан диапазоном, если прогонов больше одного (--repeat).", ""]
    return out, "\n".join(md)
