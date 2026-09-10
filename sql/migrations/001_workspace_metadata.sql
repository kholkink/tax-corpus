-- P1 плана ПО: метаданные дел в БД (файлы остаются на диске — источник истины,
-- БД — зеркало для мониторинга, коллаборации, карты точности).
CREATE TABLE IF NOT EXISTS workspace (
    workspace_id    SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    client          TEXT,
    as_of           DATE NOT NULL,
    jurisdiction    TEXT,
    confidentiality TEXT NOT NULL DEFAULT 'standard',   -- standard | sensitive
    corpus_snapshot INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_file (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    owner        TEXT NOT NULL,                      -- lawyer | agent
    sha256       TEXT NOT NULL,
    size         BIGINT NOT NULL,
    version      INT,
    sources      JSONB NOT NULL DEFAULT '[]'::jsonb, -- unit_id/doc_id из шапки файла агента
    verification JSONB,
    modified_at  TIMESTAMPTZ,
    UNIQUE (workspace_id, path)
);
CREATE INDEX IF NOT EXISTS idx_workspace_file_sources ON workspace_file USING GIN (sources);

CREATE TABLE IF NOT EXISTS session (
    session_id    TEXT PRIMARY KEY,
    workspace_id  INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    status        TEXT NOT NULL,                     -- active | waiting_user | done
    model         TEXT,
    started_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ,
    tool_calls    INT NOT NULL DEFAULT 0,
    files_written JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS session_question (
    id           BIGSERIAL PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES session(session_id) ON DELETE CASCADE,
    tool_use_id  TEXT NOT NULL,
    question     TEXT NOT NULL,
    options      JSONB NOT NULL DEFAULT '[]'::jsonb,
    asked_at     TIMESTAMPTZ,
    answer       TEXT,
    answered_at  TIMESTAMPTZ,
    UNIQUE (session_id, tool_use_id)
);

-- F1: аудит документов
CREATE TABLE IF NOT EXISTS audit (
    audit_id     BIGSERIAL PRIMARY KEY,
    workspace_id INT REFERENCES workspace(workspace_id) ON DELETE SET NULL,
    source       TEXT,                               -- путь файла дела или 'text'
    text_sha256  TEXT NOT NULL,
    as_of        DATE NOT NULL,
    doc_date     DATE,
    report       JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
