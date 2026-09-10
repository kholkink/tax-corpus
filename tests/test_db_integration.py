"""Интеграционные тесты слоя БД. Пропускаются, если локальный Postgres недоступен."""

import os

import pytest

from taxcorpus import load_dotenv  # noqa: E402

load_dotenv()  # TAXCORPUS_DB из .env
DB_URL = os.environ.get("TAXCORPUS_DB", "postgresql://postgres@127.0.0.1:5432/taxcorpus")

psycopg = pytest.importorskip("psycopg")


def _db_available() -> bool:
    try:
        with psycopg.connect(DB_URL, connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="локальный Postgres недоступен")


def _conn():
    return psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row, connect_timeout=3)


def test_get_unit_returns_current_text():
    from taxcorpus.db import get_unit

    with _conn() as conn:
        record = get_unit(conn, "nk1.ch14.art88.p1", "2026-09-09")
    assert record is not None
    assert record["label"] == "пункт 1 статьи 88 НК РФ"
    assert record["text"].startswith("Камеральная налоговая проверка")


def test_get_unit_respects_as_of_before_edition():
    from taxcorpus.db import get_unit

    with _conn() as conn:
        record = get_unit(conn, "nk1.ch14.art88.p1", "2000-01-01")
    # загружена одна редакция (интервал от даты выгрузки) — на 2000 год текста нет
    assert record is None


def test_search_finds_russian_morphology():
    from taxcorpus.db import search_units

    with _conn() as conn:
        rows = search_units(conn, "камеральная налоговая проверка", "2026-09-09", limit=5)
    assert rows, "поиск не вернул результатов"
    # сниппет с подсветкой обязан содержать словоформы запроса
    assert all("проверк" in r["snippet"].lower() for r in rows)


def test_search_expands_abbreviations():
    """«НДС» в тексте кодекса почти не встречается (пишется полностью) —
    поиск обязан расширять аббревиатуру полной формой и находить нормы."""
    from taxcorpus.db import search_units

    with _conn() as conn:
        rows = search_units(conn, "ставка НДС", "2026-09-09", limit=5)
    assert rows, "расширение НДС → «налог на добавленную стоимость» не сработало"


def test_list_amendments_since_filter():
    from taxcorpus.db import list_amendments

    with _conn() as conn:
        all_rows = list_amendments(conn, "nk1.ch1.art6-1")
        old_rows = list_amendments(conn, "nk1.ch1.art6-1", since="2020-01-01")
    assert all_rows, "у ст. 6.1 должна быть правка 137-ФЗ от 2006"
    assert len(old_rows) < len(all_rows)


def test_search_units_strict_then_loose():
    from taxcorpus.db import search_units

    with _conn() as conn:
        rows = search_units(conn, "камеральная налоговая проверка проводится в течение трех месяцев",
                            "2026-09-10", limit=5)
        assert rows and rows[0]["unit_id"].startswith("nk1.ch14.art88")
        assert all(r["pass"] in ("strict", "loose") for r in rows)
        # лишнее слово не обнуляет выдачу: строгий проход пуст, работает добор по «ИЛИ»
        rows = search_units(conn, "выездная проверка не может продолжаться более двух месяцев абракадабра",
                            "2026-09-10", limit=5)
        assert rows and rows[0]["pass"] == "loose"


def test_parameter_terms_interpretations_snapshot():
    from taxcorpus.db import create_snapshot, find_terms, get_interpretations, get_parameter, search_documents

    with _conn() as conn:
        p = get_parameter(conn, "vat_rate_general", "2026-09-10")
        assert p and float(p["value"]) == 22 and "22 процента" in p["source_text"]
        assert get_parameter(conn, "vat_rate_general", "2025-06-01") is None  # valid_from 2026-01-01
        terms = find_terms(conn, "индивидуальн", "2026-09-10")
        assert terms and terms[0]["definition_unit_id"].startswith("nk1.ch1.art11.p2")
        docs = get_interpretations(conn, "nk1.ch14.art88", "2026-09-10", 5)
        assert docs and all(str(d["date"]) <= "2026-09-10" for d in docs)
        found = search_documents(conn, "камеральная проверка", "2026-09-10", 3)
        assert found and found[0]["snippet"]
        snap = create_snapshot(conn, "test")
        assert snap["counts"]["unit"] > 30000 and snap["snapshot_id"] >= 1


def test_migrations_apply_once_and_workspace_store_syncs(tmp_path):
    from taxcorpus.db import ensure_schema
    from taxcorpus.workspace import Workspace
    from taxcorpus.workspace_store import subscriptions_for, sync_workspace

    with _conn() as conn:
        ensure_schema(conn)
        assert ensure_schema(conn) == []  # повторно ничего не применяется
        names = {r["name"] for r in conn.execute("SELECT name FROM schema_version").fetchall()}
        assert "001_workspace_metadata.sql" in names
        ws = Workspace.create("it-sync", "Интеграционное дело", as_of="2026-09-10", root=tmp_path)
        ws.write_file("research/позиция.md", "текст", {"sources": ["nk1.ch14.art88.p2", "fns-1"]})
        info = sync_workspace(conn, ws)
        assert info["files"] >= 2
        assert subscriptions_for(conn, info["workspace_id"]) == ["fns-1", "nk1.ch14.art88.p2"]
        # повторная синхронизация идемпотентна
        assert sync_workspace(conn, ws)["workspace_id"] == info["workspace_id"]
        conn.execute("DELETE FROM workspace WHERE slug = 'it-sync'")


def test_unit_versions_and_parameters_for_unit():
    from taxcorpus.db import parameters_for_unit, unit_versions

    with _conn() as conn:
        versions = unit_versions(conn, "nk2.ch21.art164.p3")
        params = parameters_for_unit(conn, "nk2.ch21.art164", "2026-09-09")
        desk = parameters_for_unit(conn, "nk1.ch14.art88.p2", "2026-09-09")
        none = parameters_for_unit(conn, "nk9.none", "2026-09-09")
    assert versions and versions[-1]["current"] and versions[-1]["chars"] > 0
    assert any(p["name"] == "vat_rate_general" for p in params) and all(p["source_unit_id"].startswith("nk2.ch21.art164") for p in params)
    assert [p["name"] for p in desk] == ["desk_audit_duration"] and none == []
