-- F4 плана ПО: позиции документов по нормам (источник истины — data/interpretations/positions.jsonl).
ALTER TABLE document ADD COLUMN IF NOT EXISTS court TEXT;
ALTER TABLE document ADD COLUMN IF NOT EXISTS case_number TEXT;
ALTER TABLE document ADD COLUMN IF NOT EXISTS outcome TEXT;        -- taxpayer | authority | mixed | n/a
ALTER TABLE document ADD COLUMN IF NOT EXISTS topics JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS position (
    position_id  TEXT PRIMARY KEY,                 -- <doc_id>#<unit_id>
    doc_id       TEXT NOT NULL REFERENCES document(doc_id) ON DELETE CASCADE,
    unit_id      TEXT NOT NULL REFERENCES unit(unit_id),
    stance       TEXT NOT NULL,                    -- pro_taxpayer | pro_authority | neutral | none
    summary      TEXT NOT NULL,
    quote        TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    extracted_by TEXT NOT NULL,                    -- llm | manual
    model        TEXT,
    verified     BOOLEAN NOT NULL DEFAULT false,
    verified_by  TEXT,
    created_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_position_unit ON position(unit_id);
CREATE INDEX IF NOT EXISTS idx_position_doc ON position(doc_id);
