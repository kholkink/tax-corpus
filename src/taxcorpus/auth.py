"""Пользователи, токены и роли в делах (P5 плана ПО).

Режимы: TAXCORPUS_AUTH не задан / off — открытый режим (один локальный юрист, принципал «local»
со всеми правами; так работают CLI и разработка). TAXCORPUS_AUTH=on — каждый запрос к API несёт
токен (Authorization: Bearer tc_… или X-API-Key); доступ к делу — по роли участника:
viewer (чтение), editor (файлы, факты, сессии, черновики), owner (+ участники, удаление).
Глобальный admin видит всё. Реестр — config/users.json (в .gitignore; токены хранятся как sha256),
зеркало в БД — таблицы app_user / api_token / workspace_member (миграция 007).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("viewer", "editor", "owner")
ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}
DEFAULT_USERS_PATH = Path(__file__).resolve().parents[2] / "config" / "users.json"


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    name: str
    admin: bool = False
    local: bool = False          # открытый режим: локальный пользователь со всеми правами

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "email": self.email, "name": self.name, "admin": self.admin, "local": self.local}


LOCAL = Principal("local", "local@localhost", "локальный юрист", admin=True, local=True)


def enabled() -> bool:
    return (os.environ.get("TAXCORPUS_AUTH") or "").strip().lower() in ("1", "on", "true", "yes")


class UserStore:
    """config/users.json: users, tokens (sha256), members (slug -> user -> role)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("TAXCORPUS_USERS") or DEFAULT_USERS_PATH)
        self.data = {"users": [], "tokens": [], "members": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            for key in ("users", "tokens", "members"):
                self.data.setdefault(key, [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- пользователи ---
    def user(self, email: str) -> dict | None:
        email = email.strip().lower()
        return next((u for u in self.data["users"] if u["email"] == email), None)

    def user_by_id(self, user_id: str) -> dict | None:
        return next((u for u in self.data["users"] if u["user_id"] == user_id), None)

    def add_user(self, email: str, name: str, admin: bool = False) -> dict:
        email = email.strip().lower()
        if self.user(email):
            raise AuthError(409, f"пользователь {email} уже есть")
        u = {"user_id": "u_" + secrets.token_hex(6), "email": email, "name": name.strip() or email,
             "admin": bool(admin), "created_at": _now()}
        self.data["users"].append(u)
        self.save()
        return u

    def users(self) -> list[dict]:
        return list(self.data["users"])

    # --- токены ---
    def issue_token(self, email: str, label: str = "") -> str:
        u = self.user(email)
        if not u:
            raise AuthError(404, f"нет пользователя {email}")
        token = "tc_" + secrets.token_hex(24)
        self.data["tokens"].append({"token_id": "t_" + secrets.token_hex(4), "user_id": u["user_id"],
                                    "sha256": _sha(token), "label": label, "created_at": _now(),
                                    "last_used_at": None, "revoked": False})
        self.save()
        return token

    def revoke_token(self, token_id: str) -> bool:
        for t in self.data["tokens"]:
            if t["token_id"] == token_id and not t["revoked"]:
                t["revoked"] = True
                self.save()
                return True
        return False

    def tokens(self, email: str | None = None) -> list[dict]:
        uid = self.user(email)["user_id"] if email and self.user(email) else None
        return [{k: v for k, v in t.items() if k != "sha256"} for t in self.data["tokens"]
                if uid is None or t["user_id"] == uid]

    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise AuthError(401, "нужен токен: Authorization: Bearer tc_… (python -m taxcorpus users token)")
        digest = _sha(token.strip())
        t = next((t for t in self.data["tokens"] if t["sha256"] == digest and not t["revoked"]), None)
        if t is None:
            raise AuthError(401, "токен не найден или отозван")
        u = self.user_by_id(t["user_id"])
        if u is None:
            raise AuthError(401, "пользователь токена удалён")
        t["last_used_at"] = _now()
        return Principal(u["user_id"], u["email"], u["name"], admin=bool(u.get("admin")))

    # --- участники дел ---
    def grant(self, slug: str, email: str, role: str) -> dict:
        if role not in ROLES:
            raise AuthError(400, f"роль должна быть одной из {', '.join(ROLES)}")
        u = self.user(email)
        if not u:
            raise AuthError(404, f"нет пользователя {email}")
        for m in self.data["members"]:
            if m["slug"] == slug and m["user_id"] == u["user_id"]:
                m["role"] = role
                self.save()
                return m
        m = {"slug": slug, "user_id": u["user_id"], "role": role, "granted_at": _now()}
        self.data["members"].append(m)
        self.save()
        return m

    def revoke(self, slug: str, email: str) -> bool:
        u = self.user(email)
        if not u:
            return False
        before = len(self.data["members"])
        self.data["members"] = [m for m in self.data["members"] if not (m["slug"] == slug and m["user_id"] == u["user_id"])]
        self.save()
        return len(self.data["members"]) < before

    def members(self, slug: str) -> list[dict]:
        out = []
        for m in self.data["members"]:
            if m["slug"] == slug:
                u = self.user_by_id(m["user_id"]) or {}
                out.append({"email": u.get("email"), "name": u.get("name"), "role": m["role"], "granted_at": m.get("granted_at")})
        return out

    def role(self, slug: str, principal: Principal) -> str | None:
        if principal.admin:
            return "owner"
        m = next((m for m in self.data["members"] if m["slug"] == slug and m["user_id"] == principal.user_id), None)
        return m["role"] if m else None

    def slugs_for(self, principal: Principal) -> set[str] | None:
        """None = все дела (admin / локальный режим)."""
        if principal.admin:
            return None
        return {m["slug"] for m in self.data["members"] if m["user_id"] == principal.user_id}


def require(store: UserStore | None, slug: str, principal: Principal, needed: str) -> str:
    """Роль принципала в деле не ниже needed, иначе AuthError 403."""
    if principal.local or principal.admin:
        return "owner"
    role = store.role(slug, principal) if store else None
    if role is None:
        raise AuthError(403, f"нет доступа к делу {slug}")
    if ROLE_RANK[role] < ROLE_RANK[needed]:
        raise AuthError(403, f"нужна роль {needed}, у вас {role}")
    return role


def required_role(method: str, path: str) -> str:
    """Роль по методу и пути: чтение — viewer, изменения — editor, участники и удаление дела — owner."""
    if "/members" in path or (method == "DELETE" and path.count("/") == 2):
        return "owner"
    return "viewer" if method in ("GET", "HEAD", "OPTIONS") else "editor"
