"""CLI корпуса: parse -> load -> ingest.

    python -m taxcorpus parse  --input data/raw/nk1.txt --act-code nk1
    python -m taxcorpus load   --data-dir data/processed/nk1
    python -m taxcorpus ingest --input data/raw/nk1.txt --act-code nk1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from .parser import parse_document
from .validator import validate


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _other_acts_units(out_dir: Path, act_code: str) -> list[dict]:
    """Единицы ранее разобранных актов (<код>_units.jsonl в out_dir), кроме текущего:
    нужны резолверу для ссылок между частями кодекса («глава 25» из ч.1 живёт в ч.2)."""
    extra: list[dict] = []
    for path in sorted(out_dir.glob("*_units.jsonl")):
        if path.name != f"{act_code}_units.jsonl":
            extra.extend(_read_jsonl(path))
    return extra


def _extract_references(records: list[dict], extra_records: list[dict] = ()) -> list[dict]:
    """Явные ссылки (регэкспы) + резолв в канонические unit_id по индексу всего корпуса."""
    from .references import extract_references
    from .resolver import resolve_all

    result: list[dict] = []
    for record in records:
        result.extend(extract_references(record["unit_id"], record["text"]))
    return resolve_all(records, result, index_records=[*records, *extra_records])


def cmd_parse(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    raw_bytes = input_path.read_bytes()
    raw = raw_bytes.decode("utf-8")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    root, records, stats = parse_document(raw, args.act_code)
    report = validate(records)
    references = _extract_references(records, _other_acts_units(out_dir, args.act_code))

    if args.valid_from:
        valid_from = args.valid_from
    else:
        valid_from = date.today().isoformat()
        print(f"[warn] --valid-from не задан: интервал действия редакции начнётся "
              f"с сегодняшней даты ({valid_from}); запросы get_unit на более ранние даты "
              "вернут пустоту. Укажите дату редакции из шапки банка.", file=sys.stderr)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    units_path = out_dir / f"{args.act_code}_units.jsonl"
    refs_path = out_dir / f"{args.act_code}_references.jsonl"
    amendments_path = out_dir / f"{args.act_code}_amendments.jsonl"
    meta_path = out_dir / f"{args.act_code}_meta.json"
    report_path = report_dir / f"{args.act_code}_validation.md"

    _write_jsonl(units_path, records)
    _write_jsonl(refs_path, references)

    from .amendments import amendments_from_records
    amendments = amendments_from_records(records)
    _write_jsonl(amendments_path, amendments)

    meta = {
        "act": {
            "code": args.act_code,
            "kind": "code",
            "official_number": args.official_number,
            "adoption_date": args.adoption_date,
            "title": args.act_title,
        },
        "source_url": args.source_url,
        "source_path": str(input_path),
        "source_format": input_path.suffix.lstrip(".") or "txt",
        "source_sha256": _sha256(raw_bytes),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "valid_from": valid_from,
        "stats": asdict(stats),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(
        report.render_markdown(f"Валидация {args.act_code}: {args.act_title}"),
        encoding="utf-8",
    )
    report_json_path = report_dir / f"{args.act_code}_validation.json"
    report_json_path.write_text(json.dumps({
        "errors": [asdict(i) for i in report.errors],
        "warnings": [asdict(i) for i in report.warnings],
        "infos": [asdict(i) for i in report.infos],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = ", ".join(f"{k}={v}" for k, v in sorted(asdict(stats)["units_by_kind"].items()))
    print(f"единиц: {len(records)} ({counts or 'нет'})")
    print(f"ссылок: {len(references)} (резолв: "
          f"{sum(1 for r in references if r.get('status') == 'resolved')} resolved, "
          f"{sum(1 for r in references if r.get('status') == 'partial')} partial, "
          f"{sum(1 for r in references if r.get('status') == 'unresolved')} unresolved, "
          f"{sum(1 for r in references if r.get('status') == 'external')} external)")
    print(f"правок из пометок редакции: {len(amendments)}")
    print(f"валидация: {len(report.errors)} ошибок, {len(report.warnings)} предупреждений")
    print(f"файлы: {units_path}, {refs_path}, {amendments_path}, {meta_path}")
    print(f"отчёт: {report_path}")

    if report.has_errors and not args.allow_errors:
        print("есть ошибки валидации — загрузку в БД стоит отложить; "
              "для принудительной загрузки: --allow-errors", file=sys.stderr)
        return 2
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    from .db import connect, ensure_schema, load_corpus

    data_dir = Path(args.data_dir)
    act_code = args.act_code
    meta = json.loads((data_dir / f"{act_code}_meta.json").read_text(encoding="utf-8"))
    units = _read_jsonl(data_dir / f"{act_code}_units.jsonl")
    refs_path = data_dir / f"{act_code}_references.jsonl"
    references = _read_jsonl(refs_path) if refs_path.exists() else []
    amendments_path = data_dir / f"{act_code}_amendments.jsonl"
    amendments = _read_jsonl(amendments_path) if amendments_path.exists() else []

    report_json_path = Path(args.report_dir) / f"{act_code}_validation.json"
    issues: list[dict] = []
    if report_json_path.exists():
        issues = json.loads(report_json_path.read_text(encoding="utf-8"))

    from .db import load_parameters, load_terms
    from .terms import extract_terms

    parameters_path = Path(args.parameters) if getattr(args, "parameters", None) else None
    conn = connect(args.db_url)
    try:
        ensure_schema(conn, args.schema)
        result = load_corpus(conn, meta, units, references, stats=meta.get("stats"),
                             issues=issues, amendments=amendments)
        edition_from = date.fromisoformat(meta["valid_from"]) if meta.get("valid_from") else None
        result["terms"] = load_terms(conn, extract_terms(units), result["act_id"], edition_from)
        result["parameters"] = 0
        if parameters_path and parameters_path.exists():
            seed = json.loads(parameters_path.read_text(encoding="utf-8"))["parameters"]
            # параметры ссылаются на единицы обоих актов: грузим только те, чьи источники уже в БД
            known = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM unit").fetchall()}
            rows = [p for p in seed if p["source_unit_id"] in known]
            result["parameters"] = load_parameters(conn, rows, edition_from)
            if len(rows) < len(seed):
                print(f"[warn] параметров пропущено {len(seed) - len(rows)}: их единицы-источники "
                      "ещё не загружены (загрузите второй акт и повторите load)", file=sys.stderr)
    finally:
        conn.close()

    print(f"загружено: act_id={result['act_id']}, edition_id={result['edition_id']}, "
          f"units={result['units']}, references={result['references']}, "
          f"amendments={result.get('amendments', 0)}, terms={result['terms']}, "
          f"parameters={result['parameters']}")
    return 0


def cmd_param(args: argparse.Namespace) -> int:
    """Ставка/срок/лимит на дату (get_parameter) с текстом-доказательством."""
    from .db import connect, get_parameter

    conn = connect(args.db_url)
    try:
        row = get_parameter(conn, args.name, args.as_of, region=args.region)
    finally:
        conn.close()
    if row is None:
        print(f"параметр {args.name} не найден или не действует на {args.as_of}", file=sys.stderr)
        return 1
    print(f"{row['name']} — {row['title'] or ''}: {row['value']} {row['unit']}")
    start = row["valid_from"] or f"не ранее даты редакции (источник интервала: {row['valid_from_source']})"
    print(f"интервал: {start} … {row['valid_to'] or 'наст. время'}")
    if row["conditions"]:
        print(f"условия: {json.dumps(row['conditions'], ensure_ascii=False)}")
    print(f"источник: {row['source_unit_id']} — {row['label']}")
    if row["source_text"]:
        print()
        print(row["source_text"])
    return 0


def cmd_term(args: argparse.Namespace) -> int:
    """Определение термина (ст. 11 НК) на дату."""
    from .db import connect, find_terms

    conn = connect(args.db_url)
    try:
        rows = find_terms(conn, args.term, args.as_of)
    finally:
        conn.close()
    if not rows:
        print(f"термин «{args.term}» не найден", file=sys.stderr)
        return 1
    for r in rows[: args.limit]:
        where = "" if r["scope"] == "code" else f"; область: {r['scope_unit_id']}"
        print(f"{r['term']} — {r['definition']}")
        print(f"   [{r['definition_unit_id']} — {r['label']}{where}]")
    return 0


def cmd_unit(args: argparse.Namespace) -> int:
    """Норма на дату: get_unit(unit_id, as_of_date) — принцип «время — первичная ось»."""
    from .db import connect, get_unit, list_amendments

    conn = connect(args.db_url)
    try:
        record = get_unit(conn, args.id, args.as_of)
        if record is None:
            print(f"единица {args.id} не найдена или не действовала на {args.as_of}",
                  file=sys.stderr)
            return 1
        print(f"{record['unit_id']} — {record['label']}")
        if record["title"]:
            print(record["title"])
        if record.get("context"):
            print(f"контекст: {record['context']}")
        print(f"интервал действия: {record['valid_from'] or '?'} … "
              f"{record['valid_to'] or 'наст. время'}")
        if record["edit_note"]:
            print(f"пометка: {record['edit_note']}")
        print()
        # полный текст (с подпунктами) — то, что цитирует юрист; text — только свои абзацы
        print(record.get("full_text") or record["text"])
        for row in list_amendments(conn, args.id):
            eff = f", вступила {row['effective_date']}" if row.get("effective_date") else ""
            print(f"\n[правка] {row['operation']}: ФЗ № {row['amending_act_number']} "
                  f"от {row['amending_act_date']}{eff}")
    finally:
        conn.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    rc = cmd_parse(args)
    if rc != 0 and not args.allow_errors:
        return rc
    return cmd_load(args)


def cmd_convert(args: argparse.Namespace) -> int:
    from .ingest import convert

    input_path = Path(args.input)
    fmt = (args.format or input_path.suffix.lstrip(".")).lower()
    raw = input_path.read_bytes().decode("utf-8-sig")
    text = convert(raw, fmt)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"формат: {fmt}; абзацев: {text.count(chr(10) * 2) + 1}; записано: {out}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Полнотекстовый поиск по нормам (BM25-плечо гибридного поиска, слой 4)."""
    from .db import connect, search_units

    conn = connect(args.db_url)
    try:
        rows = search_units(conn, args.query, args.as_of, limit=args.limit, kind=args.kind,
                            chunks_only=not args.all_kinds)
    finally:
        conn.close()
    if not rows:
        print("ничего не найдено")
        return 1
    for i, r in enumerate(rows, 1):
        print(f"{i}. [{r['rank']:.3f}] {r['unit_id']} — {r['label']}")
        print(f"   {r['snippet']}")
        print()
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Что изменилось: история правок единицы за период (list_amendments)."""
    from .db import connect, get_unit, list_amendments

    conn = connect(args.db_url)
    try:
        record = get_unit(conn, args.id, args.as_of)
        if record is None:
            print(f"единица {args.id} не найдена или не действовала на {args.as_of}",
                  file=sys.stderr)
            return 1
        rows = list_amendments(conn, args.id, since=args.since)
    finally:
        conn.close()
    print(f"{record['unit_id']} — {record['label']} (текст на {args.as_of})")
    if not rows:
        print(f"правок с {args.since} не зафиксировано")
        return 0
    print(f"правок с {args.since}: {len(rows)}")
    for row in rows:
        date = row["amending_act_date"] or "дата не распознана"
        print(f"  {date}  {row['operation']:12s} ФЗ № {row['amending_act_number']}")
    return 0


def cmd_deadline(args: argparse.Namespace) -> int:
    """Срок по ст. 6.1 НК (compute_deadline): каждая операция — со ссылкой на пункт."""
    from .deadlines import ProductionCalendar, compute_deadline

    unit = "calendar_days" if args.unit == "days" and args.calendar_days else args.unit
    try:
        result = compute_deadline(date.fromisoformat(args.start), args.amount, unit,
                                  ProductionCalendar.load(args.calendar_dir))
    except ValueError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"срок {args.amount} {unit} от {args.start}: окончание {result.end.isoformat()}"
          + (f" (номинально {result.nominal_end.isoformat()})" if result.shifted else ""))
    for step in result.steps:
        print(f"  - {step}")
    print("нормы: " + ", ".join(result.applied))
    if result.calendar_note:
        print(f"[внимание] {result.calendar_note}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Вопрос агенту (слой 6): инструменты корпуса -> синтез -> проверка цитат."""
    from .agent import TaxAgent, render
    from .deadlines import ProductionCalendar
    from .tools import DbCorpus, LocalCorpus

    try:
        import anthropic
    except ImportError:
        print("нужен пакет anthropic: pip install -e '.[agent]'", file=sys.stderr)
        return 1
    client = anthropic.Anthropic()  # ключ из ANTHROPIC_API_KEY или профиля `ant auth login`

    conn = None
    if args.local:
        corpus = LocalCorpus(args.data_dir)
        print("[внимание] офлайн-корпус: поиск грубый (по совпадению слов), без БД", file=sys.stderr)
    else:
        from .db import connect
        conn = connect(args.db_url)
        corpus = DbCorpus(conn, args.data_dir)
    try:
        agent = TaxAgent(client, corpus, model=args.model, effort=args.effort,
                         fallbacks=not args.no_fallbacks,
                         calendar=ProductionCalendar.load(args.calendar_dir))
        result = agent.ask(args.question, args.as_of)
    finally:
        if conn is not None:
            conn.close()
    print(render(result))
    if args.log:
        Path(args.log).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0 if result.verification.ok and not result.refused else 2


