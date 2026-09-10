# tax-corpus — структурный парсер Налогового кодекса РФ

Слой 1–2 архитектуры «Налогового ИИ-агента для юристов» (см.
`docs/architecture-plan.md`): инжест официального текста НК РФ из
первоисточника, разбор в иерархию структурных единиц с каноническими
идентификаторами, валидация, извлечение явных ссылок и загрузка в PostgreSQL.

## Состав

```
src/taxcorpus/
  models.py     — Unit, канонические ID, ярлыки («подпункт 1 пункта 3 статьи 164 НК РФ»)
  normalize.py  — нормализация сырого текста, разбиение на абзацы
  ingest.py     — конвертер HTML банка ГАС «Законодательство России» в текст
  parser.py     — структурный парсер: часть/раздел/глава/статья/пункт/подпункт/абзац
  validator.py  — валидатор иерархии + markdown/JSON-отчёт
  references.py — извлечение явных ссылок (регэкспы)
  resolver.py   — резолв ссылок в канонические unit_id (точный номер > вариант банка;
                  контекстные «пункт 5» — относительно статьи-источника)
  amendments.py — атомарные правки из пометок редакции (словесные даты, латинская «N»)
  db.py         — загрузка в PostgreSQL (idempotent), get_unit / search_units /
                  get_parameter / find_terms (инструменты слоя 5)
  terms.py      — термины: ст. 11 и отраслевые словари («в целях настоящей главы …
                  понятия:») с областью действия -> таблица term
  deadlines.py  — compute_deadline по ст. 6.1 (п. 2–8), производственный календарь
  calculators.py — пени (ст. 75 + ключевая ставка ЦБ), штрафы (ст. 112/114/119/122/126), сроки
                  обжалования, срок давности — каждый шаг со ссылкой на пункт
  citations.py  — проверка цитат в тексте ответа: резолв + действие на дату (OK/STALE/MISS/PART)
  tools.py      — инструменты агента (слой 5) с двумя бэкендами: DbCorpus (PostgreSQL) и
                  LocalCorpus (JSONL, офлайн); описания для function calling
  agent.py      — тонкий агент над function calling (ручной цикл, без фреймворков):
                  системный промпт «цитируй или откажись», проверка цитат, переработка, журнал
  evaluation.py — метрики §6 плана по ответам агента; прогон — scripts/eval_agent.py
  interpretations.py — реестр писем/пленумов, привязка interprets, get_interpretations,
                  проверка цитат на документы
  embeddings.py — DenseIndex (e5-small, npz-матрица), rrf(); tools.HybridSearch — слияние
  textract.py   — текст из docx/pdf/xlsx/md/html; workspace.py — дело, файлы, версии, задачи,
                  инструменты дела; case_session.py — персистентная сессия агента в деле (ask_user)
  audit.py      — аудит документа (F1); workspace_store.py — зеркало метаданных дел в БД (P1)
  facts.py      — факты и таймлайн дела с дословными цитатами, сроки из фактов (F6);
  providers.py  — профили провайдера модели (P4); redact.py — маскировка ПДн для облака (F9)
  positions.py  — карта позиций по норме (F4); templates.py / export.py — шаблоны и DOCX (F7)
  rerank.py     — кросс-энкодер над гибридом (F13); auth.py — пользователи, токены, роли (P5)
  collab.py     — комментарии к файлам агента и журнал активности (F8)
  jobs.py       — задачи и расписание (P3): краулеры, load_docs с событиями, check_bank_editions,
                  embed, snapshot, eval_search, eval_agent, daily; журнал job_run
  api.py        — FastAPI: /units, /search, /resolve, /parameters, /terms, /interpretations,
                  /deadline, /ask
  cli.py        — CLI: convert / parse / load / ingest / unit / search / diff / param / term / deadline / ask / load-docs / interpretations / embed / workspace / audit / jobs / calc
sql/schema.sql  — базовая схема (идемпотентна): Act / Edition / Unit / UnitText / Reference /
                  Amendment / Parameter / Term / Document / Snapshot; sql/migrations/NNN_*.sql —
                  нумерованные миграции, применяются один раз (schema_version); 001 — метаданные
                  дел (workspace, workspace_file, session, session_question, audit, fact, job_run, corpus_event)
tests/          — pytest: идеализированный формат, формат банка ГАС, валидатор, нормализация
scripts/setup_postgres.sh — локальный PostgreSQL 16 без прав администратора
```

