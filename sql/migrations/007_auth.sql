-- P5 плана ПО: пользователи, токены, участники дел (источник истины — config/users.json, здесь зеркало).
CREATE TABLE IF NOT EXISTS app_user (
    user_id    TEXT PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    admin      BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS api_token (
    token_id     TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    sha256       TEXT NOT NULL,
    label        TEXT,
    created_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked      BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS workspace_member (
    slug       TEXT NOT NULL,
    user_id    TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    role       TEXT NOT NULL,                 -- viewer | editor | owner
    granted_at TIMESTAMPTZ,
    PRIMARY KEY (slug, user_id)
);
