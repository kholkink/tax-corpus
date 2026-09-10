"""Загрузка корпуса в PostgreSQL.

Идемпотентность: перезагрузка одного акта целиком (units + texts + references
акта стираются и вставляются заново в одной транзакции). Тексты единиц не
редактируются на месте — при консолидации редакций появляются новые строки
unit_text с новыми интервалами (см. архитектурный план, §4.2).
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .amendments import repeal_dates

DEFAULT_DB_URL = "postgresql://postgres@localhost:5432/taxcorpus"


def connect(db_url: str | None = None) -> psycopg.Connection:
    url = db_url or os.environ.get("TAXCORPUS_DB") or DEFAULT_DB_URL
    return psycopg.connect(url, row_factory=dict_row)


def ensure_schema(conn: psycopg.Connection, schema_path: str | Path | None = None) -> None:
    path = Path(schema_path) if schema_path else \
        Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
    with conn.transaction():
        conn.execute(path.read_text(encoding="utf-8"))


def load_corpus(conn: psycopg.Connection, meta: dict, units: list[dict],
                references: list[dict], stats: dict | None = None,
                issues: list[dict] | None = None,
                amendments: list[dict] | None = None) -> dict:
    """Полная (пере)загрузка одного акта. meta — из CLI; units/references/amendments — записи парсера."""
    act = meta["act"]
    act_code = act["code"]
    valid_from = date.fromisoformat(meta.get("valid_from")) if meta.get("valid_from") else date.today()
    stats = stats or {}
    issues = issues or []
    amendments = amendments or []

    with conn.transaction():
        cur = conn.execute(
            """
            INSERT INTO act (act_code, kind, official_number, adoption_date, title,
                             source_url, retrieved_at, source_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (act_code) DO UPDATE SET
                kind = EXCLUDED.kind,
                official_number = EXCLUDED.official_number,
                adoption_date = EXCLUDED.adoption_date,
                title = EXCLUDED.title,
                source_url = EXCLUDED.source_url,
                retrieved_at = EXCLUDED.retrieved_at,
                source_sha256 = EXCLUDED.source_sha256
            RETURNING act_id
            """,
            (act_code, act.get("kind", "code"), act.get("official_number"),
             act.get("adoption_date"), act["title"], meta.get("source_url"),
             meta.get("retrieved_at"), meta.get("source_sha256")),
        )
        act_id = cur.fetchone()["act_id"]

        if meta.get("source_url"):
            # один и тот же сырой документ (url + sha256) не дублируется при перезагрузке
            conn.execute(
                """
                INSERT INTO raw_document (url, retrieved_at, sha256, format, path)
                SELECT %s, %s, %s, %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM raw_document WHERE url = %s AND sha256 = %s)
                """,
                (meta["source_url"], meta.get("retrieved_at"), meta.get("source_sha256") or "",
                 meta.get("source_format", "txt"), meta.get("source_path", ""),
                 meta["source_url"], meta.get("source_sha256") or ""),
            )

        # полная перезагрузка единиц акта: сначала все зависимые таблицы
        # (reference, amendment, term, parameter, unit_text), затем unit и edition
        for sql in (
            "DELETE FROM reference WHERE from_unit_id IN (SELECT unit_id FROM unit WHERE act_id = %s)",
            "DELETE FROM reference WHERE to_unit_id IN (SELECT unit_id FROM unit WHERE act_id = %s)",
            "DELETE FROM term WHERE definition_unit_id IN (SELECT unit_id FROM unit WHERE act_id = %s)",
            "DELETE FROM parameter WHERE source_unit_id IN (SELECT unit_id FROM unit WHERE act_id = %s)",
        ):
            conn.execute(sql, (act_id,))
        conn.execute(
            """
            DELETE FROM amendment WHERE target_unit_id IN
                (SELECT unit_id FROM unit WHERE act_id = %s)
            """,
            (act_id,),
        )
        conn.execute(
            """
            DELETE FROM unit_text WHERE unit_id IN
                (SELECT unit_id FROM unit WHERE act_id = %s)
            """,
            (act_id,),
        )
        conn.execute("DELETE FROM unit WHERE act_id = %s", (act_id,))
        conn.execute("DELETE FROM edition WHERE act_id = %s", (act_id,))

        cur = conn.execute(
            """
            INSERT INTO edition (act_id, valid_from, valid_to, notes)
            VALUES (%s, %s, NULL, %s)
            RETURNING edition_id
            """,
            (act_id, valid_from, meta.get("edition_notes", "текущая редакция на дату выгрузки")),
        )
        edition_id = cur.fetchone()["edition_id"]

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO unit (unit_id, act_id, parent_unit_id, kind, number,
                                  label, title, duplicate_of, context, is_chunk)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (r["unit_id"], act_id, r["parent_unit_id"], r["kind"],
                     r["number"], r["label"], r["title"], r.get("duplicate_of"),
                     r.get("context"), bool(r.get("is_chunk")))
                    for r in units
                ],
            )

            provenance = {
                "source_url": meta.get("source_url"),
                "retrieved_at": meta.get("retrieved_at"),
                "source_sha256": meta.get("source_sha256"),
            }
            # отменённые единицы («<Утратил силу с 1 января 2023 г.: …>»): интервал
            # действия закрывается датой утраты силы, начало неизвестно (NULL);
            # без даты в пометке — закрыт датой редакции, чтобы не считаться действующей
            repealed = repeal_dates(amendments)

            def interval(unit_id: str) -> tuple[date | None, date | None]:
                if unit_id in repealed:
                    return None, (date.fromisoformat(repealed[unit_id])
                                  if repealed[unit_id] else valid_from)
                return valid_from, None

            cur.executemany(
                """
                INSERT INTO unit_text (unit_id, edition_id, valid_from, valid_to,
                                       text, full_text, text_hash, edit_note, provenance)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (r["unit_id"], edition_id, *interval(r["unit_id"]), r["text"],
                     r.get("full_text") or r["text"], r["text_hash"], r["edit_note"],
                     Json(provenance))
                    for r in units
                ],
            )

            cur.executemany(
                """
                INSERT INTO reference (from_unit_id, kind, raw_citation, target,
                                       to_unit_id, status, resolved_depth,
                                       extracted_by, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (r["from_unit_id"], r["kind"], r["raw_citation"], Json(r["target"]),
                     r.get("to_unit_id"), r.get("status"), r.get("resolved_depth"),
                     r.get("extracted_by", "regex"), r.get("confidence", 1.0))
                    for r in references
                ],
            )

            if amendments:
                cur.executemany(
                    """
                    INSERT INTO amendment (target_unit_id, operation,
                                           amending_act_number, amending_act_date,
                                           effective_date, raw_note, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (a["target_unit_id"], a["operation"], a["amending_act_number"],
                         a["amending_act_date"], a.get("effective_date"), a["raw_note"],
                         a.get("status", "auto_extracted"))
                        for a in amendments
                    ],
                )

        conn.execute(
            "INSERT INTO parse_run (act_code, stats, issues) VALUES (%s, %s, %s)",
            (act_code, Json(stats), Json(issues)),
        )

    return {"act_id": act_id, "edition_id": edition_id, "units": len(units),
            "references": len(references), "amendments": len(amendments)}


