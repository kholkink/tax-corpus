-- F10 плана ПО: региональный слой — законы субъектов как источник параметров с регионом.
CREATE TABLE IF NOT EXISTS regional_act (
    regional_act_id SERIAL PRIMARY KEY,
    region          TEXT NOT NULL,                -- код субъекта («77», «50» …)
    number          TEXT NOT NULL,
    adoption_date   DATE,
    title           TEXT,
    source_url      TEXT,                         -- pravo.gov.ru региональный раздел
    text_sha256     TEXT,
    retrieved_at    TIMESTAMPTZ,
    UNIQUE (region, number, adoption_date)
);
ALTER TABLE parameter ADD COLUMN IF NOT EXISTS regional_act_id INT REFERENCES regional_act(regional_act_id);
ALTER TABLE parameter ADD COLUMN IF NOT EXISTS tax TEXT;        -- usn | property | transport | land | psn | profit
CREATE INDEX IF NOT EXISTS idx_parameter_region ON parameter(region, tax);
