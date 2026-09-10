"""Сравнение плеч поиска на эталоне: лексическое (ts_rank_cd), семантическое, гибрид (RRF).

Запуск: python scripts/eval_search.py  (нужны БД и индекс: python -m taxcorpus embed)
Метрики: unit@5, article@5 по вопросам kind=search эталона.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taxcorpus import load_dotenv  # noqa: E402
from taxcorpus.db import connect  # noqa: E402
from taxcorpus.resolver import article_of_unit_id  # noqa: E402
from taxcorpus.tools import DbCorpus  # noqa: E402

GOLDEN = json.loads((ROOT / "tests" / "golden" / "golden_v0.json").read_text(encoding="utf-8"))


def main() -> int:
    load_dotenv(str(ROOT / ".env"))
    qs = [q for q in GOLDEN["questions"] if q["kind"] == "search"]
    as_of = GOLDEN["as_of"]
    conn = connect()
    corpus = DbCorpus(conn, ROOT / "data" / "processed")
    dense = corpus.hybrid.dense
    variants = {
        "lexical": lambda q: [r["unit_id"] for r in corpus.search_lexical(q, as_of, 5)],
        "dense": lambda q: [uid for uid, _ in dense.search(q, limit=5, allowed={
            u for u, r in corpus._local.units.items() if r.get("is_chunk")})],
        "hybrid": lambda q: [r["unit_id"] for r in corpus.search(q, as_of, 5)],
    }
    if not corpus.hybrid.enabled:
        variants = {"lexical": variants["lexical"]}
        print("[warn] индекс не построен — только лексический вариант", file=sys.stderr)
    for name, fn in variants.items():
        u_hits = a_hits = 0
        misses = []
        for q in qs:
            ids = fn(q["query"])
            exp = set(q["expected"])
            arts = {article_of_unit_id(e) for e in exp}
            u = any(i in exp for i in ids)
            a = any(article_of_unit_id(i) in arts for i in ids)
            u_hits += u
            a_hits += a
            if not u:
                misses.append(q["id"])
        print(f"{name:8s} unit@5 {u_hits}/{len(qs)}  article@5 {a_hits}/{len(qs)}  промахи: {' '.join(misses)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
