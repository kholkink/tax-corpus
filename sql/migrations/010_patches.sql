-- F3 плана ПО: изменяющие законы и патчи (инструкции) с результатом применения.
CREATE TABLE IF NOT EXISTS amending_act (
    amending_act_id SERIAL PRIMARY KEY,
    number          TEXT NOT NULL,                -- «281-ФЗ»
    adoption_date   DATE,
    publication_date DATE,
    eo_number       TEXT,                         -- номер официального опубликования (pravo.gov.ru)
    title           TEXT,
    source_url      TEXT,
    text            TEXT,
    effective_date  DATE,
    effective_note  TEXT,
    retrieved_at    TIMESTAMPTZ,
    UNIQUE (number, adoption_date)
);

CREATE TABLE IF NOT EXISTS patch (
    patch_id        BIGSERIAL PRIMARY KEY,
    amending_act_id INT REFERENCES amending_act(amending_act_id) ON DELETE CASCADE,
    act_code        TEXT NOT NULL,
    instruction     TEXT NOT NULL,
    operation       TEXT NOT NULL,
    target_unit_id  TEXT,
    effective_date  DATE,
    applied_status  TEXT NOT NULL,                -- auto | manual | verified | failed | dry_run
    reason          TEXT,
    diff            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_patch_unit ON patch(target_unit_id);