def get_unit(conn, unit_id: str, as_of_date: str) -> dict | None:
    """Норма на дату: текст единицы с учётом интервала действия редакции.

    Интервал проверяется по valid_from/valid_to текстовой строки; при наличии
    нескольких интервалов выбирается действующий с последней valid_from.
    """
    row = conn.execute(
        """
        SELECT u.unit_id, u.kind, u.number, u.label, u.title, u.duplicate_of,
               u.context, u.is_chunk,
               t.valid_from, t.valid_to, t.text, t.full_text, t.text_hash, t.edit_note,
               e.edition_id
        FROM unit u
        JOIN unit_text t ON t.unit_id = u.unit_id
        LEFT JOIN edition e ON e.edition_id = t.edition_id
        WHERE u.unit_id = %s
          AND (t.valid_from IS NULL OR t.valid_from <= %s)
          AND (t.valid_to IS NULL OR t.valid_to > %s)
        ORDER BY t.valid_from DESC NULLS LAST
        LIMIT 1
        """,
        (unit_id, as_of_date, as_of_date),
    ).fetchone()
    return row


def list_amendments(conn, unit_id: str, since: str | None = None) -> list[dict]:
    """История правок единицы (из пометок редакции); since — фильтр по дате закона."""
    return conn.execute(
        """
        SELECT operation, amending_act_number, amending_act_date, effective_date, raw_note
        FROM amendment
        WHERE target_unit_id = %s
          AND (%s::date IS NULL OR amending_act_date >= %s::date)
        ORDER BY amending_act_date NULLS LAST, amendment_id
        """,
        (unit_id, since, since),
    ).fetchall()