## Формат источников

Первичный источник: банк ГАС «Законодательство России» (`pravo-search.minjust.ru/bigs/`)
— официально поддерживаемый эталонный банк НПА Минюста с консолидированными
текстами и списком редакций. Отчёт об источниках: `data/sources-report.md`.

Особенности подачи банка, которые парсер обрабатывает:

| Особенность | Пример | Решение |
|---|---|---|
| Сплющенные дробные номера | «Статья 61.» = ст. 6.1, «Глава 34» = гл. 3.4 (КИК), «Статья 2513» = ст. 25.13 | детокенизация по контексту (префикс предыдущей единицы) |
| Пометки в угловых скобках | «\<В новой ред. ФЗ от …\>», «\<Введена …\>», «\<Изменения: …\>» | уходят в `edit_note`, не в текст нормы |
| Заголовки, разорванные на абзацы | «Глава 2. …» + «ФЕДЕРАЦИИ» | склейка соседних центрированных абзацев |
| NBSP внутри номеров | «Раздел V. 1.» | нормализация номера |
| Повторные маркеры | ст. 88: два «11.» | суффикс «@2» + `duplicate_of`, очередь ручной сверки |

## Канонические идентификаторы

`<акт>.<глава|раздел>.art<N>.p<N>.sp<N>.ab<N>`, дробные и дефисные номера —
через `-`: `nk1.ch2.art54-1`, `nk1.ch14-4.art105-16-2`, `nk1.ch1.art6-1.p1.ab2`.
ID не меняются при перенумерации соседей. Уникальность гарантируется суффиксами.

## Быстрый старт

