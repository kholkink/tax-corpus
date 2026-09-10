"""Прогон эталона (tests/golden/golden_v0.json) против загруженной БД: поиск,
резолв цитат, действие на дату. Печатает доказательства по каждому вопросу и
итоговые метрики (recall@5 по статье и по единице, точность резолва, as_of).

Ожидаемые ID эталона сверены по тексту корпуса тестом tests/test_golden_corpus.py —
здесь проверяется уже поведение инструментов слоя 5 (search / resolve / get_unit).

Запуск: python scripts/build_golden.py [postgresql://…]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from taxcorpus.db import get_unit, search_units
from taxcorpus.resolver import UnitIndex, article_of_unit_id, resolve_citation

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests" / "golden" / "golden_v0.json").read_text(encoding="utf-8"))
DB = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAXCORPUS_DB", "postgresql://postgres@127.0.0.1:5432/taxcorpus")


def main() -> int:
    conn = psycopg.connect(DB, row_factory=dict_row)
    rows = conn.execute("SELECT unit_id, kind, number, label, title, parent_unit_id, duplicate_of "
                        "FROM unit").fetchall()
    index = UnitIndex(rows)
    as_of = GOLDEN["as_of"]

    totals = {"search_article@5": [0, 0], "search_unit@5": [0, 0], "resolve": [0, 0], "as_of": [0, 0]}
    for q in GOLDEN["questions"]:
        qid, kind = q["id"], q["kind"]
        print(f"\n=== {qid} [{kind}] {q.get('question') or q.get('citation')}")
        if kind == "search":
            hits = search_units(conn, q["query"], as_of, limit=5)
            expected = set(q["expected"])
            exp_articles = {article_of_unit_id(e) for e in expected}
            by_article = any(article_of_unit_id(h["unit_id"]) in exp_articles for h in hits)
            by_unit = any(h["unit_id"] in expected for h in hits)
            totals["search_article@5"][0] += by_article
            totals["search_unit@5"][0] += by_unit
            totals["search_article@5"][1] += 1
            totals["search_unit@5"][1] += 1
            print(f"  {'OK' if by_unit else ('ART' if by_article else 'MISS')} expected={sorted(expected)}")
            for i, h in enumerate(hits, 1):
                mark = " <<" if h["unit_id"] in expected else (
                    " <" if article_of_unit_id(h["unit_id"]) in exp_articles else "")
                print(f"  {i}. {h['unit_id']} [{h['rank']:.2f}] {h['label']}{mark}")
                print(f"     {h['snippet'][:130]}")
        elif kind == "resolve":
            res = resolve_citation(q["citation"], index)
            ok = res.unit_id in q["expected"]
            totals["resolve"][0] += ok
            totals["resolve"][1] += 1
            print(f"  {'OK' if ok else 'MISS'} -> {res.unit_id} ({res.status}, {res.depth}); "
                  f"expected={q['expected']}")
        elif kind in ("as_of_present", "as_of_absent"):
            rec = get_unit(conn, q["unit_id"], q["as_of"])
            present = rec is not None
            want = kind == "as_of_present"
            totals["as_of"][0] += present == want
            totals["as_of"][1] += 1
            print(f"  {'OK' if present == want else 'MISS'}: на {q['as_of']} единица "
                  f"{'есть' if present else 'отсутствует'} (ожидалось: {'есть' if want else 'отсутствует'})")

    print("\n=== метрики ===")
    for name, (ok, n) in totals.items():
        if n:
            print(f"  {name:18s} {ok}/{n} = {ok / n:.2f}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