# аббревиатуры, которых почти нет в тексте кодекса (он пишет полные формы):
# запрос юриста расширяется синонимами до tsquery-веток через OR
ABBREVIATIONS: dict[str, str] = {
    "ндс": "налог на добавленную стоимость",
    "ндфл": "налог на доходы физических лиц",
    "енп": "единый налоговый платеж",
    "енс": "единый налоговый счет",
    "усн": "упрощенная система налогообложения",
    "псн": "патентная система налогообложения",
    "кгн": "консолидированная группа налогоплательщиков",
    "енвд": "единый налог на вмененный доход",
    "есхн": "единый сельскохозяйственный налог",
    "тцо": "трансфертное ценообразование",
}


def expand_query(query: str) -> list[str]:
    """Запрос -> полные формы найденных аббревиатур (для OR-веток поиска)."""
    expansions = []
    for word in re.findall(r"\w+", query.lower()):
        if word in ABBREVIATIONS:
            expansions.append(ABBREVIATIONS[word])
    return expansions


def search_units(conn, query: str, as_of_date: str, limit: int = 10,
                 kind: str | None = None, chunks_only: bool = True) -> list[dict]:
    """Полнотекстовый поиск (лексическое плечо гибридного поиска, слой 4; ts_rank, не BM25).

    Чанк = пункт/подпункт (или статья без пунктов) с полным текстом и контекстом
    заголовков (вес B) — как в плане, §5 слой 4. По умолчанию ищем только по чанкам,
    чтобы одна и та же фраза не всплывала на уровне статьи, пункта и абзаца сразу;
    kind= переключает на конкретный вид единицы, chunks_only=False — на все.
    websearch_to_tsquery понимает естественный синтаксис («камеральная OR
    выездная проверка»); индекс всегда фильтруется по as_of_date. Аббревиатуры
    (НДС, ЕНС, ...) расширяются полными формами как OR-ветки — кодекс их
    пишет словами.
    """
    expansions = expand_query(query)
    chunk_filter = chunks_only and kind is None
    return conn.execute(
        """
        SELECT u.unit_id, u.kind, u.label, u.title, u.context,
               GREATEST(
                   ts_rank(setweight(t.search_vector, 'A') || setweight(u.context_vector, 'B'),
                           q_main),
                   COALESCE((SELECT max(ts_rank(t.search_vector,
                                   phraseto_tsquery('russian', e.phrase)))
                             FROM unnest(%s::text[]) AS e(phrase)), 0)
               ) AS rank,
               ts_headline('russian', coalesce(t.full_text, t.text), q_main,
                           'MaxWords=40, MinWords=15, StartSel=«, StopSel=», MaxFragments=2')
                   AS snippet
        FROM unit_text t
        JOIN unit u ON u.unit_id = t.unit_id,
             websearch_to_tsquery('russian', %s) q_main
        WHERE (t.search_vector @@ q_main
            OR u.context_vector @@ q_main
            OR EXISTS (SELECT 1 FROM unnest(%s::text[]) AS e(phrase)
                       WHERE t.search_vector @@ phraseto_tsquery('russian', e.phrase)))
          AND (t.valid_from IS NULL OR t.valid_from <= %s)
          AND (t.valid_to IS NULL OR t.valid_to > %s)
          AND (%s::text IS NULL OR u.kind = %s::text)
          AND (NOT %s::boolean OR u.is_chunk)
        ORDER BY rank DESC
        LIMIT %s
        """,
        (expansions, query, expansions,
         as_of_date, as_of_date, kind, kind, chunk_filter, limit),
    ).fetchall()


# --- слой 3: параметры и термины -------------------------------------------------