```bash
# Windows:
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev,agent,api]"
bash scripts/setup_postgres.sh            # локальный Postgres 16 (user-space), база taxcorpus
# Linux / WSL (без python3-venv помогает uv):
uv venv .venv-linux --python 3.12 && uv pip install --python .venv-linux/bin/python -e ".[dev,agent,api]"
bash scripts/setup_postgres.sh 5433       # порт 5432 в WSL обычно занят Windows-инстансом
export TAXCORPUS_DB=postgresql://postgres@127.0.0.1:5433/taxcorpus   # его читают CLI, тесты, скрипты, API

# 1. HTML банка -> нормализованный текст
python -m taxcorpus convert --input data/raw/sources/minjust_..._red199.html \
    --output data/processed/nk1_red199.txt

# 2. Парсинг + валидация + JSONL
python -m taxcorpus parse --input data/processed/nk1_red199.txt --act-code nk1 \
    --source-url "https://pravo-search.minjust.ru/bigs/" --valid-from 2026-08-04
# --valid-from — дата редакции из шапки банка; без неё интервал действия начнётся
# с сегодняшнего дня и get_unit на более ранние даты вернёт пустоту (будет предупреждение)

# 3. Загрузка в PostgreSQL
python -m taxcorpus load --data-dir data/processed --act-code nk1

# шаги 2–3 одной командой:
python -m taxcorpus ingest --input data/processed/nk1_red199.txt --act-code nk1 --valid-from 2026-08-04

# норма на дату (с историей правок из пометок редакции):
python -m taxcorpus unit --id nk1.ch14.art88.p1 --as-of 2026-09-09

# семантический индекс чанков (слой 4): intfloat/multilingual-e5-small на CPU, ~10 тыс. чанков
# за час, файл data/index/<model>.npz (не в git); гибрид = лексика + dense через RRF.
# На эталоне: лексика unit@5 17/23, dense 17/23, гибрид 21/23 (article@5 23/23):
pip install -e ".[semantic]" && python -m taxcorpus embed
python -m taxcorpus search --query "ставка налога на прибыль" --hybrid
# полнотекстовый поиск по нормам (ts_rank_cd PostgreSQL: строгий проход по всем словам,
# добор по «ИЛИ» лемм; на эталоне unit@5 = 17/23; русская морфология, фильтр по дате,
# аббревиатуры расширяются полными формами: НДС → «налог на добавленную стоимость»):
python -m taxcorpus search --query "камеральная налоговая проверка" --kind point --limit 10

# история правок единицы за период:
python -m taxcorpus diff --id nk1.ch1.art6-1 --since 2000-01-01

# ставка/срок/лимит на дату с текстом-доказательством (таблица parameter, сид
# data/parameters/parameters_v0.json — каждое значение подтверждено якорем в тексте):
python -m taxcorpus param --name vat_rate_general --as-of 2026-09-10

# определение термина (ст. 11 НК и отраслевые словари с областью действия):
python -m taxcorpus term --term "индивидуальн"

# срок по ст. 6.1 НК: каждая операция расчёта — со ссылкой на пункт статьи
# (дни — рабочие, --calendar-days для календарных; переносы выходных — data/calendar/<год>.json):
python -m taxcorpus deadline --start 2025-03-20 --amount 3 --unit months

# провайдер модели (P4): профили в config/providers.json (в .gitignore; образец —
# config/providers.example.json; ключи только через имена переменных окружения) или один
# профиль из .env: ANTHROPIC_API_KEY, при необходимости ANTHROPIC_BASE_URL
# (DeepSeek: https://api.deepseek.com/anthropic) и TAXCORPUS_MODEL (deepseek-chat);
# без base_url по умолчанию claude-opus-5 с серверным фолбэком при отказе.
# Выбор: --provider ИМЯ > профиль дела (workspace new --provider) > TAXCORPUS_PROVIDER > default.
# Конфиденциальность дела (F9): `workspace new --confidentiality sensitive` — такое дело идёт
# только в профиль location=local; без него запуск отклоняется, а с TAXCORPUS_ALLOW_CLOUD_SENSITIVE=1
# уходит в облако с маскировкой ПДн (ИНН, КПП, ОГРН, СНИЛС, паспорт, счета, телефоны, e-mail, ФИО ->
# [ИНН-1] и т.п.; словарь — workspaces/<slug>/redaction_map.json, ответ демаскируется).
# Бейдж «куда уходят данные» — в /health, в карточке дела и в UI.
# агент (слой 6): планирование -> инструменты корпуса -> синтез в фиксированном формате ->
# детерминированная проверка каждой цитаты на существование и действие на дату ->
# при проблемах один круг переработки. Нужны `pip install -e ".[agent]"` и ключ
# (ANTHROPIC_API_KEY или `ant auth login`); модель по умолчанию claude-opus-5:
python -m taxcorpus ask --question "Сколько длится камеральная проверка?" --as-of 2026-09-10 --log reports/ask.json
python -m taxcorpus ask --question "…" --local   # офлайн-корпус из JSONL без БД (поиск грубый)

# разъяснения и практика (слой 3, ребро interprets): реестр JSONL в data/interpretations,
# ссылки на нормы извлекаются и резолвятся тем же кодом, что и внутри кодекса;
# агент цитирует только письма из реестра (проверка цитат ловит чужие):
python -m taxcorpus interpretations --id nk1.ch14.art88.p2 --as-of 2026-09-10
python -m taxcorpus load-docs --input data/interpretations   # -> таблицы document / doc_reference

# краулер писем ФНС, обязательных для налоговых органов (nalog.gov.ru, первоисточник,
# 1 запрос / 2 с, повторный запуск дописывает): статус актуальности, теги по статьям НК:
python scripts/fetch_fns_letters.py --pages 114          # весь раздел: 1690 писем выгружено
python scripts/fetch_minfin_letters.py                   # письма Минфина по 11 категориям раздела
python scripts/fetch_court_acts.py                       # пленумы ВАС № 57 и № 53 с arbitr.ru (список в SEED)
# итог реестра: 1690 писем ФНС + 222 письма Минфина + 2 пленума = 1914 документов, 8711 рёбер interprets
python -m taxcorpus snapshot --description "…"           # снимок корпуса: счётчики, хеши, коммит (§7)

# HTTP API (слой 7) и веб-клиент рабочего пространства: дела, файлы с просмотром, чат сессии
# с карточкой вопроса агента, кликабельные источники (норма в нужной редакции), задачи
pip install -e ".[api]" && uvicorn taxcorpus.api:app --reload   # http://127.0.0.1:8000/ — UI, /docs — OpenAPI

# рабочее пространство дела (docs/workspace-plan.md): папка с файлами юриста (inbox/, notes/)
# и агента (research/, drafts/ с версиями и шапкой провенанса), сессии с паузой на вопрос
# юристу (ask_user), задачи; пример — workspaces/demo:
python -m taxcorpus workspace new --slug delo1 --title "Проверка ООО …" --client "ООО …" --as-of 2026-09-10
python -m taxcorpus workspace add --slug delo1 акт.docx требование.pdf     # -> inbox/
python -m taxcorpus workspace chat --slug delo1                              # REPL: /files /tasks /quit
python -m taxcorpus workspace chat --slug delo1 --message "Подготовь позицию по notes/задача.md"
python -m taxcorpus workspace chat --slug delo1 --session <id> --message "ответ на вопрос агента"

# факты и таймлайн дела (F6): агент извлекает факты из документов только с ДОСЛОВНОЙ цитатой из
# файла (иначе факт отклоняется), даты/суммы/периоды нормализуются, юрист подтверждает; роли дат
# (act_received, decision_received, decision_date, period_end …) дают сроки процедуры и давность
# через калькуляторы F5 и ставят задачи. Вкладка «Факты и таймлайн» в UI, API /workspaces/{slug}/facts:
python -m taxcorpus workspace facts --slug delo1                 # таблица фактов (? — не подтверждён)
python -m taxcorpus workspace add-fact --slug delo1 --kind event --value 27.03.2026 --text "акт вручён" --role act_received
python -m taxcorpus workspace confirm-fact --slug delo1 --id 2
python -m taxcorpus workspace timeline --slug delo1
python -m taxcorpus workspace deadlines --slug delo1             # сроки со ссылками на нормы -> задачи

# реранкер и pgvector (F13): TAXCORPUS_RERANK=1 включает кросс-энкодер BAAI/bge-reranker-v2-m3
# над top-30 гибрида (на CPU 3–5 с на запрос — для GPU/прода или оценки: scripts/eval_search.py --rerank 1);
# python -m taxcorpus embed --to-db кладёт e5-векторы в unit_embedding (pgvector, миграция 006),
# DbCorpus сам переключается на pgvector, если таблица заполнена; без pgvector — npz-индекс.

# развёртывание и доступ (P7, P5): docker compose up -d (pgvector/pgvector:pg16 + api + scheduler),
# бэкапы scripts/backup.sh / restore.sh — см. docs/deploy.md. Пользователи и роли в делах
# (viewer / editor / owner, TAXCORPUS_AUTH=on, токены Bearer tc_…, реестр config/users.json):
python -m taxcorpus users add --email anna@firm.ru --name "Анна" && python -m taxcorpus users token --email anna@firm.ru
python -m taxcorpus users grant --slug delo1 --email anna@firm.ru --role editor

# совместная работа (F8): комментарии к файлам агента (comments.json дела: файл, версия, абзац-якорь,
# ветки ответов) и журнал активности (activity.jsonl: user:<email> | agent, действие, цель);
# агент читает открытые замечания list_comments перед перезаписью файла и отвечает reply_comment,
# закрывает ветку юрист. API /workspaces/{slug}/comments, …/comments/{id}/resolve, …/activity; панель в UI.

# шаблоны документов и DOCX (F7): templates/*.md (возражения на акт, апелляционная жалоба, ответ на
# требование, меморандум) с плейсхолдерами {{facts.<роль>|запасное}}, {{manifest.client}}, {{deadlines.<ключ>}}
# и секциями <!-- agent: … -->; инструмент агента draft_document; экспорт md -> DOCX (python-docx,
# провенанс — в свойствах и на последней странице; --reference docx со стилями фирмы):
python -m taxcorpus workspace draft --slug delo1 --template возражения-на-акт --path возражения --set "authority=ИФНС № 1"
python -m taxcorpus workspace export --slug delo1 --path drafts/возражения.md --out возражения.docx
# в API: GET /templates, POST /workspaces/{slug}/drafts, GET /workspaces/{slug}/files/{path}?format=docx

# карта позиций по норме (F4): позиции писем/пленумов/обзоров по каждой норме извлекает модель
# (stance pro_taxpayer | pro_authority | neutral + дословная цитата, проверяемая по тексту документа;
# без точной цитаты позиция отклоняется), реестр — data/interpretations/positions.jsonl, зеркало — таблица
# position; инструмент агента get_position_map (conflict = позиции расходятся), API /units/{id}/positions,
# вкладка «Позиции» в карточке нормы. Краулер судебных актов — по списку первоисточников
# (scripts/fetch_court_acts.py): vsrf.ru — JS-приложение без открытого списка, ksrf.ru отдаёт 403 роботам.
python -m taxcorpus jobs run extract_positions -- --limit 100 --kinds plenum,review   # платно, инкрементально
python -m taxcorpus jobs run load_docs                                                # позиции -> БД

# объяснимость (F12): каждый результат поиска несёт sources (lexical/dense), matched_terms и why
# («все слова запроса; близко по смыслу — совпали: …»); карточка нормы GET /units/{id}/card —
# текст на дату, лента правок, письма и практика (с пометкой обязательных), версии текста,
# заякоренные параметры; в UI — поле «Нормы» справа и клик по любой цитате в ответе агента.

# карта точности (F11): эталон tests/golden/golden_v0.json (v1-draft, 75 вопросов, 14 тем; каждый
# ожидаемый ID сверен с текстом корпуса по якорю; протокол сбора v1 с юристами — docs/golden-v1-protocol.md):
python scripts/eval_search.py                          # unit@5/article@5 по темам -> reports/eval_search.json
python scripts/eval_agent.py --repeat 3 --topic НДС     # прогон агента (платно), повторы = разброс модели
python scripts/eval_agent.py --accuracy                # reports/accuracy.{json,md}; в API — GET /accuracy
python -m taxcorpus jobs run accuracy                  # то же по расписанию

# аудит документа (F1 плана ПО): свой или чужой меморандум -> по каждой ссылке статус на дату,
# правки после даты документа, снятые письма, не упомянутые обязательные письма ФНС;
# в UI — вкладка «Аудит документа», в деле — инструмент audit_document, API POST /audit
python -m taxcorpus audit --file меморандум.docx --as-of 2026-09-10 --doc-date 2024-01-01

# задачи и расписание (P3 плана ПО): журнал запусков в job_run, события корпуса в corpus_event
python -m taxcorpus jobs list
python -m taxcorpus jobs run daily            # краулеры -> load_docs -> check_bank_editions -> snapshot
python -m taxcorpus jobs run crawl_fns -- --pages 114
python -m taxcorpus jobs history --limit 20
python -m taxcorpus jobs cron                 # строки для crontab (03:00 daily, пн 04:00 eval_agent)

# калькуляторы с цитатами (F5 плана ПО): пени по ст. 75 с ключевой ставкой ЦБ по дням
# (правила 1/300 и 1/150 по периодам, п. 4/5/5.1), штрафы ст. 119/122/126 со смягчающими и
# отягчающими (ст. 112, 114), сроки обжалования (ст. 100, 101, 139, 139.1), срок давности (ст. 113);
# инструменты агента, POST /calc/{name}, ключевая ставка — data/parameters/key_rate.json (задача crawl_key_rate)
python -m taxcorpus calc compute_penalty amount=1200000 due_date=2026-01-28 paid_date=2026-06-15 taxpayer=organization
python -m taxcorpus calc compute_fine article=119 base=200000 due_date=2025-04-25 actual_date=2025-07-01 documents=0 'mitigating=["тяжёлое положение"]' aggravating=false reduction_factor=2

# оценка агента на эталоне (метрики §6 плана: citation precision/recall, hallucination
# rate, temporal correctness, abstention; каждый вопрос — платный запрос к модели):
python scripts/eval_agent.py --limit 5 [--local]   # -> reports/eval_agent.{json,md}

# отчёт по частично разрешённым ссылкам (очередь сверки):
python scripts/report_partial.py
```

