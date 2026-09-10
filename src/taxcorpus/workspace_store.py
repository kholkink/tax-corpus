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


def sync_facts(conn, ws: Workspace, workspace_id: int) -> int:
    """Зеркало facts.json (F6)."""
    from .facts import FactStore
    facts = FactStore(ws).facts
    with conn.transaction():
        for f in facts:
            conn.execute(
                """
                INSERT INTO fact (workspace_id, fact_id, kind, value, text, role, source_path, page, quote,
                                  extracted_by, confirmed, created_at, confirmed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, fact_id) DO UPDATE SET kind = EXCLUDED.kind, value = EXCLUDED.value,
                    text = EXCLUDED.text, role = EXCLUDED.role, source_path = EXCLUDED.source_path,
                    page = EXCLUDED.page, quote = EXCLUDED.quote, extracted_by = EXCLUDED.extracted_by,
                    confirmed = EXCLUDED.confirmed, confirmed_at = EXCLUDED.confirmed_at
                """,
                (workspace_id, f.fact_id, f.kind, str(f.value), f.text, f.role, f.source_path, f.page, f.quote,
                 f.extracted_by, f.confirmed, f.created_at, f.confirmed_at),
            )
        conn.execute("DELETE FROM fact WHERE workspace_id = %s AND NOT (fact_id = ANY(%s))",
                     (workspace_id, [f.fact_id for f in facts]))
    return len(facts)


def sync_collab(conn, ws: Workspace, workspace_id: int) -> dict:
    """Зеркало comments.json и activity.jsonl (F8)."""
    from .collab import CollabStore
    store = CollabStore(ws)
    with conn.transaction():
        for c in store.comments:
            conn.execute(
                """
                INSERT INTO comment (comment_id, workspace_id, path, version, anchor, author, text, parent_id,
                                     created_at, resolved, resolved_by, resolved_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (comment_id) DO UPDATE SET resolved = EXCLUDED.resolved,
                    resolved_by = EXCLUDED.resolved_by, resolved_at = EXCLUDED.resolved_at, text = EXCLUDED.text
                """,
                (c["comment_id"], workspace_id, c["path"], c.get("version"), c.get("anchor"), c["author"], c["text"],
                 c.get("parent_id"), c["created_at"], c["resolved"], c.get("resolved_by"), c.get("resolved_at")))
        entries = store.activity(limit=100000)
        for e in entries:
            conn.execute(
                "INSERT INTO activity (workspace_id, at, actor, action, target, details) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (workspace_id, e["at"], e["actor"], e["action"], e.get("target"), Json(e.get("details") or {})))
    return {"comments": len(store.comments), "activity": len(entries)}


def sync_workspace(conn, ws: Workspace, agent=None) -> dict:
    """Полная синхронизация дела: манифест, файлы, факты, комментарии и журнал, все сессии."""
    workspace_id = upsert_workspace(conn, ws)
    files = sync_files(conn, ws, workspace_id)
    facts = sync_facts(conn, ws, workspace_id)
    collab = sync_collab(conn, ws, workspace_id)
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
    return {"workspace_id": workspace_id, "files": files, "facts": facts, "sessions": sessions, **collab}


def subscriptions_for(conn, workspace_id: int) -> list[str]:
    """Единицы и документы, на которые опираются файлы агента дела (основа мониторинга, F2)."""
    rows = conn.execute(
        "SELECT DISTINCT jsonb_array_elements_text(sources) AS src FROM workspace_file "
        "WHERE workspace_id = %s ORDER BY 1", (workspace_id,)).fetchall()
    return [r["src"] for r in rows]


def sync_users(conn, store) -> dict:
    """Зеркало config/users.json (P5): app_user, api_token (только sha256), workspace_member."""
    d = store.data
    with conn.transaction():
        for u in d["users"]:
            conn.execute(
                "INSERT INTO app_user (user_id, email, name, admin, created_at) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, name = EXCLUDED.name, admin = EXCLUDED.admin",
                (u["user_id"], u["email"], u["name"], bool(u.get("admin")), u.get("created_at")))
        for t in d["tokens"]:
            conn.execute(
                "INSERT INTO api_token (token_id, user_id, sha256, label, created_at, last_used_at, revoked) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (token_id) DO UPDATE SET "
                "last_used_at = EXCLUDED.last_used_at, revoked = EXCLUDED.revoked",
                (t["token_id"], t["user_id"], t["sha256"], t.get("label"), t.get("created_at"), t.get("last_used_at"), bool(t.get("revoked"))))
        conn.execute("DELETE FROM workspace_member")
        for m in d["members"]:
            conn.execute("INSERT INTO workspace_member (slug, user_id, role, granted_at) VALUES (%s, %s, %s, %s)",
                         (m["slug"], m["user_id"], m["role"], m.get("granted_at")))
    return {"users": len(d["users"]), "tokens": len(d["tokens"]), "members": len(d["members"])}
