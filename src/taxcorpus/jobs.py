"""Задачи и расписание (P3 плана ПО): единый запуск, журнал job_run, события корпуса.

    python -m taxcorpus jobs list
    python -m taxcorpus jobs run crawl_fns -- --pages 114
    python -m taxcorpus jobs run daily
    python -m taxcorpus jobs history [--name …]
    python -m taxcorpus jobs cron            # строки для crontab

Каждый запуск пишет строку в job_run (статус, статистика, ошибка). Задачи, замечающие
изменения источников, кладут события в corpus_event — их читает мониторинг дел (F2).
Краулеры переиспользуют скрипты scripts/fetch_*.py (у них main(argv)).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from psycopg.types.json import Json

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
# UUID актов в банке ГАС — из каталога «Кодексы РФ» (data/raw/minjust_res_codes_v2.json):
# nk1 = ФЗ от 31.07.1998 № 146-ФЗ, nk2 = ФЗ от 05.08.2000 № 117-ФЗ (сверять по названию, не по памяти)
ACTS = {"nk1": "F7DE1846-3C6A-47AB-B440-B8E4CEA90C68", "nk2": "B5C1D49E-FAAD-4027-8721-C4ED5CA2F0A3"}


@dataclass
class JobContext:
    conn: object | None
    run_id: int | None
    argv: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def say(self, message: str) -> None:
        self.log.append(message)
        print(message, flush=True)

    def event(self, kind: str, **payload) -> None:
        """Событие корпуса (F2): act_code/unit_id/doc_id — из payload, остальное в JSON."""
        if self.conn is None:
            return
        self.conn.execute(
            "INSERT INTO corpus_event (kind, act_code, unit_id, doc_id, payload, run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (kind, payload.get("act_code"), payload.get("unit_id"), payload.get("doc_id"),
             Json({k: v for k, v in payload.items() if k not in ("act_code", "unit_id", "doc_id")}),
             self.run_id),
        )


JOBS: dict[str, tuple[str, Callable[[JobContext], None]]] = {}


def job(name: str, description: str):
    def wrap(fn):
        JOBS[name] = (description, fn)
        return fn
    return wrap


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _count_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) if path.exists() else 0


# --- краулеры ------------------------------------------------------------------------

@job("crawl_fns", "письма ФНС, обязательные для налоговых органов (nalog.gov.ru), инкрементально")
def crawl_fns(ctx: JobContext) -> None:
    path = DATA / "interpretations" / "fns_mandatory.jsonl"
    before = _count_lines(path)
    _script("fetch_fns_letters").main(ctx.argv or ["--pages", "6", "--delay", "2.5"])
    ctx.stats.update(before=before, after=_count_lines(path))


@job("crawl_minfin", "письма Минфина (minfin.gov.ru), инкрементально")
def crawl_minfin(ctx: JobContext) -> None:
    path = DATA / "interpretations" / "minfin_letters.jsonl"
    before = _count_lines(path)
    _script("fetch_minfin_letters").main(ctx.argv or ["--pages", "1", "--delay", "3"])
    ctx.stats.update(before=before, after=_count_lines(path))


@job("crawl_courts", "постановления Пленума и обзоры по списку первоисточников")
def crawl_courts(ctx: JobContext) -> None:
    path = DATA / "interpretations" / "court_acts.jsonl"
    before = _count_lines(path)
    _script("fetch_court_acts").main(ctx.argv or [])
    ctx.stats.update(before=before, after=_count_lines(path))


# --- загрузка в БД и события -----------------------------------------------------------

@job("load_docs", "реестр разъяснений -> БД; события document_added / document_status_changed")
def load_docs(ctx: JobContext) -> None:
    if ctx.conn is None:
        raise RuntimeError("нужна БД (TAXCORPUS_DB)")
    from .db import load_documents_db
    from .interpretations import link_document, load_documents
    from .resolver import UnitIndex

    before = {r["doc_id"]: r["status"] for r in
              ctx.conn.execute("SELECT doc_id, status FROM document").fetchall()}
    docs = load_documents(DATA / "interpretations")
    records = []
    for p in sorted((DATA / "processed").glob("*_units.jsonl")):
        with p.open(encoding="utf-8") as fh:
            records.extend(json.loads(line) for line in fh if line.strip())
    index = UnitIndex(records)
    edges = [e for d in docs for e in link_document(d, index)]
    load_documents_db(ctx.conn, docs, edges)
    added = changed = 0
    for d in docs:
        cites = sorted({e["to_unit_id"] for e in edges if e["doc_id"] == d.doc_id})
        if d.doc_id not in before:
            added += 1
            ctx.event("document_added", doc_id=d.doc_id, agency=d.agency, number=d.number,
                      date=d.date, status=d.status, cites=cites[:50])
        elif before[d.doc_id] != d.status:
            changed += 1
            ctx.event("document_status_changed", doc_id=d.doc_id, old=before[d.doc_id],
                      new=d.status, cites=cites[:50])
    ctx.stats.update(documents=len(docs), edges=len(edges), added=added, status_changed=changed)
    ctx.say(f"документов {len(docs)}, рёбер {len(edges)}, новых {added}, сменили статус {changed}")


def parse_edition_label(label: str) -> tuple[str | None, str | None]:
    """«Редакция №376 от 04.08.2026» / «ред. 199 от 04.08.2026» -> ("376", "2026-08-04")."""
    m = re.search(r"(?:№|N|ред\.?)\s*(\d+)\D+(\d{2})\.(\d{2})\.(\d{4})", label or "")
    if not m:
        return None, None
    return m.group(1), f"{m.group(4)}-{m.group(3)}-{m.group(2)}"


def loaded_edition(act_code: str) -> tuple[str | None, str | None]:
    meta = DATA / "processed" / f"{act_code}_meta.json"
    if not meta.exists():
        return None, None
    return parse_edition_label(json.loads(meta.read_text(encoding="utf-8")).get("source_url", ""))


@job("check_bank_editions", "новые редакции НК в банке ГАС (без текста): событие edition_available")
def check_bank_editions(ctx: JobContext) -> None:
    import time
    fa = _script("fetch_act")
    opener = fa.make_opener()
    fa.get_session(opener)
    time.sleep(2)
    query = json.loads(json.dumps(fa.CATALOG_QUERY))
    query["request"]["additionalFields"] = ["document_edition", "document_number", "document_date_edition"]
    data = fa.post_json(opener, f"{fa.BASE}/s.action", query)
    docs = (data.get("searchResult") or {}).get("documents") or []
    found = 0
    for act_code, uuid in ACTS.items():
        target = next((d for d in docs if str(d.get("id", "")).upper().startswith(uuid.upper()[:8])), None)
        if target is None:
            ctx.say(f"{act_code}: акт не найден в каталоге")
            continue
        found += 1
        add = fa.normalize_additional(target.get("additionalFields"))
        bank_no, bank_date = parse_edition_label(str(add.get("document_edition") or ""))
        have_no, have_date = loaded_edition(act_code)
        ctx.stats[act_code] = {"bank": [bank_no, bank_date], "loaded": [have_no, have_date]}
        if bank_no and bank_no != have_no:
            ctx.say(f"{act_code}: в банке редакция №{bank_no} от {bank_date}, загружена №{have_no} от {have_date}")
            ctx.event("edition_available", act_code=act_code, bank_edition=bank_no, bank_date=bank_date,
                      loaded_edition=have_no, loaded_date=have_date, uuid=uuid)
        else:
            ctx.say(f"{act_code}: редакция №{have_no} актуальна")
    ctx.stats["acts_found"] = found


@job("embed", "семантический индекс чанков (долго на CPU)")
def embed(ctx: JobContext) -> None:
    from .embeddings import DenseIndex
    records = []
    for p in sorted((DATA / "processed").glob("*_units.jsonl")):
        with p.open(encoding="utf-8") as fh:
            records.extend(json.loads(line) for line in fh if line.strip())
    n = DenseIndex().build(records, show_progress=False)
    ctx.stats["chunks"] = n


@job("snapshot", "снимок корпуса в БД")
def snapshot(ctx: JobContext) -> None:
    if ctx.conn is None:
        raise RuntimeError("нужна БД (TAXCORPUS_DB)")
    from .db import create_snapshot
    snap = create_snapshot(ctx.conn, ctx.argv[0] if ctx.argv else "job snapshot")
    ctx.stats.update(snapshot_id=snap["snapshot_id"], counts=snap["counts"])
    ctx.say(f"снимок #{snap['snapshot_id']}")


@job("eval_search", "качество поиска на эталоне (лексика/dense/гибрид)")
def eval_search(ctx: JobContext) -> None:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _script("eval_search").main()
    ctx.stats["report"] = buf.getvalue().strip().splitlines()
    ctx.say(buf.getvalue().strip())


@job("eval_agent", "ночная оценка агента на эталоне (платные запросы; --limit N)")
def eval_agent(ctx: JobContext) -> None:
    _script("eval_agent").main(ctx.argv or ["--limit", "10"])
    summary_path = ROOT / "reports" / "eval_agent.json"
    if summary_path.exists():
        ctx.stats["summary"] = json.loads(summary_path.read_text(encoding="utf-8")).get("summary")


@job("daily", "ежедневный конвейер: краулеры -> load_docs -> check_bank_editions -> snapshot")
def daily(ctx: JobContext) -> None:
    for name in ("crawl_fns", "crawl_minfin", "crawl_courts", "load_docs", "check_bank_editions", "snapshot"):
        ctx.say(f"=== {name}")
        sub = run_job(name, [], conn=ctx.conn)
        ctx.stats[name] = {"status": sub["status"], **sub["stats"]}
        if sub["status"] != "ok":
            ctx.say(f"{name}: {sub['error']}")


# --- запуск и журнал ----------------------------------------------------------------------

def run_job(name: str, argv: list[str] | None = None, conn=None) -> dict:
    if name not in JOBS:
        raise KeyError(f"нет задачи {name}; есть: {', '.join(sorted(JOBS))}")
    description, fn = JOBS[name]
    own_conn = False
    if conn is None:
        try:
            from .db import connect, ensure_schema
            conn = connect()
            ensure_schema(conn)
            own_conn = True
        except Exception as exc:  # noqa: BLE001 — задачи без БД (краулеры) работают и так
            print(f"[warn] БД недоступна ({type(exc).__name__}), журнал запусков не ведётся", file=sys.stderr)
            conn = None
    run_id = None
    if conn is not None:
        run_id = conn.execute("INSERT INTO job_run (name) VALUES (%s) RETURNING run_id", (name,)).fetchone()["run_id"]
    ctx = JobContext(conn=conn, run_id=run_id, argv=list(argv or []))
    started = datetime.now(timezone.utc)
    status, error = "ok", None
    try:
        fn(ctx)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        ctx.log.append(traceback.format_exc())
    if conn is not None and run_id is not None:
        conn.execute(
            "UPDATE job_run SET finished_at = now(), status = %s, stats = %s, error = %s WHERE run_id = %s",
            (status, Json({**ctx.stats, "log": ctx.log[-50:]}), error, run_id))
    if own_conn and conn is not None:
        conn.close()
    seconds = (datetime.now(timezone.utc) - started).total_seconds()
    return {"name": name, "run_id": run_id, "status": status, "error": error, "stats": ctx.stats,
            "seconds": round(seconds, 1)}


def history(conn, name: str | None = None, limit: int = 20) -> list[dict]:
    return conn.execute(
        "SELECT run_id, name, started_at, finished_at, status, error FROM job_run "
        "WHERE (%s::text IS NULL OR name = %s) ORDER BY started_at DESC LIMIT %s",
        (name, name, limit)).fetchall()


CRONTAB = """# tax-corpus: ежедневный конвейер в 03:00, ночная оценка в 04:00 (см. README)
0 3 * * *  cd {root} && {python} -m taxcorpus jobs run daily >> reports/jobs.log 2>&1
0 4 * * 1  cd {root} && {python} -m taxcorpus jobs run eval_agent -- --limit 30 >> reports/jobs.log 2>&1
"""