Результаты: `data/processed/nk1_units.jsonl`, `nk1_references.jsonl`,
`nk1_amendments.jsonl`, `nk1_meta.json`, отчёт `reports/nk1_validation.md` (+ `.json`).

## Визуализация графа

```bash
python scripts/build_graph.py       # данные + матрица глава→глава + топ цитируемых
python scripts/render_graph_html.py # автономный интерактивный reports/nk1_graph.html
```

`reports/nk1_graph.html` — self-contained (библиотека вшита, работает офлайн):
граф **обеих частей** (814 статей, 1955 межстатьевых связей), поиск, фильтры,
иерархия глав, панорама/зум. Плюс статичные `reports/nk1_chapter_matrix.png`
и `reports/nk1_top_cited.png`.

## Загрузка нового акта из банка ГАС

```bash
python scripts/fetch_act.py --uuid <UUID акта> --out data/raw/sources/<акт>.html --label <код>
python -m taxcorpus convert --input data/raw/sources/<акт>.html --output data/processed/<код>.txt
python -m taxcorpus ingest --input data/processed/<код>.txt --act-code <код> \
    --act-title "..." --official-number "...-ФЗ" --adoption-date YYYY-MM-DD \
    --source-url "https://pravo-search.minjust.ru/bigs/"
```
UUID кодексов — в `data/raw/minjust_res_codes_v2.json` (каталог «Кодексы РФ»).

