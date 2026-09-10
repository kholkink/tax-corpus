"""Судебный слой (план, §3.3): постановления Пленума ВАС/ВС и обзоры по налогам.

Источники — только первоисточники (arbitr.ru, vsrf.ru). Список актов задан в SEED:
URL страницы, вид, суд, номер, дата, название. Текст берётся из абзацев <p> страницы
между вводной частью («… постановляет …») и подписями («Председатель …»).

Выход: data/interpretations/court_acts.jsonl (kind=plenum|review|ruling), сырой HTML —
data/raw/courts/<id>.html. Запуск: python scripts/fetch_court_acts.py
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = "tax-corpus-research/0.1 (+https://github.com/kholkink/tax-corpus; kholkinkbauman@gmail.com)"

SEED = [
    {"doc_id": "plenum-vas-2013-57", "kind": "plenum", "agency": "ВАС РФ", "number": "57",
     "date": "2013-07-30",
     "title": "О некоторых вопросах, возникающих при применении арбитражными судами части первой "
              "Налогового кодекса Российской Федерации",
     "url": "https://arbitr.ru/materials/91622?path=%2Farxiv%2Fpost_plenum%2F"},
    {"doc_id": "plenum-vas-2006-53", "kind": "plenum", "agency": "ВАС РФ", "number": "53",
     "date": "2006-10-12",
     "title": "Об оценке арбитражными судами обоснованности получения налогоплательщиком налоговой выгоды",
     "url": "https://arbitr.ru/materials/3151?path=%2Farxiv%2Fpost_plenum%2F"},
]

RE_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)


def fetch(url: str, delay: float = 2.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = resp.read()
    time.sleep(delay)
    return data.decode("utf-8", errors="replace")


def clean(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_text(page: str) -> str | None:
    paras = [clean(p) for p in RE_P.findall(page)]
    paras = [p for p in paras if p]
    start = next((i for i, p in enumerate(paras)
                  if re.search(r"постановляет|ПОСТАНОВЛЯЕТ|разъяснения", p) and "Пленум" in p), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(paras))
                if re.match(r"(Председатель|Секретарь Пленума|Заместитель Председателя)", paras[i])),
               len(paras))
    body = paras[start:end]
    return "\n\n".join(body) if len(body) > 3 else None


def main() -> int:
    out = ROOT / "data" / "interpretations" / "court_acts.jsonl"
    raw_dir = ROOT / "data" / "raw" / "courts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(l)["doc_id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    n = 0
    with out.open("a", encoding="utf-8") as fh:
        for item in SEED:
            if item["doc_id"] in done:
                continue
            try:
                page = fetch(item["url"])
            except Exception as exc:
                print(f"[warn] {item['url']}: {exc}", file=sys.stderr)
                continue
            (raw_dir / f"{item['doc_id']}.html").write_text(page, encoding="utf-8")
            text = extract_text(page)
            if not text:
                print(f"[warn] {item['doc_id']}: текст не выделен", file=sys.stderr)
                continue
            doc = {**{k: v for k, v in item.items() if k != "url"}, "text": text,
                   "source_url": item["url"], "mandatory": False, "status": "actual",
                   "retrieved_at": datetime.now(timezone.utc).isoformat(),
                   "sha256": "sha256:" + hashlib.sha256(page.encode("utf-8")).hexdigest()}
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
            n += 1
            print(f"  {doc['agency']} № {doc['number']} от {doc['date']}: {len(text)} симв., "
                  f"пунктов ≈ {len(re.findall(r'(?m)^\\d+\\. ', text))}")
    print(f"добавлено: {n}; файл: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
