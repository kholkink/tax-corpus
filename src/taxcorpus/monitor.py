"""Мониторинг дел (F2 плана ПО): события корпуса -> подписки дел -> уведомления -> обновление ресёрча.

События (corpus_event) порождают перезагрузка акта (unit_text_changed / unit_repealed / unit_added /
parameter_changed, см. db.detect_unit_changes), краулеры (document_added / document_status_changed)
и проверка редакций банка (edition_available). Подписки дела выводятся из шапок файлов агента
(sources: unit_id и doc_id) плюс уровень статьи; вручную — таблица subscription. match_events()
сопоставляет новые события с подписками и создаёт notification. what_changed() строит
детерминированный раздел «Что изменилось» (diff архивной и текущей версии, правки, новые
документы). apply_notification() запускает сессию агента в деле с задачей обновить файл.
"""

from __future__ import annotations

import difflib
import json
from datetime import date

from .resolver import article_of_unit_id

DOC_KINDS = ("document_added", "document_status_changed")
UNIT_KINDS = ("unit_text_changed", "unit_repealed", "unit_added", "parameter_changed")


def _sources_by_workspace(conn) -> dict[int, dict]:
    """workspace_id -> {slug, units: {unit_id: [paths]}, docs: {doc_id: [paths]}} + ручные подписки."""
    out: dict[int, dict] = {}
    rows = conn.execute(
        "SELECT w.workspace_id, w.slug, f.path, f.sources FROM workspace w "
        "JOIN workspace_file f ON f.workspace_id = w.workspace_id WHERE jsonb_array_length(f.sources) > 0").fetchall()
    for r in rows:
        ws = out.setdefault(r["workspace_id"], {"slug": r["slug"], "units": {}, "docs": {}})
        for src in r["sources"]:
            bucket = ws["units"] if src.startswith("nk") else ws["docs"]
            bucket.setdefault(src, []).append(r["path"])
    for r in conn.execute("SELECT s.workspace_id, w.slug, s.unit_id, s.doc_id FROM subscription s "
                          "JOIN workspace w ON w.workspace_id = s.workspace_id").fetchall():
        ws = out.setdefault(r["workspace_id"], {"slug": r["slug"], "units": {}, "docs": {}})
        if r["unit_id"]:
            ws["units"].setdefault(r["unit_id"], []).append("(подписка)")
        if r["doc_id"]:
            ws["docs"].setdefault(r["doc_id"], []).append("(подписка)")
    return out


def _related(sub_unit: str, event_unit: str) -> bool:
    """Подписка задета событием: та же единица, предок/потомок, или общая статья."""
    return (sub_unit == event_unit or event_unit.startswith(sub_unit + ".") or sub_unit.startswith(event_unit + ".")
            or article_of_unit_id(sub_unit) == event_unit)