def load_parameters(conn, rows: list[dict], edition_valid_from: date | None = None) -> int:
    """Полная перезагрузка таблицы parameter из сида (data/parameters/*.json).

    valid_from_source = edition: начало неизвестно, valid_from остаётся NULL
    (норма действует как минимум с даты редакции; get_parameter отдаёт её на любую дату).
    """
    with conn.transaction():
        conn.execute("DELETE FROM parameter")
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO parameter (name, title, value, unit, valid_from, valid_to,
                                       valid_from_source, conditions, source_unit_id,
                                       anchor, region, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (p["name"], p.get("title"), p["value"], p["unit"], p.get("valid_from"),
                     p.get("valid_to"), p.get("valid_from_source", "edition"),
                     Json(p.get("conditions") or {}), p["source_unit_id"], p["anchor"],
                     p.get("region"), p.get("status", "verified_by_anchor"))
                    for p in rows
                ],
            )
    return len(rows)


def get_parameter(conn, name: str, as_of_date: str, region: str | None = None) -> dict | None:
    """Ставка/срок/лимит на дату (инструмент get_parameter слоя 5) вместе с текстом-доказательством."""
    return conn.execute(
        """
        SELECT p.name, p.title, p.value, p.unit, p.valid_from, p.valid_to, p.valid_from_source,
               p.conditions, p.source_unit_id, p.anchor, p.region, u.label,
               (SELECT t.full_text FROM unit_text t WHERE t.unit_id = p.source_unit_id
                  AND (t.valid_from IS NULL OR t.valid_from <= %s)
                  AND (t.valid_to IS NULL OR t.valid_to > %s)
                ORDER BY t.valid_from DESC NULLS LAST LIMIT 1) AS source_text
        FROM parameter p JOIN unit u ON u.unit_id = p.source_unit_id
        WHERE p.name = %s
          AND (p.valid_from IS NULL OR p.valid_from <= %s)
          AND (p.valid_to IS NULL OR p.valid_to > %s)
          AND (p.region IS NOT DISTINCT FROM %s OR p.region IS NULL)
        ORDER BY p.region NULLS LAST, p.valid_from DESC NULLS LAST
        LIMIT 1
        """,
        (as_of_date, as_of_date, name, as_of_date, as_of_date, region),
    ).fetchone()


def load_terms(conn, rows: list[dict], act_id: int, valid_from: date | None = None) -> int:
    """Перезагрузка терминов, определённых в единицах данного акта."""
    with conn.transaction():
        conn.execute(
            """
            DELETE FROM term WHERE definition_unit_id IN
                (SELECT unit_id FROM unit WHERE act_id = %s)
            """,
            (act_id,),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO term (term, term_norm, definition, definition_unit_id, scope,
                                  scope_unit_id, valid_from)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [(t["term"], t["term_norm"], t["definition"], t["definition_unit_id"],
                  t.get("scope", "code"), t.get("scope_unit_id"), valid_from) for t in rows],
            )
    return len(rows)


def find_terms(conn, query: str, as_of_date: str) -> list[dict]:
    """Термин по подстроке (регистронезависимо) на дату."""
    norm = query.lower().replace("ё", "е")
    return conn.execute(
        """
        SELECT t.term, t.definition, t.definition_unit_id, t.scope, t.scope_unit_id, u.label
        FROM term t JOIN unit u ON u.unit_id = t.definition_unit_id
        WHERE t.term_norm LIKE %s
          AND (t.valid_from IS NULL OR t.valid_from <= %s)
          AND (t.valid_to IS NULL OR t.valid_to > %s)
        ORDER BY (t.scope = 'code') DESC, length(t.term), t.term
        """,
        (f"%{norm}%", as_of_date, as_of_date),
    ).fetchall()


# --- слой 3: разъяснения (document / doc_reference) --------------------------------

def load_documents_db(conn, docs: list, edges: list[dict]) -> int:
    """Перезагрузка документов из реестра: документ + его рёбра interprets."""
    with conn.transaction():
        with conn.cursor() as cur:
            for d in docs:
                cur.execute("DELETE FROM document WHERE doc_id = %s", (d.doc_id,))
                cur.execute(
                    """
                    INSERT INTO document (doc_id, kind, agency, number, doc_date, title, text,
                                          source_url, mandatory, retrieved_at, sha256,
                                          status, category, tags)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (d.doc_id, d.kind, d.agency, d.number, d.date, d.title, d.text,
                     d.source_url, d.mandatory, d.retrieved_at, d.sha256,
                     d.status, d.category, Json(d.tags or [])),
                )
            cur.executemany(
                """
                INSERT INTO doc_reference (doc_id, to_unit_id, kind, raw_citation, status, confidence)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [(e["doc_id"], e["to_unit_id"], e["kind"], e["raw_citation"], e["status"],
                  e["confidence"]) for e in edges],
            )
    return len(docs)


