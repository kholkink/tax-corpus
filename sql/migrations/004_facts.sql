-- F6 плана ПО: факты дела (источник истины — workspaces/<slug>/facts.json, здесь зеркало).
CREATE TABLE IF NOT EXISTS fact (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  INT NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    fact_id       INT NOT NULL,
    kind          TEXT NOT NULL,               -- date | amount | party | event | period | regime | other
    value         TEXT NOT NULL,
    text          TEXT NOT NULL,
    role          TEXT,
    source_path   TEXT,
    page          INT,
    quote         TEXT,
    extracted_by  TEXT NOT NULL,               -- agent | lawyer
    confirmed     BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ,
    confirmed_at  TIMESTAMPTZ,
    UNIQUE (workspace_id, fact_id)
);
