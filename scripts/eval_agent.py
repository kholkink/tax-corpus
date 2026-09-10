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


def _score_run(q: dict, res: dict, rep: int = 0):
    return score_answer(q["id"], q["expected"], res["answer"], res["verification"]["checks"],
                        reworked=res["reworked"], tool_calls=len(res["tool_calls"]),
                        expected_abstain=q["kind"] == "agent_abstain", topic=q.get("topic"), rep=rep)


def _write_reports(args, runs: list[dict]) -> None:
    from taxcorpus.evaluation import QuestionScore
    summary = EvalSummary([QuestionScore(**{k: v for k, v in r["score"].items()
                                          if k in QuestionScore.__dataclass_fields__}) for r in runs])
    (ROOT / "reports" / "eval_agent.json").write_text(
        json.dumps({"as_of": args.as_of, "model": args.model, "summary": summary.as_dict(),
                    "by_topic": summary.by_topic(), "runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "reports" / "eval_agent.md").write_text(
        f"# Оценка агента на эталоне ({args.model}, as_of {args.as_of})\n\n" + summary.render() + "\n",
        encoding="utf-8")
    print(summary.render())
    write_accuracy()


def write_accuracy() -> dict:
    """reports/accuracy.{json,md} — публичная карта точности (F11) из уже сохранённых отчётов."""
    from taxcorpus.evaluation import accuracy_report
    reports = ROOT / "reports"
    agent = json.loads((reports / "eval_agent.json").read_text(encoding="utf-8")) if (reports / "eval_agent.json").exists() else None
    search = json.loads((reports / "eval_search.json").read_text(encoding="utf-8")) if (reports / "eval_search.json").exists() else None
    snapshot = None
    try:
        from taxcorpus.db import connect, latest_snapshot
        conn = connect()
        snapshot = latest_snapshot(conn)
        conn.close()
    except Exception:  # noqa: BLE001 — карта строится и без БД
        pass
    data, md = accuracy_report(agent, search, GOLDEN, snapshot)
    (reports / "accuracy.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (reports / "accuracy.md").write_text(md + "\n", encoding="utf-8")
    return data


def rescore(args) -> int:
    """Пересчёт метрик по сохранённым ответам: изменилась метрика или эталон — модель не нужна."""
    import os
    from taxcorpus import load_dotenv
    load_dotenv(str(ROOT / ".env"))
    args.model = args.model or os.environ.get("TAXCORPUS_MODEL") or "claude-opus-5"
    runs_path = ROOT / "reports" / "eval_agent_runs.jsonl"
    by_id = {q["id"]: q for q in GOLDEN["questions"]}
    runs = []
    for line in runs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        q = by_id.get(r["question"]["id"], r["question"])
        score = _score_run(q, r["result"], r.get("rep", 0))
        runs.append({**r, "question": q, "score": score.__dict__})
    _write_reports(args, runs)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--as-of", default=GOLDEN.get("as_of") or date.today().isoformat())
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=None, help="по умолчанию модель профиля провайдера")
    ap.add_argument("--provider", default=None, help="имя профиля из config/providers.json")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--db-url", default=None)
    ap.add_argument("--no-resume", action="store_true", help="не пропускать уже оценённые вопросы")
    ap.add_argument("--rescore", action="store_true",
                    help="только пересчитать метрики по reports/eval_agent_runs.jsonl (без модели)")
    ap.add_argument("--accuracy", action="store_true", help="только собрать reports/accuracy.{json,md}")
    ap.add_argument("--repeat", type=int, default=1, help="повторов на вопрос (разброс модели), по умолчанию 1")
    ap.add_argument("--topic", default=None, help="только вопросы этой темы эталона")
    args = ap.parse_args(argv)
    if args.accuracy:
        print(json.dumps({k: v for k, v in write_accuracy().items() if k != "search"}, ensure_ascii=False)[:600])
        return 0
    if args.rescore:
        return rescore(args)

    from dataclasses import replace
    from taxcorpus import load_dotenv
    from taxcorpus.providers import choose, make_client
    load_dotenv(str(ROOT / ".env"))
    provider = choose("standard", args.provider)          # профиль из config/providers.json или .env
    if args.model:
        provider = replace(provider, model=args.model)
    args.model, fallbacks = provider.model, provider.fallbacks
    client = make_client(provider)  # max_retries=4: обрывы соединения провайдера
    print(f"[провайдер] {provider.badge()}", file=sys.stderr)
    conn = None
    if args.local:
        corpus = LocalCorpus(ROOT / "data" / "processed")
    else:
        from taxcorpus.db import connect
        conn = connect(args.db_url)
        corpus = DbCorpus(conn, ROOT / "data" / "processed")
    agent = TaxAgent(client, corpus, model=args.model, effort=args.effort, fallbacks=fallbacks)

    questions = [q for q in GOLDEN["questions"] if q["kind"] in ("search", "agent", "agent_abstain")]
    if args.topic:
        questions = [q for q in questions if q.get("topic") == args.topic]
    if args.limit:
        questions = questions[: args.limit]

    # инкрементально: каждый ответ дописывается в eval_agent_runs.jsonl, повторный
    # запуск пропускает уже оценённые вопросы (прогон дорогой и долгий)
    runs_path = ROOT / "reports" / "eval_agent_runs.jsonl"
    runs: list[dict] = []
    if runs_path.exists() and not args.no_resume:
        runs = [json.loads(l) for l in runs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        runs = [r for r in runs if r.get("model") == args.model and r.get("as_of") == args.as_of]
    done = {(r["question"]["id"], r.get("rep", 0)) for r in runs}
    summary = EvalSummary()
    try:
        with runs_path.open("a", encoding="utf-8") as fh:
            for rep in range(args.repeat):
                for q in questions:
                    if (q["id"], rep) in done:
                        continue
                    print(f"=== {q['id']}" + (f" (повтор {rep + 1})" if args.repeat > 1 else "") + f" {q['question']}", flush=True)
                    try:
                        result = agent.ask(q["question"], args.as_of)
                    except Exception as exc:  # noqa: BLE001 — один сбой не должен ронять прогон
                        print(f"    [error] {type(exc).__name__}: {str(exc)[:160]}", flush=True)
                        continue
                    res = result.to_dict()
                    res["verification"]["checks"] = [c.__dict__ for c in result.verification.checks]
                    score = _score_run(q, res, rep)
                    run = {"question": q, "result": res, "score": score.__dict__,
                           "model": args.model, "as_of": args.as_of, "rep": rep}
                    runs.append(run)
                    fh.write(json.dumps(run, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"    P/R unit {score.precision_unit}/{score.recall_unit}, "
                          f"галлюцинаций {score.hallucinations}, вызовов {score.tool_calls}", flush=True)
    finally:
        if conn is not None:
            conn.close()

    _write_reports(args, runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
