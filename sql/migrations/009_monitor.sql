-- F2 плана ПО: мониторинг дел — архив версий текста единиц, подписки, уведомления, чекпоинт.
CREATE TABLE IF NOT EXISTS unit_text_archive (
    id          BIGSERIAL PRIMARY KEY,
    unit_id     TEXT NOT NULL,                 -- без FK: единица могла быть исключена из акта
    act_code    TEXT,
    text_hash   TEXT NOT NULL,
    text        TEXT NOT NULL,
    full_text   TEXT,
    valid_from  DATE,
    valid_to    DATE,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (unit_id, text_hash)
);
CREATE INDEX IF NOT EXISTS idx_unit_text_archive_unit ON unit_text_archive(unit_id);

CREATE TABLE IF NOT EXISTS subscription (
    subscription_id BIGSERIAL PRIMARY KEY,
    workspace_id    INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    unit_id         TEXT,
    doc_id          TEXT,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, unit_id, doc_id)
);

CREATE TABLE IF NOT EXISTS notification (
    notification_id BIGSERIAL PRIMARY KEY,
    workspace_id    INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    event_id        BIGINT NOT NULL REFERENCES corpus_event(event_id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'open',   -- open | seen | applied
    matched_by      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- какие источники дела задеты
    paths           JSONB NOT NULL DEFAULT '[]'::jsonb,  -- файлы дела, опирающиеся на источник
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at      TIMESTAMPTZ,
    applied_session TEXT,
    UNIQUE (workspace_id, event_id)
);

CREATE TABLE IF NOT EXISTS monitor_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
