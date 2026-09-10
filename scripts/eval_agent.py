"""Прогон эталона через агента и подсчёт метрик плана (§6).

Запуск: python scripts/eval_agent.py [--local] [--as-of 2026-09-10] [--limit N] [--model …]
Нужны: pip install -e ".[agent]", ключ ANTHROPIC_API_KEY (или профиль `ant auth login`),
для основного режима — PostgreSQL с загруженным корпусом.
Выход: reports/eval_agent.json (полные ответы и журналы) и reports/eval_agent.md (метрики).
Каждый запрос к модели стоит денег — ограничивайте --limit при отладке.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taxcorpus.agent import TaxAgent  # noqa: E402
from taxcorpus.evaluation import EvalSummary, score_answer  # noqa: E402
from taxcorpus.tools import DbCorpus, LocalCorpus  # noqa: E402

GOLDEN = json.loads((ROOT / "tests" / "golden" / "golden_v0.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--as-of", default=GOLDEN.get("as_of") or date.today().isoformat())
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--db-url", default=None)
    args = ap.parse_args()

    import anthropic
    client = anthropic.Anthropic()
    conn = None
    if args.local:
        corpus = LocalCorpus(ROOT / "data" / "processed")
    else:
        from taxcorpus.db import connect
        conn = connect(args.db_url)
        corpus = DbCorpus(conn, ROOT / "data" / "processed")
    agent = TaxAgent(client, corpus, model=args.model, effort=args.effort)

    questions = [q for q in GOLDEN["questions"] if q["kind"] == "search"]
    if args.limit:
        questions = questions[: args.limit]

    summary = EvalSummary()
    runs = []
    try:
        for q in questions:
            print(f"=== {q['id']} {q['question']}", flush=True)
            result = agent.ask(q["question"], args.as_of)
            checks = [c.__dict__ for c in result.verification.checks]
            score = score_answer(q["id"], q["expected"], result.answer, checks,
                                 reworked=result.reworked, tool_calls=len(result.tool_calls))
            summary.scores.append(score)
            runs.append({"question": q, "result": result.to_dict(), "score": score.__dict__})
            print(f"    P/R unit {score.precision_unit}/{score.recall_unit}, "
                  f"галлюцинаций {score.hallucinations}, вызовов {score.tool_calls}", flush=True)
    finally:
        if conn is not None:
            conn.close()

    out_json = ROOT / "reports" / "eval_agent.json"
    out_md = ROOT / "reports" / "eval_agent.md"
    out_json.write_text(json.dumps({"as_of": args.as_of, "model": args.model,
                                    "summary": summary.as_dict(), "runs": runs},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(f"# Оценка агента на эталоне ({args.model}, as_of {args.as_of})\n\n"
                      + summary.render() + "\n", encoding="utf-8")
    print(summary.render())
    print(f"\nзаписано: {out_json}, {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
