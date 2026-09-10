-- F8 плана ПО: комментарии и журнал активности (источник истины — файлы дела, здесь зеркало).
CREATE TABLE IF NOT EXISTS comment (
    comment_id   TEXT PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    version      INT,
    anchor       TEXT,
    author       TEXT NOT NULL,
    text         TEXT NOT NULL,
    parent_id    TEXT,
    created_at   TIMESTAMPTZ,
    resolved     BOOLEAN NOT NULL DEFAULT false,
    resolved_by  TEXT,
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_comment_ws_path ON comment(workspace_id, path);

CREATE TABLE IF NOT EXISTS activity (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    at           TIMESTAMPTZ NOT NULL,
    actor        TEXT NOT NULL,                  -- user:<email> | agent | system
    action       TEXT NOT NULL,
    target       TEXT,
    details      JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, at, actor, action, target)
);