## Состояние данных (обе части НК РФ, редакции от 04.08.2026)

| Акт | Источник | Единиц | Ссылок | Правок |
|---|---|---|---|---|
| nk1 — ч.1 (146-ФЗ, ред. №199) | ГАС Минюста | 7 329 | 4 174 (3 816 resolved, 6 unresolved) | 2 577 (71 repeal) |
| nk2 — ч.2 (117-ФЗ, ред. №376) | ГАС Минюста | 24 089 | 18 601 (17 165 resolved, 26 unresolved) | 8 986 (428 repeal) |
| **итого** | | **31 418** | **22 775** | **11 563** |

Резолвер работает по индексу всего корпуса (ссылки ч.1 на главы/статьи ч.2 резолвятся,
если в `data/processed` уже лежит `nk2_units.jsonl`), поэтому части стоит парсить
последовательно, а первую — после второй. Ссылки вида «статьи 10 Федерального закона …»
и «статьи 395 Гражданского кодекса» хранятся как `external_act_unit` (confidence 0.6),
а не как ссылки на НК. Контекстные ссылки («абзаце первом настоящего пункта»,
«подпункте 2 настоящего пункта») резолвятся относительно предка источника нужного
вида (`target.relative_to`); без явного «настоящего …» пункт ищется в статье-источнике,
подпункт и абзац — в пункте-источнике. Диапазоны и перечни («подпунктами 1 - 3 и 7», «абзацах втором - четвертом»)
раскрываются в отдельные ссылки. «Абзац N пункта» считается по юридической технике:
строки подпунктов «1)…» — тоже абзацы пункта (в `resolution_note` пишется, какой подпункт).

