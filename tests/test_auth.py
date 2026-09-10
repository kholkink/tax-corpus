"""Пользователи, токены и роли (P5): реестр, проверка ролей, middleware API."""

import pytest

from taxcorpus.auth import LOCAL, AuthError, Principal, UserStore, require, required_role


def test_user_store_tokens_and_roles(tmp_path):
    store = UserStore(tmp_path / "users.json")
    anna = store.add_user("Anna@Firm.ru", "Анна", admin=False)
    boss = store.add_user("boss@firm.ru", "Партнёр", admin=True)
    with pytest.raises(AuthError):
        store.add_user("anna@firm.ru", "дубль")
    token = store.issue_token("anna@firm.ru", "ноутбук")
    assert token.startswith("tc_") and token not in (tmp_path / "users.json").read_text(encoding="utf-8")
    p = store.authenticate(token)
    assert p.email == "anna@firm.ru" and not p.admin and store.tokens("anna@firm.ru")[0]["last_used_at"]
    with pytest.raises(AuthError) as exc:
        store.authenticate("tc_wrong")
    assert exc.value.status == 401
    assert store.role("delo", p) is None and store.slugs_for(p) == set()
    store.grant("delo", "anna@firm.ru", "viewer")
    assert store.role("delo", p) == "viewer" and store.slugs_for(p) == {"delo"}
    assert require(store, "delo", p, "viewer") == "viewer"
    with pytest.raises(AuthError) as exc:
        require(store, "delo", p, "editor")
    assert exc.value.status == 403
    store.grant("delo", "anna@firm.ru", "editor")                        # повышение роли, без дубля
    assert len(store.members("delo")) == 1 and require(store, "delo", p, "editor") == "editor"
    admin = Principal(boss["user_id"], boss["email"], boss["name"], admin=True)
    assert require(store, "любое", admin, "owner") == "owner" and store.slugs_for(admin) is None
    assert require(None, "любое", LOCAL, "owner") == "owner"
    assert store.revoke("delo", "anna@firm.ru") and store.role("delo", p) is None
    tid = store.tokens("anna@firm.ru")[0]["token_id"]
    assert store.revoke_token(tid) and not store.revoke_token(tid)
    with pytest.raises(AuthError):
        store.authenticate(token)
    store2 = UserStore(tmp_path / "users.json")                           # перечитано с диска
    assert store2.user("anna@firm.ru")["user_id"] == anna["user_id"]
    assert required_role("GET", "/workspaces/x/files") == "viewer" and required_role("POST", "/workspaces/x/facts") == "editor"
    assert required_role("POST", "/workspaces/x/members") == "owner" and required_role("DELETE", "/workspaces/x") == "owner"


def test_api_enforces_roles(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from taxcorpus import api

    monkeypatch.setattr(api, "WORKSPACES_ROOT", tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    monkeypatch.setattr(api, "_sync", lambda *a, **k: None)
    monkeypatch.setenv("TAXCORPUS_AUTH", "on")
    monkeypatch.setenv("TAXCORPUS_USERS", str(tmp_path / "users.json"))
    store = UserStore(tmp_path / "users.json")
    store.add_user("owner@firm.ru", "Владелец")
    store.add_user("view@firm.ru", "Читатель")
    t_owner = store.issue_token("owner@firm.ru")
    t_view = store.issue_token("view@firm.ru")
    c = TestClient(api.app)
    assert c.get("/health").status_code == 200                         # публичный
    assert c.get("/workspaces").status_code == 401                     # без токена
    assert c.get("/workspaces", headers={"X-API-Key": "tc_nope"}).status_code == 401
    H = lambda t: {"Authorization": f"Bearer {t}"}
    me = c.get("/me", headers=H(t_owner)).json()
    assert me["auth"] and me["email"] == "owner@firm.ru" and me["workspaces"] == []
    r = c.post("/workspaces", json={"slug": "delo", "title": "Дело"}, headers=H(t_owner))
    assert r.status_code == 201
    assert [m["role"] for m in c.get("/workspaces/delo/members", headers=H(t_owner)).json()] == ["owner"]
    assert c.get("/workspaces", headers=H(t_view)).json() == []          # читатель ещё не участник
    assert c.get("/workspaces/delo", headers=H(t_view)).status_code == 403
    assert c.post("/workspaces/delo/members", json={"email": "view@firm.ru", "role": "viewer"}, headers=H(t_view)).status_code == 403
    assert c.post("/workspaces/delo/members", json={"email": "view@firm.ru", "role": "viewer"}, headers=H(t_owner)).status_code == 201
    assert [w["slug"] for w in c.get("/workspaces", headers=H(t_view)).json()] == ["delo"]
    assert c.get("/workspaces/delo", headers=H(t_view)).status_code == 200
    r = c.post("/workspaces/delo/facts", json={"kind": "event", "value": "27.03.2026", "text": "акт"}, headers=H(t_view))
    assert r.status_code == 403 and "editor" in r.json()["detail"]
    assert c.post("/workspaces/delo/members", json={"email": "view@firm.ru", "role": "editor"}, headers=H(t_owner)).status_code == 201
    assert c.post("/workspaces/delo/facts", json={"kind": "event", "value": "27.03.2026", "text": "акт"}, headers=H(t_view)).status_code == 201
    assert c.post("/workspaces/delo/members", json={"email": "nobody@firm.ru"}, headers=H(t_owner)).status_code == 404
    assert c.delete("/workspaces/delo/members/view@firm.ru", headers=H(t_owner)).json() == {"revoked": True}
    assert c.get("/workspaces/delo", headers=H(t_view)).status_code == 403
    monkeypatch.setenv("TAXCORPUS_AUTH", "off")                        # открытый режим — без токена
    assert c.get("/workspaces").status_code == 200 and c.get("/me").json()["local"]
