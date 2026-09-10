"""Мониторинг дел (F2): события -> подписки -> уведомления -> обновление файла агентом (на живой БД)."""

import json
import os

import pytest

from taxcorpus import load_dotenv

load_dotenv()
psycopg = pytest.importorskip("psycopg")
DB_URL = os.environ.get("TAXCORPUS_DB", "postgresql://postgres@127.0.0.1:5432/taxcorpus")


def _db_available() -> bool:
    try:
        with psycopg.connect(DB_URL, connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="локальный Postgres недоступен")


def _conn():
    return psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row, connect_timeout=3, autocommit=True)


def test_event_matches_subscription_and_agent_applies(tmp_path):
    from taxcorpus.agent import TaxAgent
    from taxcorpus.db import record_events
    from taxcorpus.monitor import apply_notification, match_events, notifications, render_change, set_status, what_changed
    from taxcorpus.tools import DbCorpus
    from taxcorpus.workspace import Workspace
    from taxcorpus.workspace_store import sync_workspace
    from tests.test_agent import FakeClient, _block, _response

    ws = Workspace.create("mon-test", "Мониторинг", as_of="2026-09-10", root=tmp_path)
    ws.write_file("research/позиция.md", "# Позиция\n\nСрок камеральной проверки — три месяца (п. 2 ст. 88 НК РФ).",
                  {"sources": ["nk1.ch14.art88.p2"]})
    with _conn() as conn:
        try:
            info = sync_workspace(conn, ws)
            match_events(conn)                                         # чекпоинт — до нашего события
            record_events(conn, [{"kind": "unit_text_changed", "act_code": "nk1", "unit_id": "nk1.ch14.art88.p2",
                                  "payload": {"old_hash": "sha256:old", "new_hash": "sha256:new", "edition_from": "2026-09-01"}},
                                 {"kind": "document_added", "doc_id": "test-doc", "payload": {"cites": ["nk1.ch14.art88"], "agency": "ФНС", "number": "1", "date": "2026-09-05"}},
                                 {"kind": "unit_text_changed", "act_code": "nk2", "unit_id": "nk2.ch21.art164.p3", "payload": {}}])
            stats = match_events(conn)
            assert stats["events"] == 3 and stats["notifications"] >= 2
            ns = notifications(conn, "mon-test", "open")
            kinds = {n["kind"]: n for n in ns}
            assert set(kinds) == {"unit_text_changed", "document_added"}                # ст. 164 дело не задевает
            assert kinds["unit_text_changed"]["paths"] == ["research/позиция.md"]
            assert kinds["document_added"]["matched_by"] == ["nk1.ch14.art88.p2"]        # статья задевает пункт
            assert match_events(conn)["notifications"] == 0                            # повторно не создаются
            change = what_changed(conn, "nk1.ch14.art88.p2", "2026-01-01", "2026-09-10")
            assert change["in_force"] and "Что изменилось" in render_change(change)
            n_id = kinds["unit_text_changed"]["notification_id"]
            assert set_status(conn, n_id, "seen")["status"] == "seen"
            responses = [
                _response([_block(type="tool_use", id="t1", name="write_file",
                                  input={"path": "research/позиция.md", "summary": "обновлено по событию",
                                         "content": "# Позиция v2\n\n## Что изменилось для нашего периода\n\nТекст п. 2 ст. 88 НК РФ изменён; вывод прежний."})], "tool_use"),
                _response([_block(type="text", text="Файл обновлён: п. 2 ст. 88 НК РФ.")], "end_turn"),
            ]
            agent = TaxAgent(FakeClient(responses), DbCorpus(conn), fallbacks=False)
            r = apply_notification(conn, ws, agent, n_id)
            assert r["files_written"] == ["research/позиция.md"] and r["kind"] == "answer"
            task = agent.client.requests[0]["messages"][0]["content"]
            assert "Событие мониторинга" in task and "research/позиция.md" in task and "Что изменилось" in task
            row = conn.execute("SELECT status, applied_session FROM notification WHERE notification_id = %s", (n_id,)).fetchone()
            assert row["status"] == "applied" and row["applied_session"] == r["session_id"]
            assert (ws.path / "research" / ".versions" / "позиция.md.v1").exists()
            acts = json.loads((ws.path / "activity.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            assert acts["action"] == "monitor_apply" and acts["actor"] == "system"
        finally:
            conn.execute("DELETE FROM corpus_event WHERE (unit_id = 'nk1.ch14.art88.p2' AND payload->>'old_hash' = 'sha256:old') "
                         "OR doc_id = 'test-doc' OR (unit_id = 'nk2.ch21.art164.p3' AND payload = '{}'::jsonb)")
            conn.execute("DELETE FROM workspace WHERE slug = 'mon-test'")


def test_detect_unit_changes_and_parameter_changes():
    from taxcorpus.db import detect_parameter_changes, detect_unit_changes

    with _conn() as conn:
        cur = conn.execute("SELECT t.unit_id, t.text_hash FROM unit_text t JOIN unit u ON u.unit_id = t.unit_id "
                           "JOIN act a ON a.act_id = u.act_id WHERE a.act_code = 'nk1' AND t.valid_to IS NULL LIMIT 3").fetchall()
        previous = {r["unit_id"]: {"text_hash": r["text_hash"], "valid_from": None} for r in cur}
        previous[cur[0]["unit_id"]] = {"text_hash": "sha256:other", "valid_from": None}
        previous["nk1.ch99.art999"] = {"text_hash": "sha256:gone", "valid_from": None}
        assert detect_unit_changes(conn, "nk1", {}, "2026-09-01") == []                 # первая загрузка
        events = detect_unit_changes(conn, "nk1", previous, "2026-09-01")
        kinds = {(e["kind"], e["unit_id"]) for e in events}
        assert ("unit_text_changed", cur[0]["unit_id"]) in kinds and ("unit_repealed", "nk1.ch99.art999") in kinds
        assert sum(1 for e in events if e["kind"] == "unit_added") > 1000                # остальные единицы «новые»
        p = conn.execute("SELECT name, value::text AS value, valid_from, source_unit_id FROM parameter WHERE valid_to IS NULL LIMIT 1").fetchone()
        assert detect_parameter_changes(conn, {p["name"]: {**p, "value": "-1"}})[0]["payload"]["new"] == p["value"]
        assert detect_parameter_changes(conn, {p["name"]: dict(p)}) == []
