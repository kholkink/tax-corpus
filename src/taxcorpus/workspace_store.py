"""Зеркало метаданных дела в PostgreSQL (P1 плана ПО).

Источник истины — файлы дела (workspace.py, case_session.py); БД хранит манифест, список
файлов с хешами и источниками из шапок файлов агента, сессии и вопросы. Это нужно
мониторингу (подписки по sources), коллаборации и карте точности. Синхронизация
идемпотентна: sync_workspace() можно звать после каждого хода агента.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Json

from .case_session import CaseSession
from .workspace import AGENT_DIRS, Workspace

RE_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def file_header(path: Path) -> dict:
    """Шапка провенанса файла агента (JSON-строки «ключ: значение» между ---)."""
    if path.suffix.lower() != ".md":
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    m = RE_FRONT.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        try:
            out[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            out[key.strip()] = value.strip()
    return out


def upsert_workspace(conn, ws: Workspace) -> int:
    m = ws.manifest
    row = conn.execute(
        """
        INSERT INTO workspace (slug, title, client, as_of, jurisdiction, corpus_snapshot, created_at, synced_at,
                               confidentiality, provider)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
        ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title, client = EXCLUDED.client,
            as_of = EXCLUDED.as_of, jurisdiction = EXCLUDED.jurisdiction,
            corpus_snapshot = EXCLUDED.corpus_snapshot, synced_at = now(),
            confidentiality = EXCLUDED.confidentiality, provider = EXCLUDED.provider
        RETURNING workspace_id
        """,
        (m.slug, m.title, m.client or None, m.as_of, m.jurisdiction, m.corpus_snapshot, m.created_at,
         m.confidentiality, m.provider),
    ).fetchone()
    return row["workspace_id"]


def sync_files(conn, ws: Workspace, workspace_id: int) -> int:
    seen = []
    with conn.transaction():
        for f in ws.list_files():
            p = ws.path / f["path"]
            data = p.read_bytes()
            header = file_header(p) if f["path"].split("/")[0] in AGENT_DIRS else {}
            conn.execute(
                """
                INSERT INTO workspace_file (workspace_id, path, owner, sha256, size, version, sources,
                                            verification, modified_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, path) DO UPDATE SET owner = EXCLUDED.owner,
                    sha256 = EXCLUDED.sha256, size = EXCLUDED.size, version = EXCLUDED.version,
                    sources = EXCLUDED.sources, verification = EXCLUDED.verification,
                    modified_at = EXCLUDED.modified_at
                """,
                (workspace_id, f["path"], f["owner"], "sha256:" + hashlib.sha256(data).hexdigest(),
                 f["size"], header.get("version"), Json(header.get("sources") or []),
                 Json(header.get("verification")) if header.get("verification") is not None else None,
                 f["modified"]),
            )
            seen.append(f["path"])
        conn.execute("DELETE FROM workspace_file WHERE workspace_id = %s AND NOT (path = ANY(%s))",
                     (workspace_id, seen))
    return len(seen)


def upsert_session(conn, workspace_id: int, session: CaseSession) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO session (session_id, workspace_id, status, model, started_at, updated_at,
                                 tool_calls, files_written)
            VALUES (%s, %s, %s, %s, %s, now(), %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET status = EXCLUDED.status, model = EXCLUDED.model,
                updated_at = now(), tool_calls = EXCLUDED.tool_calls, files_written = EXCLUDED.files_written
            """,
            (session.session_id, workspace_id, session.status, session.agent.model,
             session.started_at, len(session.tool_log), Json(sorted(set(session.files_written)))),
        )
        for q in session.questions:
            conn.execute(
                """
                INSERT INTO session_question (session_id, tool_use_id, question, options, asked_at,
                                              answer, answered_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, tool_use_id) DO UPDATE SET answer = EXCLUDED.answer,
                    answered_at = EXCLUDED.answered_at
                """,
                (session.session_id, q["tool_use_id"], q["question"], Json(q.get("options") or []),
                 q.get("asked_at"), q.get("answer"), q.get("answered_at")),
            )


def sync_workspace(conn, ws: Workspace, agent=None) -> dict:
    """Полная синхронизация дела: манифест, файлы, все сессии (по файлам sessions/)."""
    workspace_id = upsert_workspace(conn, ws)
    files = sync_files(conn, ws, workspace_id)
    sessions = 0
    for p in sorted((ws.path / "sessions").glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO session (session_id, workspace_id, status, model, started_at, updated_at,
                                     tool_calls, files_written)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET status = EXCLUDED.status, model = EXCLUDED.model,
                    updated_at = EXCLUDED.updated_at, tool_calls = EXCLUDED.tool_calls,
                    files_written = EXCLUDED.files_written
                """,
                (data["session_id"], workspace_id, data["status"], data.get("model"),
                 data.get("started_at"), data.get("updated_at") or datetime.now(timezone.utc),
                 len(data.get("tool_log", [])), Json(sorted(set(data.get("files_written", []))))),
            )
            for q in data.get("questions", []):
                conn.execute(
                    """
                    INSERT INTO session_question (session_id, tool_use_id, question, options, asked_at,
                                                  answer, answered_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, tool_use_id) DO UPDATE SET answer = EXCLUDED.answer,
                        answered_at = EXCLUDED.answered_at
                    """,
                    (data["session_id"], q["tool_use_id"], q["question"], Json(q.get("options") or []),
                     q.get("asked_at"), q.get("answer"), q.get("answered_at")),
                )
        sessions += 1
    return {"workspace_id": workspace_id, "files": files, "sessions": sessions}


def subscriptions_for(conn, workspace_id: int) -> list[str]:
    """Единицы и документы, на которые опираются файлы агента дела (основа мониторинга, F2)."""
    rows = conn.execute(
        "SELECT DISTINCT jsonb_array_elements_text(sources) AS src FROM workspace_file "
        "WHERE workspace_id = %s ORDER BY 1", (workspace_id,)).fetchall()
    return [r["src"] for r in rows]
