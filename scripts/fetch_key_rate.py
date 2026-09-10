"""Ключевая ставка Банка России (для пеней по ст. 75 НК) с cbr.ru -> data/parameters/key_rate.json.

Первоисточник: https://www.cbr.ru/hd_base/KeyRate/ (таблица по дням). Дневные значения
сжимаются в интервалы [valid_from, valid_to) со ставкой; последний интервал открыт.
Запуск: python scripts/fetch_key_rate.py [--from 01.01.2016]
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "parameters" / "key_rate.json"
UA = "tax-corpus-research/0.1 (+https://github.com/kholkink/tax-corpus; kholkinkbauman@gmail.com)"


def fetch_table(date_from: str, date_to: str) -> list[tuple[date, float]]:
    url = (f"https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True"
           f"&UniDbQuery.From={date_from}&UniDbQuery.To={date_to}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    rows = re.findall(r"<tr>\s*<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td>\s*([\d,\.]+)\s*</td>", html)
    out = []
    for d, v in rows:
        dd, mm, yy = d.split(".")
        out.append((date(int(yy), int(mm), int(dd)), float(v.replace(",", "."))))
    return sorted(out)


def compress(daily: list[tuple[date, float]]) -> list[dict]:
    """Дневные значения -> интервалы; valid_to = день, с которого действует следующая ставка."""
    intervals: list[dict] = []
    for d, v in daily:
        if intervals and intervals[-1]["rate"] == v:
            continue
        if intervals:
            intervals[-1]["valid_to"] = d.isoformat()
        intervals.append({"valid_from": d.isoformat(), "valid_to": None, "rate": v})
    return intervals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="01.01.2016")
    args = ap.parse_args(argv)
    today = date.today()
    daily = fetch_table(args.date_from, today.strftime("%d.%m.%Y"))
    if not daily:
        raise SystemExit("таблица ключевой ставки не распознана")
    intervals = compress(daily)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source_url": "https://www.cbr.ru/hd_base/KeyRate/",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "first_day": daily[0][0].isoformat(), "last_day": daily[-1][0].isoformat(),
        "unit": "percent_per_year", "intervals": intervals,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"дней: {len(daily)}, интервалов: {len(intervals)}, последняя ставка {intervals[-1]['rate']} % "
          f"с {intervals[-1]['valid_from']}; файл: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