Особенности: гл. 26.1 (ЕСХН) в подаче ч.2 отсутствует как заголовок, её статьи
346.1–346.10 висят прямо под разделом VIII.1 (ID вида `nk2.rviii-1.art346-4`);
дефекты подсвечены валидатором (`reports/nk2_validation.md` — очередь сверки).
Пункты, поданные маркером «N)» прямо под статьёй (ст. 217, 270 и др.), парсер
считает пунктами, как их и цитируют юристы: `nk2.ch23.art217.p18-1` = п. 18.1 ст. 217.
Если после таких «N)» идёт пункт «2.» с точкой (ст. 150: банк потерял маркер «1.»),
парсер синтезирует п. 1 из вводных абзацев статьи (`inferred: true`), а «N)» делает
его подпунктами. Строки таблиц координат «1. 61 53 00; …» (ст. 333.45) пунктами не
считаются. Валидация ч.2: 12 ошибок, все — повторные маркеры источника («@2»).

## Известные ограничения (текущая редакция №199 от 04.08.2026)

- 4 ошибки валидации на 7364 единицы — повторные маркеры источника (ст. 88:
  два «11.», ст. 105.28: два подпункта «3)» и т.п.): парсер сохранил их с
  суффиксом «@2» и пометкой `duplicate_of`; полный список в
  `reports/nk1_validation.md` — это очередь на ручную сверку с официальной
  нумерацией.
