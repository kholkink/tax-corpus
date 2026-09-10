"""Краулер разъяснений Минфина России (minfin.gov.ru, раздел «Письма Минфина»).

Первоисточник; robots.txt раздел разрешает. Один запрос раз в --delay секунд,
узнаваемый User-Agent, resume по doc_id. Категории раздела перебираются
по страницам «Посмотреть ещё» (?page_N=K), текст письма берётся из HTML
страницы документа (div.text_wrapper), docx не нужен.

Выход: JSONL реестра (kind=letter, agency=Минфин, mandatory=false) с категорией,
тегами сайта и провенансом; сырой HTML — data/raw/minfin/<id>.html.

Запуск: python scripts/fetch_minfin_letters.py [--categories orgprofit,indirect] [--pages 2] [--max 30]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://minfin.gov.ru"
SECTION = BASE + "/ru/perfomance/tax_relations/Answers/"
CATEGORIES = ["commonlaw", "orgprofit", "fizprofit", "indirect", "property", "special",
              "imposition", "foreign", "international", "transfert", "customs_value"]
UA = "tax-corpus-research/0.1 (+https://github.com/kholkink/tax-corpus; kholkinkbauman@gmail.com)"


def fetch(url: str, delay: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    time.sleep(delay)
    return data.decode("utf-8", errors="replace")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(fragment: str) -> str:
    p = _Text()
    p.feed(fragment)
    text = re.sub(r"[ \t\xa0]+", " ", "".join(p.parts))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _block(page: str, start_marker: str, end_marker: str) -> str | None:
    i = page.find(start_marker)
    if i < 0:
        return None
    j = page.find(end_marker, i)
    return page[i:j if j > 0 else None]


RE_TITLE = re.compile(r"Письмо\s+Минфина\s+России\s+от\s+(\d{2}\.\d{2}\.\d{4})\s*(?:№|N)\s*(\S+)\s*(.*)", re.S)


def parse_category(page_html: str) -> tuple[list[str], int]:
    ids = []
    for m in re.finditer(r'data-href="[^"]*[?&]id_\d+=(\d+)-', page_html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    m = re.search(r'data-page-count="(\d+)"', page_html)
    return ids, int(m.group(1)) if m else 1


def parse_document(page: str, doc_id: str, url: str) -> dict | None:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    if not m:
        return None
    title_text = html_to_text(m.group(1))
    t = RE_TITLE.match(title_text)
    if not t:
        return None
    d, mth, y = t.group(1).split(".")
    body = _block(page, '<div class="text_wrapper">', '<!-- end') or ""
    text = html_to_text(body)
    if not text:
        return None
    section = re.search(r'Опубликован в разделе:\s*<a[^>]*>(.*?)</a>', page, re.S)
    published = re.search(r"Опубликовано:\s*(\d{2}\.\d{2}\.\d{4})", page)
    tags = sorted(set(html.unescape(x) for x in re.findall(r'TAG_ID_4\[\]=\d+"[^>]*title="([^"]+)"', page)))
    return {
        "doc_id": f"minfin-{doc_id}",
        "kind": "letter",
        "agency": "Минфин",
        "number": html.unescape(t.group(2)),
        "date": f"{y}-{mth}-{d}",
        "title": " ".join(t.group(3).split()),
        "text": text,
        "source_url": url,
        "mandatory": False,
        "status": None,
        "category": html_to_text(section.group(1)) if section else None,
        "tags": tags,
        "published": published.group(1) if published else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", default=",".join(CATEGORIES))
    ap.add_argument("--pages", type=int, default=None, help="страниц на категорию (по умолчанию все)")
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--out", default=str(ROOT / "data" / "interpretations" / "minfin_letters.jsonl"))
    ap.add_argument("--raw-dir", default=str(ROOT / "data" / "raw" / "minfin"))
    args = ap.parse_args()

    out, raw_dir = Path(args.out), Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["doc_id"])

    fetched = 0
    with out.open("a", encoding="utf-8") as fh:
        for cat in args.categories.split(","):
            page_no, total = 1, 1
            while page_no <= total and (args.pages is None or page_no <= args.pages):
                url = f"{SECTION}{cat}/" + (f"?page_57={page_no}" if page_no > 1 else "")
                try:
                    listing = fetch(url, args.delay)
                except Exception as exc:
                    print(f"[warn] {url}: {exc}", file=sys.stderr)
                    break
                ids, total = parse_category(listing)
                print(f"{cat} стр. {page_no}/{total}: {len(ids)} документов", flush=True)
                for doc_id in ids:
                    if f"minfin-{doc_id}" in done:
                        continue
                    if args.max is not None and fetched >= args.max:
                        print(f"достигнут --max {args.max}")
                        return 0
                    doc_url = f"{BASE}/ru/document?id_4={doc_id}"
                    try:
                        page = fetch(doc_url, args.delay)
                    except Exception as exc:
                        print(f"[warn] {doc_url}: {exc}", file=sys.stderr)
                        continue
                    (raw_dir / f"{doc_id}.html").write_text(page, encoding="utf-8")
                    doc = parse_document(page, doc_id, doc_url)
                    if doc is None:
                        print(f"[warn] {doc_url}: не распознано", file=sys.stderr)
                        continue
                    doc["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                    doc["sha256"] = "sha256:" + hashlib.sha256(page.encode("utf-8")).hexdigest()
                    fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
                    fh.flush()
                    done.add(doc["doc_id"])
                    fetched += 1
                    print(f"  {doc['number']} от {doc['date']} {doc['title'][:60]} "
                          f"({len(doc['text'])} симв.)", flush=True)
                page_no += 1
    print(f"выгружено за запуск: {fetched}; всего: {len(done)}; файл: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