def cmd_load_docs(args: argparse.Namespace) -> int:
    """Реестр разъяснений (JSONL) -> PostgreSQL: документы + рёбра interprets."""
    from .db import connect, ensure_schema, load_documents_db
    from .interpretations import link_document, load_documents
    from .resolver import UnitIndex

    docs = load_documents(args.input)
    records = []
    for path in sorted(Path(args.data_dir).glob("*_units.jsonl")):
        records.extend(_read_jsonl(path))
    index = UnitIndex(records)
    edges = [e for d in docs for e in link_document(d, index)]
    conn = connect(args.db_url)
    try:
        ensure_schema(conn, args.schema)
        n = load_documents_db(conn, docs, edges)
    finally:
        conn.close()
    print(f"документов: {n}, рёбер interprets: {len(edges)}")
    return 0


def cmd_interpretations(args: argparse.Namespace) -> int:
    """Разъяснения по единице на дату (офлайн: реестр JSONL + корпус)."""
    from .tools import LocalCorpus

    corpus = LocalCorpus(args.data_dir, parameters_path=None, interpretations_dir=args.input)
    rows = corpus.get_interpretations(args.id, args.as_of, limit=args.limit)
    if not rows:
        print("разъяснений не найдено")
        return 1
    for r in rows:
        flag = " [обязательно для налоговых органов]" if r["mandatory"] else ""
        print(f"{r['kind']} {r['agency']} от {r['date']} № {r['number']}{flag}")
        print(f"   {r['title']}")
        print(f"   цитирует: {', '.join(r['cites'])}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Снимок корпуса (§7 плана): номер, дата, счётчики, хеши источников, коммит."""
    import subprocess
    from .db import connect, create_snapshot, ensure_schema

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=False).stdout.strip() or None
    except OSError:
        commit = None
    conn = connect(args.db_url)
    try:
        ensure_schema(conn, args.schema)
        snap = create_snapshot(conn, args.description, commit)
    finally:
        conn.close()
    print(f"снимок #{snap['snapshot_id']} от {snap['created_at']} (коммит {commit or '—'})")
    print("счётчики:", json.dumps(snap["counts"], ensure_ascii=False))
    for a in snap["acts"]:
        print(f"  {a['act_code']}: редакция с {a['valid_from']}, источник {a['source_sha256']}")
    for d in snap["documents"]:
        print(f"  {d['agency']} {d['kind']}: {d['n']} (последний {d['latest']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxcorpus", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", required=True, help="сырой текст кодекса (utf-8)")
    common.add_argument("--act-code", default="nk1", help="канонический код акта")
    common.add_argument("--act-title",
                        default="Налоговый кодекс Российской Федерации (часть первая)")
    common.add_argument("--official-number", default="146-ФЗ")
    common.add_argument("--adoption-date", default="1998-07-31")
    common.add_argument("--source-url", default=None, help="URL первоисточника")
    common.add_argument("--valid-from", default=None,
                        help="дата начала действия редакции (YYYY-MM-DD) из шапки банка; "
                             "без неё — сегодня, с предупреждением")
    common.add_argument("--allow-errors", action="store_true")

    p_parse = sub.add_parser("parse", parents=[common], help="разобрать текст и вывести JSONL")
    p_parse.add_argument("--out-dir", default="data/processed")
    p_parse.add_argument("--report-dir", default="reports")
    p_parse.set_defaults(func=cmd_parse)

    p_load = sub.add_parser("load", help="загрузить JSONL в PostgreSQL")
    p_load.add_argument("--data-dir", default="data/processed")
    p_load.add_argument("--act-code", default="nk1")
    p_load.add_argument("--db-url", default=None)
    p_load.add_argument("--schema", default=None, help="путь к sql/schema.sql")
    p_load.add_argument("--report-dir", default="reports")
    p_load.add_argument("--parameters", default="data/parameters/parameters_v0.json",
                        help="сид параметров (ставки/сроки); '' — не грузить")
    p_load.set_defaults(func=cmd_load)

    p_ingest = sub.add_parser("ingest", parents=[common], help="parse + load за один проход")
    p_ingest.add_argument("--out-dir", default="data/processed")
    p_ingest.add_argument("--report-dir", default="reports")
    p_ingest.add_argument("--db-url", default=None)
    p_ingest.add_argument("--schema", default=None)
    p_ingest.add_argument("--parameters", default="data/parameters/parameters_v0.json")
    p_ingest.set_defaults(func=cmd_ingest)

    p_convert = sub.add_parser("convert", help="сырой источник -> нормализованный текст")
    p_convert.add_argument("--input", required=True)
    p_convert.add_argument("--output", required=True)
    p_convert.add_argument("--format", default=None, help="html|txt; по умолчанию из расширения")
    p_convert.set_defaults(func=cmd_convert)

    p_unit = sub.add_parser("unit", help="текст единицы на дату (get_unit)")
    p_unit.add_argument("--id", required=True, help="канонический unit_id")
    p_unit.add_argument("--as-of", default=date.today().isoformat(), help="дата (YYYY-MM-DD)")
    p_unit.add_argument("--db-url", default=None)
    p_unit.set_defaults(func=cmd_unit)

    p_search = sub.add_parser("search", help="полнотекстовый поиск по нормам (BM25)")
    p_search.add_argument("--query", required=True, help='например: "камеральная проверка"')
    p_search.add_argument("--as-of", default=date.today().isoformat())
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--kind", default=None, help="фильтр по виду единицы: article|point|...")
    p_search.add_argument("--all-kinds", action="store_true",
                          help="искать по всем единицам, а не только по чанкам (пункт/подпункт)")
    p_search.add_argument("--db-url", default=None)
    p_search.set_defaults(func=cmd_search)

    p_diff = sub.add_parser("diff", help="история правок единицы за период")
    p_diff.add_argument("--id", required=True)
    p_diff.add_argument("--since", required=True, help="начало периода (YYYY-MM-DD)")
    p_diff.add_argument("--as-of", default=date.today().isoformat())
    p_diff.add_argument("--db-url", default=None)
    p_diff.set_defaults(func=cmd_diff)

    p_param = sub.add_parser("param", help="ставка/срок/лимит на дату (get_parameter)")
    p_param.add_argument("--name", required=True, help="например: vat_rate_general")
    p_param.add_argument("--as-of", default=date.today().isoformat())
    p_param.add_argument("--region", default=None)
    p_param.add_argument("--db-url", default=None)
    p_param.set_defaults(func=cmd_param)

    p_term = sub.add_parser("term", help="определение термина (ст. 11 НК) на дату")
    p_term.add_argument("--term", required=True, help="подстрока термина: «индивидуальн»")
    p_term.add_argument("--as-of", default=date.today().isoformat())
    p_term.add_argument("--limit", type=int, default=5)
    p_term.add_argument("--db-url", default=None)
    p_term.set_defaults(func=cmd_term)

    p_dl = sub.add_parser("deadline", help="срок по ст. 6.1 НК (compute_deadline)")
    p_dl.add_argument("--start", required=True, help="дата события/начала (YYYY-MM-DD)")
    p_dl.add_argument("--amount", required=True, type=int)
    p_dl.add_argument("--unit", default="days", choices=["days", "months", "quarters", "years"])
    p_dl.add_argument("--calendar-days", action="store_true",
                      help="срок в календарных днях (по умолчанию дни — рабочие, п. 6 ст. 6.1)")
    p_dl.add_argument("--calendar-dir", default=None,
                      help="каталог с переносами выходных по годам (data/calendar/<год>.json)")
    p_dl.set_defaults(func=cmd_deadline)

    p_ask = sub.add_parser("ask", help="вопрос агенту с проверкой цитат (нужен anthropic + ключ)")
    p_ask.add_argument("--question", required=True)
    p_ask.add_argument("--as-of", default=date.today().isoformat())
    p_ask.add_argument("--model", default="claude-opus-5")
    p_ask.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p_ask.add_argument("--local", action="store_true", help="офлайн-корпус из JSONL вместо БД")
    p_ask.add_argument("--data-dir", default="data/processed")
    p_ask.add_argument("--db-url", default=None)
    p_ask.add_argument("--calendar-dir", default=None)
    p_ask.add_argument("--no-fallbacks", action="store_true",
                       help="не использовать серверный фолбэк при отказе модели")
    p_ask.add_argument("--log", default=None, help="куда записать JSON-журнал запроса")
    p_ask.set_defaults(func=cmd_ask)

    p_docs = sub.add_parser("load-docs", help="реестр разъяснений (JSONL) -> PostgreSQL")
    p_docs.add_argument("--input", default="data/interpretations")
    p_docs.add_argument("--data-dir", default="data/processed")
    p_docs.add_argument("--db-url", default=None)
    p_docs.add_argument("--schema", default=None)
    p_docs.set_defaults(func=cmd_load_docs)

    p_int = sub.add_parser("interpretations", help="разъяснения по единице на дату (офлайн)")
    p_int.add_argument("--id", required=True)
    p_int.add_argument("--as-of", default=date.today().isoformat())
    p_int.add_argument("--limit", type=int, default=10)
    p_int.add_argument("--input", default="data/interpretations")
    p_int.add_argument("--data-dir", default="data/processed")
    p_int.set_defaults(func=cmd_interpretations)

    p_snap = sub.add_parser("snapshot", help="зафиксировать снимок корпуса в БД (§7 плана)")
    p_snap.add_argument("--description", default=None)
    p_snap.add_argument("--db-url", default=None)
    p_snap.add_argument("--schema", default=None)
    p_snap.set_defaults(func=cmd_snapshot)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
