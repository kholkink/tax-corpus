"""Совместная работа в деле (F8 плана ПО): комментарии к файлам агента и журнал активности.

Источник истины — файлы дела: comments.json (комментарии с привязкой к файлу, версии и абзацу)
и activity.jsonl (кто что сделал: user:<email> | agent | system). БД — зеркало (миграция 008).
Агент видит комментарии инструментом list_comments и отвечает reply_comment; закрывает
(resolve) комментарий юрист. Каждое действие агента и юриста попадает в журнал.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .workspace import Workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_version(ws: Workspace, path: str) -> int | None:
    from .workspace_store import file_header
    try:
        return file_header(ws.path / path).get("version")
    except Exception:  # noqa: BLE001
        return None


class CollabStore:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.comments_path = ws.path / "comments.json"
        self.activity_path = ws.path / "activity.jsonl"
        self.comments: list[dict] = []
        if self.comments_path.exists():
            self.comments = json.loads(self.comments_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.comments_path.write_text(json.dumps(self.comments, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- комментарии --------------------------------------------------------------------
    def add_comment(self, path: str, text: str, author: str, anchor: str | None = None,
                    parent_id: str | None = None) -> dict:
        if not (self.ws.path / path).is_file():
            raise FileNotFoundError(path)
        if parent_id and not any(c["comment_id"] == parent_id for c in self.comments):
            raise KeyError(parent_id)
        c = {"comment_id": "c_" + secrets.token_hex(4), "path": path, "version": file_version(self.ws, path),
             "anchor": (anchor or "").strip() or None, "author": author, "text": text.strip(),
             "parent_id": parent_id, "created_at": _now(), "resolved": False, "resolved_by": None, "resolved_at": None}
        self.comments.append(c)
        self._save()
        self.log(author, "comment", path, {"comment_id": c["comment_id"], "reply": bool(parent_id)})
        return c

    def resolve(self, comment_id: str, by: str, resolved: bool = True) -> dict:
        c = self.get(comment_id)
        for item in [c, *[r for r in self.comments if r.get("parent_id") == comment_id]]:   # ветка закрывается целиком
            item["resolved"], item["resolved_by"], item["resolved_at"] = resolved, (by if resolved else None), (_now() if resolved else None)
        self._save()
        self.log(by, "resolve" if resolved else "reopen", c["path"], {"comment_id": comment_id})
        return c

    def get(self, comment_id: str) -> dict:
        for c in self.comments:
            if c["comment_id"] == comment_id:
                return c
        raise KeyError(comment_id)

    def list(self, path: str | None = None, open_only: bool = False) -> list[dict]:
        out = [c for c in self.comments if (path is None or c["path"] == path) and (not open_only or not c["resolved"])]
        return sorted(out, key=lambda c: c["created_at"])

    def threads(self, path: str, open_only: bool = True) -> list[dict]:
        """Комментарии юристов с ответами — то, что видит агент."""
        roots = [c for c in self.list(path, open_only) if not c.get("parent_id")]
        out = []
        for r in roots:
            replies = [c for c in self.comments if c.get("parent_id") == r["comment_id"]]
            out.append({**r, "replies": sorted(replies, key=lambda c: c["created_at"])})
        return out

    # --- журнал -------------------------------------------------------------------------
    def log(self, actor: str, action: str, target: str | None = None, details: dict | None = None) -> dict:
        entry = {"at": _now(), "actor": actor, "action": action, "target": target, "details": details or {}}
        with self.activity_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def activity(self, limit: int = 50) -> list[dict]:
        if not self.activity_path.exists():
            return []
        lines = [line for line in self.activity_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [json.loads(line) for line in lines[-limit:]][::-1]


COLLAB_TOOLS: list[dict] = [
    {"name": "list_comments",
     "description": "Открытые комментарии юристов к файлу агента (с абзацем-якорем и ответами). Перед "
                    "обновлением файла в research/ или drafts/ прочитай комментарии и учти их в новой версии; "
                    "на каждый ответь reply_comment.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"], "additionalProperties": False},
     "strict": True},
    {"name": "reply_comment",
     "description": "Ответить на комментарий юриста: что изменено в файле и где, или почему замечание не "
                    "принято (со ссылкой на норму). Закрывает комментарий сам юрист.",
     "input_schema": {"type": "object",
                      "properties": {"comment_id": {"type": "string"}, "text": {"type": "string"}},
                      "required": ["comment_id", "text"], "additionalProperties": False},
     "strict": True},
]
COLLAB_TOOL_NAMES = {t["name"] for t in COLLAB_TOOLS}
