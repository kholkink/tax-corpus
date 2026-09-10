"""Краулер писем ФНС, обязательных для применения налоговыми органами (слой 1 плана).

Источник: https://www.nalog.gov.ru/rn77/about_fts/about_nalog/ (первоисточник, robots.txt
раздел разрешает). Уважительный режим: один запрос раз в --delay секунд, узнаваемый
User-Agent, повторный запуск пропускает уже выгруженные письма (--resume).

Выход: JSONL реестра разъяснений (interpretations.py, kind=letter, mandatory=true) со
статусом актуальности, тегами по статьям НК, категорией и провенансом (URL, дата
выгрузки, sha256 HTML). Сырой HTML сохраняется в data/raw/fns/<id>.html.

Запуск: python scripts/fetch_fns_letters.py --pages 2 [--max 30] [--delay 2]
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
BASE = "https://www.nalog.gov.ru"
LIST_URL = BASE + "/rn77/about_fts/about_nalog/"
UA = "tax-corpus-research/0.1 (+https://github.com/kholkink/tax-corpus; kholkinkbauman@gmail.com)"


def fetch(url: str, delay: float, attempts: int = 4) -> str:
    """GET с паузой после запроса; при обрыве (TLS EOF, 5xx, таймаут) — повтор с растущей
    паузой 15/45/135 с: сервер nalog.gov.ru рвёт соединения при плотном потоке запросов."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            time.sleep(delay)
            return data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            pause = 15 * (3 ** attempt)
            print(f"[retry] {url}: {type(exc).__name__}; пауза {pause} с", file=sys.stderr, flush=True)
            time.sleep(pause)
    raise last  # type: ignore[misc]


RE_ITEM = re.compile(r'<div class="news-block__item[^"]*">(.*?)(?=<div class="news-block__item|</section>|$)', re.S)
RE_HEADER = re.compile(r"№\s*([^<\s]+)\s+от\s+(\d{2}\.\d{2}\.\d{4})")
RE_LINK = re.compile(r'href="(/rn77/about_fts/about_nalog/(\d+)/)"')


def parse_listing(page_html: str) -> list[dict]:
    items = []
    for m in RE_ITEM.finditer(page_html):
        block = m.group(1)
        head = RE_HEADER.search(block)
        link = RE_LINK.search(block)
        if not head or not link:
            continue
        # статус: видимый блок тегов (без display: none) с подсказкой Актуально/Неактуально
        status = None
        for tags_div in re.finditer(r'<div class="tags tags_white" style="([^"]*)">.*?title="([^"]+)"', block, re.S):
            if "display: none" not in tags_div.group(1):
                status = "actual" if tags_div.group(2).strip().lower() == "актуально" else "outdated"
        items.append({"id": link.group(2), "url": BASE + link.group(1),
                      "number": html.unescape(head.group(1)), "date": head.group(2), "status": status})
    return items


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
    text = "".join(p.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _field(page: str, label: str) -> str | None:
    m = re.search(rf"<strong>{label}:\s*</strong>\s*(.*?)<br", page, re.S)
    return html_to_text(m.group(1)) if m else None


def parse_letter(page: str, meta: dict) -> dict | None:
    box = page.find('<div class="text_block">')
    if box < 0:
        return None
    end = page.find('<div class="gray mb-2">', box)
    seg = page[box:end if end > 0 else box + 400000]
    answer = re.search(r"<strong>Ответ:\s*</strong>", seg)
    if not answer:
        return None
    text = html_to_text(seg[answer.end():])
    question = _field(seg, "Вопрос")
    tags = [html.unescape(t) for t in re.findall(r">(Статья [^<]+НК РФ)</a>", seg)]
    number = _field(seg, "Номер") or meta["number"]
    date = _field(seg, "Дата письма") or meta["date"]
    d, mth, y = date.split(".")
    return {
        "doc_id": f"fns-{meta['id']}",
        "kind": "letter",
        "agency": "ФНС",
        "number": number,
        "date": f"{y}-{mth}-{d}",
        "title": question or "",
        "text": text,
        "source_url": meta["url"],
        "mandatory": True,
        "status": meta.get("status"),
        "category": _field(seg, r"Категория \(тематика\) письма"),
        "tags": tags,
        "published": _field(seg, "Дата публикации"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=1, help="последняя страница списка (по 15 писем)")
    ap.add_argument("--start-page", type=int, default=1, help="с какой страницы списка начать")
    ap.add_argument("--max", type=int, default=None, help="не больше N писем за запуск")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--out", default=str(ROOT / "data" / "interpretations" / "fns_mandatory.jsonl"))
    ap.add_argument("--raw-dir", default=str(ROOT / "data" / "raw" / "fns"))
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists() and not args.no_resume:
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["doc_id"])

    fetched = 0
    with out.open("a", encoding="utf-8") as fh:
        for page_no in range(args.start_page, args.pages + 1):
            url = LIST_URL if page_no == 1 else f"{LIST_URL}{page_no}.html"
            try:
                listing = fetch(url, args.delay)
            except Exception as exc:
                print(f"[warn] список {url}: {exc}", file=sys.stderr)
                continue
            items = parse_listing(listing)
            print(f"страница {page_no}: {len(items)} писем", flush=True)
            for meta in items:
                doc_id = f"fns-{meta['id']}"
                if doc_id in done:
                    continue
                if args.max is not None and fetched >= args.max:
                    print(f"достигнут --max {args.max}")
                    return 0
                try:
                    page = fetch(meta["url"], args.delay)
                except Exception as exc:
                    print(f"[warn] {meta['url']}: {exc}", file=sys.stderr)
                    continue
                (raw_dir / f"{meta['id']}.html").write_text(page, encoding="utf-8")
                doc = parse_letter(page, meta)
                if doc is None:
                    print(f"[warn] {meta['url']}: не распознан текст", file=sys.stderr)
                    continue
                doc["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                doc["sha256"] = "sha256:" + hashlib.sha256(page.encode("utf-8")).hexdigest()
                fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
                fh.flush()
                done.add(doc_id)
                fetched += 1
                print(f"  {doc['number']} от {doc['date']} [{doc['status']}] {doc['title'][:60]} "
                      f"({len(doc['text'])} симв., теги: {', '.join(doc['tags']) or '—'})", flush=True)
    print(f"выгружено за запуск: {fetched}; всего в реестре: {len(done)}; файл: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