- 330 сплющенных дробных номеров восстановлены контекстно (ст. 6.1, гл. 3.4
  КИК, п. 1.1, подп. 3.1 и т.д.); при спорных случаях см. `parse_run.stats`.
- Отменённые единицы. Пометка «<Утратил силу с 1 января 2023 г.: …>» уходит в
  `edit_note` (не в текст нормы), даёт правку `repeal` с `effective_date`, а при
  загрузке — `unit_text.valid_to` = дата утраты силы (начало интервала NULL), так что
  `get_unit(as_of)` после этой даты её не выдаёт. Пометки о части единицы
  («<Абзац пятый утратил силу …>») имеют `scope = child` и родителя не закрывают.
  У части repeal-пометок даты вступления нет (nk1: 10 из 65) — интервал закрывается
  датой редакции.
- Несколько пометок у одной единицы накапливаются в `edit_note` через перевод строки.
  Пометки об абзацах («<Абзац введен …>», «<Абзац пятый утратил силу …>») переезжают
  на сами единицы-абзацы: без порядкового — на абзац, после которого стоят, с порядковым —
  на указанный собственный абзац; порядковый за пределами собственных абзацев (строки
  подпунктов в юридическом счёте) остаётся на пункте как `scope: child`.
- Абзац пункта, идущий после списка подпунктов, грамматически неотличим от абзаца
  последнего подпункта и приписывается ему (260 непоследних подпунктов тоже имеют
  абзацы с заглавной буквы, эвристика ненадёжна) — известное ограничение.
- У каждой единицы два текста: `text` — только собственные абзацы (доказательный),
  `full_text` — вместе с вложенными пунктами/подпунктами (то, что цитирует юрист).
  Поиск и `get_unit` работают по `full_text`; поле `context` хранит цепочку заголовков
  («НК РФ, часть 1, глава 14 «…», статья 88 «…»»), `is_chunk` отмечает единицы поиска
  (пункт/подпункт, статья без пунктов) — по умолчанию `search` ищет только по ним
  (`--all-kinds` снимает фильтр).
- Эталон v0 (`tests/golden/golden_v0.json`, 33 вопроса) сверен по тексту корпуса
  тестом `tests/test_golden_corpus.py`; прогон против БД с метриками —
  `python scripts/build_golden.py`.
- Исторические тексты редакций банком через открытый API не отдаются (эндпоинт
  карточки сломан: `shardsWhitelist`) — задел: применитель патчей на списке
  изменяющих законов (шапка текста содержит их полный перечень с НГР-номерами).

## Запуск тестов

```bash
.venv/Scripts/python -m pytest -q
```