def get_interpretations(conn, unit_id: str, as_of_date: str, limit: int = 10) -> list[dict]:
    """Документы, толкующие единицу (или её статью/потомков), изданные не позже даты."""
    from .interpretations import PRIORITY
    from .resolver import article_of_unit_id
    article = article_of_unit_id(unit_id)
    rows = conn.execute(
        """
        SELECT d.doc_id, d.kind, d.agency, d.number, d.doc_date AS date, d.title, d.mandatory,
               d.status, d.category, d.tags, d.source_url, left(d.text, 600) AS excerpt,
               array_agg(DISTINCT r.raw_citation) AS cites
        FROM document d JOIN doc_reference r ON r.doc_id = d.doc_id
        WHERE (r.to_unit_id = %s OR r.to_unit_id LIKE %s OR %s LIKE r.to_unit_id || '.%%'
               OR r.to_unit_id = %s)
          AND d.doc_date <= %s
        GROUP BY d.doc_id
        """,
        (unit_id, unit_id + ".%", unit_id, article, as_of_date),
    ).fetchall()
    rows.sort(key=lambda r: (PRIORITY.get(r["kind"], 9), not r["mandatory"], str(r["date"])))
    return rows[:limit]


def search_documents(conn, query: str, as_of_date: str, limit: int = 5) -> list[dict]:
    """Поиск по разъяснениям (отдельный индекс, слой 4): заголовок весомее текста."""
    expansions = expand_query(query)
    return conn.execute(
        """
        SELECT d.doc_id, d.kind, d.agency, d.number, d.doc_date AS date, d.title, d.mandatory,
               d.status, d.category, d.tags, d.source_url,
               GREATEST(ts_rank(d.search_vector, q),
                        COALESCE((SELECT max(ts_rank(d.search_vector, phraseto_tsquery('russian', e.phrase)))
                                  FROM unnest(%s::text[]) AS e(phrase)), 0)) AS rank,
               ts_headline('russian', d.text, q,
                           'MaxWords=40, MinWords=15, StartSel=«, StopSel=», MaxFragments=2') AS snippet
        FROM document d, websearch_to_tsquery('russian', %s) q
        WHERE (d.search_vector @@ q
            OR EXISTS (SELECT 1 FROM unnest(%s::text[]) AS e(phrase)
                       WHERE d.search_vector @@ phraseto_tsquery('russian', e.phrase)))
          AND d.doc_date <= %s
        ORDER BY rank DESC, d.doc_date DESC
        LIMIT %s
        """,
        (expansions, query, expansions, as_of_date, limit),
    ).fetchall()


# --- §7: снимки корпуса -------------------------------------------------------------

def create_snapshot(conn, description: str | None = None, git_commit: str | None = None) -> dict:
    """Фиксирует состояние БД: счётчики таблиц, акты с хешами источников и датами редакций,
    число документов по ведомствам. Юристу показывается номер снимка и дата актуальности."""
    counts = {}
    for table in ("unit", "unit_text", "reference", "amendment", "parameter", "term",
                  "document", "doc_reference"):
        counts[table] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
    acts = conn.execute(
        """
        SELECT a.act_code, a.source_sha256, a.retrieved_at, e.valid_from
        FROM act a LEFT JOIN edition e ON e.act_id = a.act_id ORDER BY a.act_code
        """).fetchall()
    docs = conn.execute(
        "SELECT agency, kind, count(*) AS n, max(doc_date) AS latest FROM document "
        "GROUP BY agency, kind ORDER BY agency, kind").fetchall()
    content = {"counts": counts,
               "acts": [dict(r) for r in acts],
               "documents": [dict(r) for r in docs]}
    row = conn.execute(
        "INSERT INTO snapshot (description, git_commit, content) VALUES (%s, %s, %s) "
        "RETURNING snapshot_id, created_at",
        (description, git_commit, Json(content, dumps=lambda o: __import__("json").dumps(o, default=str))),
    ).fetchone()
    conn.commit()
    return {"snapshot_id": row["snapshot_id"], "created_at": row["created_at"], **content}
