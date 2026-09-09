"""Загрузка текущей редакции акта из банка ГАС «Законодательство России».

Рецепт из data/sources-report.md §3: cookie-сессия (GET portal.html) -> POST
s.action с каталожным запросом (additionalFields=document_text_tag). Ответ
содержит полные тексты всех найденных документов; акт выбирается по UUID.

Запуск: python scripts/fetch_act.py --uuid B5C1D49E-FAAD-4027-8721-C4ED5CA2F0A3 \
            --out data/raw/sources/nk2.html --label nk2
Пауза >= 2 с между запросами; TLS-сертификат хоста просрочен — проверка отключена
(зафиксировано в отчёте по источникам).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib import request

BASE = "https://pravo-search.minjust.ru/bigs"
HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE}/portal.html",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "identity",
}

CATALOG_QUERY = {
    "request": {
        "mode": "EXTENDED",
        "rows": 40,
        "typeRequests": [{
            "name": "Правовые акты", "mode": "AND", "typesMode": None,
            "fieldRequestGroups": [{"mode": "AND", "fieldRequests": [
                {"name": "document_description", "operator": "EX", "query": "КОДЕКС"}]}],
        }],
        "groups": ["Текущие редакции"],
        "sortField": "document_date_edition", "sortOrder": "asc",
        "additionalFields": ["document_text_tag", "document_text", "document_edition",
                             "document_number", "document_date_edition",
                             "document_date_public"],
        "customFilters": [],
    }
}


def make_opener() -> request.OpenerDirector:
    ctx = ssl._create_unverified_context()
    return request.build_opener(
        request.HTTPCookieProcessor(CookieJar()),
        request.HTTPSHandler(context=ctx),
    )


def post_json(opener, url: str, payload: dict, timeout: int = 180) -> dict:
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers=HEADERS, method="POST")
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_session(opener) -> None:
    req = request.Request(f"{BASE}/portal.html", headers={"User-Agent": HEADERS["User-Agent"]})
    with opener.open(req, timeout=60) as resp:
        resp.read()


def normalize_additional(raw) -> dict:
    """additionalFields приходит словарём или списком повторяющихся полей."""
    if isinstance(raw, dict):
        return raw
    fields: dict[str, str] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and "name" in entry:
                fields[entry["name"]] = entry.get("value") or ""
            elif isinstance(entry, str):
                fields.setdefault(entry, "")
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uuid", required=True, help="UUID акта в банке")
    parser.add_argument("--out", required=True, help="путь к файлу HTML")
    parser.add_argument("--label", default="act")
    args = parser.parse_args()

    opener = make_opener()
    print("сессия (GET portal.html)...")
    get_session(opener)
    time.sleep(2)

    print("каталожный запрос с текстами (это может занять минуту)...")
    data = post_json(opener, f"{BASE}/s.action", CATALOG_QUERY)
    docs = (data.get("searchResult") or {}).get("documents") or []
    print(f"найдено документов: {len(docs)}")

    target = None
    for d in docs:
        if str(d.get("id", "")).upper().startswith(args.uuid.upper()[:8]):
            target = d
            break
    if target is None:
        print("акт не найден в ответе", file=sys.stderr)
        return 1

    add = normalize_additional(target.get("additionalFields"))
    html = add.get("document_text_tag") or ""
    edition = str(add.get("document_edition") or "ред-unknown")
    number = str(add.get("document_number") or "")
    date_ed = str(add.get("document_date_edition") or "")
    if not html:
        print("поле document_text_tag пусто", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    slug = re.sub(r"[^\w.-]+", "_", edition)[:60]
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps({
        "uuid": target.get("id"),
        "name": target.get("name"),
        "number": number,
        "edition": edition,
        "date_edition": date_ed,
        "shard": target.get("shard"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{args.label}] {number}, {edition}")
    print(f"HTML: {out} ({out.stat().st_size / 1024 / 1024:.1f} МБ)")
    print(f"метаданные: {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
