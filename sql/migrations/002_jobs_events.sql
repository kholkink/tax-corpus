-- P3 плана ПО: журнал запусков задач; F2: события корпуса (основа мониторинга дел)
CREATE TABLE IF NOT EXISTS job_run (
    run_id      BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed
    stats       JSONB NOT NULL DEFAULT '{}'::jsonb,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_run_name ON job_run(name, started_at DESC);

CREATE TABLE IF NOT EXISTS corpus_event (
    event_id    BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,      -- edition_available | document_added | document_status_changed | unit_text_changed | unit_repealed
    act_code    TEXT,
    unit_id     TEXT,
    doc_id      TEXT,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot_id INT,
    run_id      BIGINT REFERENCES job_run(run_id)
);
CREATE INDEX IF NOT EXISTS idx_corpus_event_unit ON corpus_event(unit_id);
CREATE INDEX IF NOT EXISTS idx_corpus_event_doc ON corpus_event(doc_id);
CREATE INDEX IF NOT EXISTS idx_corpus_event_detected ON corpus_event(detected_at DESC);
