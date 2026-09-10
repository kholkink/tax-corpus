-- F13 плана ПО: эмбеддинги чанков в БД (pgvector). Расширение может отсутствовать
-- (встроенный Postgres разработчика) — тогда таблица не создаётся, поиск идёт по npz-индексу.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector недоступен: unit_embedding не создана (поиск по data/index/*.npz)';
        RETURN;
    END;
    EXECUTE '
        CREATE TABLE IF NOT EXISTS unit_embedding (
            unit_id  TEXT NOT NULL REFERENCES unit(unit_id) ON DELETE CASCADE,
            model    TEXT NOT NULL,
            dim      INT  NOT NULL,
            vec      vector NOT NULL,
            built_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (unit_id, model)
        )';
END $$;
