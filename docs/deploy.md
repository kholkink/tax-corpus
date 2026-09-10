# Развёртывание (P7)

## Одной командой

```bash
cp .env.example .env            # ключ провайдера, пароль БД
docker compose up -d db         # PostgreSQL 16 + pgvector
docker compose run --rm app python -m taxcorpus ingest --act nk1 --act nk2   # корпус в БД (см. README)
docker compose run --rm app python -m taxcorpus load-docs
docker compose run --rm app python -m taxcorpus embed --to-db                  # e5-индекс -> pgvector
docker compose up -d            # api (127.0.0.1:8000) + scheduler
```

- `app` — FastAPI (`/` — UI, `/docs` — схема). Дела хранятся в `./workspaces` на хосте.
- `scheduler` — `scripts/scheduler.sh`: daily 03:00 (краулеры → БД → снимок), бэкап 03:30,
  по понедельникам — оценка агента, поиска и карта точности, по воскресеньям — извлечение позиций.
- Модели (e5-small, реранкер) кэшируются в volume `hf`.

## Бэкапы

`scripts/backup.sh [каталог]` — `pg_dump` (custom) + tar дел и реестров + sha256, хранится 14
последних. `scripts/restore.sh backups/db-<stamp>.dump [backups/files-<stamp>.tgz]` — проверка
контрольных сумм, `pg_restore --clean`, распаковка файлов. Каталог `./backups` смонтирован в `db`
и `scheduler`.

## Реранкер и GPU

`TAXCORPUS_RERANK=1` включает кросс-энкодер `BAAI/bge-reranker-v2-m3` над top-30 гибрида
(`TAXCORPUS_RERANK_DEPTH`). На CPU это 3–10 с на запрос — включать на GPU-хосте
(`docker compose` с `deploy.resources.reservations.devices` для nvidia) или только для оценки:
`python scripts/eval_search.py --rerank 1`.

## Обновление

```bash
git pull && docker compose build && docker compose up -d
docker compose run --rm app python -c "from taxcorpus.db import connect, ensure_schema; print(ensure_schema(connect()))"
```
Миграции `sql/migrations/NNN_*.sql` применяются один раз (`schema_version`).