def match_events(conn, since_event_id: int | None = None) -> dict:
    """Новые события (после чекпоинта) -> notification для задетых дел. Возвращает статистику."""
    if since_event_id is None:
        row = conn.execute("SELECT value FROM monitor_state WHERE key = 'last_event_id'").fetchone()
        since_event_id = int(row["value"]) if row else 0
    events = conn.execute("SELECT * FROM corpus_event WHERE event_id > %s ORDER BY event_id", (since_event_id,)).fetchall()
    subs = _sources_by_workspace(conn)
    created = 0
    last = since_event_id
    with conn.transaction():
        for e in events:
            last = max(last, e["event_id"])
            targets: list[str] = []
            if e["kind"] in UNIT_KINDS and e["unit_id"]:
                targets = [e["unit_id"]]
            elif e["kind"] in DOC_KINDS:
                targets = [e["doc_id"]] if e["doc_id"] else []
                targets += list((e["payload"] or {}).get("cites") or [])
            elif e["kind"] == "edition_available":
                continue  # новая редакция акта в банке — общее событие, не по делу
            if not targets:
                continue
            for wid, ws in subs.items():
                matched, paths = [], set()
                for t in targets:
                    if t.startswith("nk"):
                        for sub_unit, ps in ws["units"].items():
                            if _related(sub_unit, t):
                                matched.append(sub_unit)
                                paths.update(ps)
                    elif t in ws["docs"]:
                        matched.append(t)
                        paths.update(ws["docs"][t])
                if matched:
                    conn.execute(
                        "INSERT INTO notification (workspace_id, event_id, matched_by, paths) VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (workspace_id, event_id) DO NOTHING",
                        (wid, e["event_id"], json.dumps(sorted(set(matched)), ensure_ascii=False),
                         json.dumps(sorted(paths), ensure_ascii=False)))
                    created += 1
        conn.execute("INSERT INTO monitor_state (key, value) VALUES ('last_event_id', %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (str(last),))
    return {"events": len(events), "notifications": created, "last_event_id": last, "workspaces": len(subs)}


def notifications(conn, slug: str, status: str | None = None, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT n.notification_id, n.status, n.matched_by, n.paths, n.created_at, n.applied_at, n.applied_session,
               e.event_id, e.kind, e.unit_id, e.doc_id, e.payload, e.detected_at
        FROM notification n JOIN workspace w ON w.workspace_id = n.workspace_id
        JOIN corpus_event e ON e.event_id = n.event_id
        WHERE w.slug = %s AND (%s::text IS NULL OR n.status = %s::text)
        ORDER BY n.notification_id DESC LIMIT %s
        """, (slug, status, status, limit)).fetchall()
    return [dict(r) for r in rows]


def set_status(conn, notification_id: int, status: str, session_id: str | None = None) -> dict | None:
    row = conn.execute(
        "UPDATE notification SET status = %s, applied_at = CASE WHEN %s = 'applied' THEN now() ELSE applied_at END, "
        "applied_session = COALESCE(%s, applied_session) WHERE notification_id = %s RETURNING *",
        (status, status, session_id, notification_id)).fetchone()
    return dict(row) if row else None


def subscribe(conn, workspace_id: int, unit_id: str | None = None, doc_id: str | None = None, note: str = "") -> dict:
    row = conn.execute(
        "INSERT INTO subscription (workspace_id, unit_id, doc_id, note) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (workspace_id, unit_id, doc_id) DO UPDATE SET note = EXCLUDED.note RETURNING *",
        (workspace_id, unit_id, doc_id, note)).fetchone()
    return dict(row)


def what_changed(conn, unit_id: str, since: str | None = None, as_of: str | None = None) -> dict:
    """Детерминированный раздел «Что изменилось»: diff архивной и текущей версии текста, правки, документы."""
    from .db import get_interpretations, get_unit, list_amendments
    as_of = as_of or date.today().isoformat()
    current = get_unit(conn, unit_id, as_of)
    archive = conn.execute(
        "SELECT text_hash, text, full_text, valid_from, valid_to, archived_at FROM unit_text_archive "
        "WHERE unit_id = %s ORDER BY archived_at DESC, id DESC", (unit_id,)).fetchall()
    cur_hash = current.get("text_hash") if current else None
    previous = next((a for a in archive if a["text_hash"] != cur_hash), None)
    diff = None
    if previous is not None:
        old_text = (previous["full_text"] or previous["text"]).splitlines()
        new_text = ((current or {}).get("full_text") or (current or {}).get("text") or "").splitlines()
        diff = "\n".join(difflib.unified_diff(old_text, new_text, "прежняя редакция", "текущая редакция", lineterm="", n=1))
    amendments = list_amendments(conn, unit_id, since)
    docs = [d for d in get_interpretations(conn, unit_id, as_of, 50) if not since or str(d["date"]) >= since]
    return {"unit_id": unit_id, "as_of": as_of, "since": since, "in_force": current is not None,
            "current": ({"text_hash": cur_hash, "valid_from": str(current.get("valid_from")), "label": current.get("label")}
                        if current else None),
            "previous": ({"text_hash": previous["text_hash"], "valid_from": str(previous["valid_from"]),
                          "archived_at": str(previous["archived_at"])} if previous else None),
            "diff": diff, "amendments_since": amendments,
            "documents_since": [{k: d.get(k) for k in ("doc_id", "kind", "agency", "number", "date", "title", "mandatory", "status")}
                                for d in docs],
            "versions_archived": len(archive)}


def render_change(change: dict) -> str:
    lines = [f"## Что изменилось: {change['unit_id']}" + (f" с {change['since']}" if change.get("since") else "")]
    if not change["in_force"]:
        lines.append(f"- норма НЕ действует на {change['as_of']}")
    if change.get("previous"):
        lines.append(f"- текст изменён: прежняя версия (архив от {change['previous']['archived_at'][:10]}) -> "
                     f"текущая с {change['current']['valid_from'] if change.get('current') else '?'}")
    if change.get("diff"):
        lines += ["", "```diff", change["diff"][:6000], "```"]
    if change["amendments_since"]:
        lines.append("- правки: " + "; ".join(f"{a.get('amending_act_date') or '?'} {a.get('operation')} ФЗ № {a.get('amending_act_number')}"
                                               for a in change["amendments_since"][:10]))
    if change["documents_since"]:
        lines.append("- новые разъяснения и практика: " + "; ".join(
            f"{d['agency'] or ''} {d['number']} от {d['date']}{' (обязательное)' if d.get('mandatory') else ''}"
            for d in change["documents_since"][:10]))
    if len(lines) == 1:
        lines.append("- зафиксированных изменений нет")
    return "\n".join(lines)


def task_for(conn, n: dict) -> str:
    """Задача агенту по уведомлению: что случилось, какие файлы дела задеты, детерминированный diff."""
    kind = n["kind"]
    paths = ", ".join(n.get("paths") or []) or "файлы не определены"
    head = {"unit_text_changed": f"изменился текст нормы {n['unit_id']}",
            "unit_repealed": f"норма {n['unit_id']} исключена из акта",
            "unit_added": f"в акте появилась новая единица {n['unit_id']}",
            "parameter_changed": f"изменился параметр {n['payload'].get('name')} ({n['unit_id']}): "
                                 f"{n['payload'].get('old')} -> {n['payload'].get('new')}",
            "document_added": f"появился документ {n['doc_id']} ({n['payload'].get('agency')} № {n['payload'].get('number')} "
                              f"от {n['payload'].get('date')})",
            "document_status_changed": f"документ {n['doc_id']} сменил статус {n['payload'].get('old')} -> {n['payload'].get('new')}"}
    text = [f"Событие мониторинга #{n['event_id']}: {head.get(kind, kind)}.",
            f"Задеты файлы дела: {paths} (источники: {', '.join(n.get('matched_by') or [])}).",
            "Задача: перечитай задетые файлы, проверь, влияет ли изменение на выводы для периода дела, и запиши "
            "новую версию каждого задетого файла через write_file с разделом «Что изменилось для нашего периода» "
            "(последствия сформулируй сам, факты изменения — ниже). Если изменение не влияет — допиши в notes/протокол.md "
            "через append_note короткую отметку и объясни почему."]
    if n.get("unit_id"):
        text += ["", render_change(what_changed(conn, n["unit_id"], since=(n["payload"] or {}).get("edition_from")))]
    return "\n".join(text)


def apply_notification(conn, ws, agent, notification_id: int) -> dict:
    """Сессия агента в деле по уведомлению; статус -> applied; запись в журнал дела."""
    from .case_session import CaseSession
    from .collab import CollabStore
    n = next((x for x in notifications(conn, ws.manifest.slug, None, 500) if x["notification_id"] == notification_id), None)
    if n is None:
        raise KeyError(notification_id)
    session = CaseSession(ws, agent)
    turn = session.send(task_for(conn, n))
    set_status(conn, notification_id, "applied", session.session_id)
    CollabStore(ws).log("system", "monitor_apply", f"event:{n['event_id']}",
                        {"notification_id": notification_id, "session_id": session.session_id,
                         "files_written": sorted(set(session.files_written))})
    return {"notification_id": notification_id, "session_id": session.session_id, "kind": turn.kind,
            "text": turn.text, "files_written": sorted(set(session.files_written)), "status": session.status}


MONITOR_TOOLS: list[dict] = [
    {"name": "list_notifications",
     "description": "Открытые уведомления мониторинга по делу: какие нормы/документы, на которые опираются файлы "
                    "дела, изменились (событие, задетые файлы). Используй, когда юрист спрашивает, что поменялось.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True},
]
MONITOR_TOOL_NAMES = {t["name"] for t in MONITOR_TOOLS}
