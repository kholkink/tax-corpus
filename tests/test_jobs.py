"""Задачи (P3): реестр, разбор редакций банка, журнал запусков (БД — интеграционно)."""

import os

import pytest

from taxcorpus import jobs as J
from taxcorpus import load_dotenv


def test_registry_and_edition_parsing():
    assert {"crawl_fns", "crawl_minfin", "load_docs", "check_bank_editions", "snapshot", "daily"} <= set(J.JOBS)
    assert J.parse_edition_label("Редакция №376 от 04.08.2026") == ("376", "2026-08-04")
    assert J.parse_edition_label("https://… (ГАС Законодательство России, ред. 199 от 04.08.2026)") == ("199", "2026-08-04")
    assert J.parse_edition_label("") == (None, None)
    assert "jobs run daily" in J.CRONTAB


def test_run_job_without_db_logs_warning(monkeypatch):
    J.JOBS["_noop"] = ("тест", lambda ctx: ctx.stats.update(ok=True))
    monkeypatch.setenv("TAXCORPUS_DB", "postgresql://nobody@127.0.0.1:1/none")
    try:
        result = J.run_job("_noop", [])
        assert result["status"] == "ok" and result["stats"] == {"ok": True} and result["run_id"] is None
    finally:
        del J.JOBS["_noop"]


load_dotenv()
psycopg = pytest.importorskip("psycopg")


def _db_available() -> bool:
    try:
        with psycopg.connect(os.environ.get("TAXCORPUS_DB", ""), connect_timeout=3):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="локальный Postgres недоступен")
def test_run_job_records_history_and_events():
    from taxcorpus.db import connect, ensure_schema

    def good(ctx):
        ctx.stats["n"] = 1
        ctx.event("document_added", doc_id="test-doc", cites=["nk1.ch14.art88.p2"])

    def bad(ctx):
        raise RuntimeError("сломалось")

    J.JOBS["_good"] = ("тест", good)
    J.JOBS["_bad"] = ("тест", bad)
    conn = connect()
    ensure_schema(conn)
    try:
        ok = J.run_job("_good", [], conn=conn)
        failed = J.run_job("_bad", [], conn=conn)
        assert ok["status"] == "ok" and failed["status"] == "failed" and "сломалось" in failed["error"]
        rows = {r["run_id"]: r for r in J.history(conn, limit=5)}
        assert rows[ok["run_id"]]["status"] == "ok" and rows[failed["run_id"]]["status"] == "failed"
        ev = conn.execute("SELECT kind, doc_id, payload FROM corpus_event WHERE run_id = %s", (ok["run_id"],)).fetchone()
        assert ev["kind"] == "document_added" and ev["doc_id"] == "test-doc" and ev["payload"]["cites"] == ["nk1.ch14.art88.p2"]
        conn.execute("DELETE FROM corpus_event WHERE run_id = %s", (ok["run_id"],))
        conn.execute("DELETE FROM job_run WHERE run_id IN (%s, %s)", (ok["run_id"], failed["run_id"]))
    finally:
        conn.close()
        J.JOBS.pop("_good", None)
        J.JOBS.pop("_bad", None)
