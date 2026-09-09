-- Схема корпуса (срез фазы 1: парсер, канонические ID, ссылки).
-- Полная модель — в архитектурном плане: Act / Edition / Unit / UnitText /
-- Amendment / Reference / Letter / CourtAct / Term / Parameter / Snapshot.

CREATE TABLE IF NOT EXISTS act (
    act_id          SERIAL PRIMARY KEY,
    act_code        TEXT NOT NULL UNIQUE,       -- nk1, nk2, ...
    kind            TEXT NOT NULL,              -- code | federal_law | gov_decree | fns_order | letter | court_act
    official_number TEXT,                       -- 146-ФЗ
    adoption_date   DATE,
    title           TEXT NOT NULL,
    source_url      TEXT,
    retrieved_at    TIMESTAMPTZ,
    source_sha256   TEXT
);

CREATE TABLE IF NOT EXISTS edition (
    edition_id        SERIAL PRIMARY KEY,
    act_id            INT NOT NULL REFERENCES act(act_id),
    valid_from        DATE NOT NULL,
    valid_to          DATE,
    created_by_act_id INT REFERENCES act(act_id),
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS unit (
    unit_id        TEXT PRIMARY KEY,            -- nk1.ch6.art14.p1.sp2
    act_id         INT NOT NULL REFERENCES act(act_id),
    parent_unit_id TEXT REFERENCES unit(unit_id),
    kind           TEXT NOT NULL,               -- part|section|subsection|chapter|article|point|subpoint|paragraph
    number         TEXT,
    label          TEXT NOT NULL,               -- «подпункт 1 пункта 3 статьи 164 НК РФ»
    title          TEXT,
    duplicate_of   TEXT REFERENCES unit(unit_id), -- заполнено, если источник содержал повторный маркер
    context        TEXT,                        -- «НК РФ, часть 1, глава 14 «…», статья 88 «…»» — контекст чанка
    is_chunk       BOOLEAN NOT NULL DEFAULT false, -- единица поиска: пункт/подпункт, статья без пунктов
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- миграция для баз, созданных до появления колонок
ALTER TABLE unit ADD COLUMN IF NOT EXISTS context TEXT;
ALTER TABLE unit ADD COLUMN IF NOT EXISTS is_chunk BOOLEAN NOT NULL DEFAULT false;
-- заголовочный контекст участвует в поиске (вес B, см. search_units)
ALTER TABLE unit ADD COLUMN IF NOT EXISTS context_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('russian', coalesce(context, '') || ' ' || coalesce(title, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_unit_act ON unit(act_id);
CREATE INDEX IF NOT EXISTS idx_unit_parent ON unit(parent_unit_id);
CREATE INDEX IF NOT EXISTS idx_unit_kind ON unit(kind);

-- главная таблица: текст единицы в конкретном интервале действия.
-- Правки не переписывают строки, а добавляют новые с новым интервалом.
CREATE TABLE IF NOT EXISTS unit_text (
    id         BIGSERIAL PRIMARY KEY,
    unit_id    TEXT NOT NULL REFERENCES unit(unit_id),
    edition_id INT REFERENCES edition(edition_id),
    valid_from DATE,
    valid_to   DATE,
    text       TEXT NOT NULL,                   -- собственный текст единицы (доказательный)
    full_text  TEXT,                            -- текст с вложенными пунктами/подпунктами (как читает юрист)
    text_hash  TEXT NOT NULL,
    edit_note  TEXT,                            -- «(в ред. Федерального закона от ...)»
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_unit_text_unit ON unit_text(unit_id);

-- миграция: до появления full_text поисковый вектор строился по text
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'unit_text' AND column_name = 'full_text') THEN
        ALTER TABLE unit_text ADD COLUMN full_text TEXT;
        ALTER TABLE unit_text DROP COLUMN IF EXISTS search_vector;
    END IF;
END $$;

-- слой 4 плана: полнотекстовый поиск (ts_rank) по полному тексту единицы.
-- generated column пересчитывается сам при перезагрузке корпуса.
ALTER TABLE unit_text ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('russian', coalesce(full_text, text))) STORED;
CREATE INDEX IF NOT EXISTS idx_unit_text_search ON unit_text USING GIN (search_vector);

-- рёбра графа ссылок; to_unit_id/status заполняются резолвером (resolver.py)
CREATE TABLE IF NOT EXISTS reference (
    reference_id BIGSERIAL PRIMARY KEY,
    from_unit_id TEXT NOT NULL REFERENCES unit(unit_id),
    kind         TEXT NOT NULL,                 -- internal_citation | external_federal_law | external_gov_decree | external_agency_act
    raw_citation TEXT NOT NULL,                 -- точный фрагмент текста
    target       JSONB NOT NULL,                -- {"type":"unit","article":"164","point":"3"} | {"type":"act","law_date":"30.11.2016","law_number":"401-ФЗ"}
    to_unit_id   TEXT REFERENCES unit(unit_id),
    status       TEXT,                          -- resolved | partial | unresolved | external
    resolved_depth TEXT,                        -- unit | article | point | subpoint | paragraph | chapter
    extracted_by TEXT NOT NULL DEFAULT 'regex', -- regex | llm | manual
    confidence   REAL NOT NULL DEFAULT 1.0
);

CREATE INDEX IF NOT EXISTS idx_reference_from ON reference(from_unit_id);
CREATE INDEX IF NOT EXISTS idx_reference_to ON reference(to_unit_id);
CREATE INDEX IF NOT EXISTS idx_reference_status ON reference(status);

-- атомарные правки из пометок редакции: история изменений единицы законами
CREATE TABLE IF NOT EXISTS amendment (
    amendment_id        BIGSERIAL PRIMARY KEY,
    target_unit_id      TEXT NOT NULL REFERENCES unit(unit_id),
    operation           TEXT NOT NULL,          -- replace | insert | repeal | title_change | modify
    amending_act_number TEXT NOT NULL,          -- «137-ФЗ»
    amending_act_date   DATE,                   -- дата закона из пометки
    effective_date      DATE,                   -- «с 1 января 2023 г.» — дата вступления правки в силу
    raw_note            TEXT NOT NULL,          -- исходная пометка банка
    status              TEXT NOT NULL DEFAULT 'auto_extracted', -- auto_extracted | manual | verified
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- миграция для баз, созданных до появления колонки
ALTER TABLE amendment ADD COLUMN IF NOT EXISTS effective_date DATE;

CREATE INDEX IF NOT EXISTS idx_amendment_target ON amendment(target_unit_id);
CREATE INDEX IF NOT EXISTS idx_amendment_law ON amendment(amending_act_number, amending_act_date);

-- сырые документы (принцип происхождения: URL, дата, хеш)
CREATE TABLE IF NOT EXISTS raw_document (
    raw_id       BIGSERIAL PRIMARY KEY,
    url          TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    sha256       TEXT NOT NULL,
    format       TEXT NOT NULL,
    path         TEXT NOT NULL
);

-- журнал прогонов парсера: статистика и issue-список валидации
CREATE TABLE IF NOT EXISTS parse_run (
    run_id     BIGSERIAL PRIMARY KEY,
    act_code   TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stats      JSONB NOT NULL,
    issues     JSONB NOT NULL
);
