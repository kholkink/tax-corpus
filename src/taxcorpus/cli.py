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
            # запрос вне with conn.transaction() открыл бы неявную транзакцию, внутри которой
            # load_parameters стал бы savepoint'ом и откатился при закрытии соединения
            with conn.transaction():
                known = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM unit").fetchall()}
            rows = [p for p in seed if p["source_unit_id"] in known]
            result["parameters"] = load_parameters(conn, rows, edition_from)
            if len(rows) < len(seed):
                print(f"[warn] параметров пропущено {len(seed) - len(rows)}: их единицы-источники "
                      "ещё не загружены (загрузите второй акт и повторите load)", file=sys.stderr)
        # F2: события перезагрузки (изменённые/исключённые/новые единицы, параметры) -> corpus_event
        from .db import detect_parameter_changes, record_events
        events = list(result.get("events") or []) + detect_parameter_changes(conn, result.pop("_old_parameters", None) or {})
        result["events"] = record_events(conn, events)
    finally:
        conn.close()

    if result.get("events"):
        print(f"событий мониторинга записано: {result['events']} (jobs run monitor — уведомления делам)")
    print(f"загружено: act_id={result['act_id']}, edition_id={result['edition_id']}, "
          f"units={result['units']}, references={result['references']} "
          f"(отложено {result.get('references_pending', 0)}, дозаполнено "
          f"{result.get('references_relinked', 0)}), amendments={result.get('amendments', 0)}, "
          f"terms={result['terms']}, parameters={result['parameters']}")
    print("[внимание] рёбра писем к единицам этого акта пересозданы не были: выполните "
          "`load-docs` повторно", file=sys.stderr)
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


def cmd_embed(args: argparse.Namespace) -> int:
    """Построить семантический индекс чанков (слой 4): data/index/<model>.npz."""
    from .embeddings import DenseIndex

    records = []
    for path in sorted(Path(args.data_dir).glob("*_units.jsonl")):
        records.extend(_read_jsonl(path))
    index = DenseIndex(args.model)
    if args.to_db and not args.rebuild and index.load():
        n = len(index.ids)
        print(f"индекс загружен из {index.path}: чанков {n}")
    else:
        n = index.build(records, max_chars=args.max_chars)
        print(f"чанков: {n}; модель: {args.model}; файл: {index.path}")
    if args.to_db:
        from .db import connect
        from .embeddings import load_embeddings_db
        conn = connect(args.db_url)
        try:
            print(f"в БД (pgvector): {load_embeddings_db(conn, index)} строк")
        finally:
            conn.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Поиск по нормам: лексический (ts_rank_cd) или гибридный с семантическим плечом (RRF)."""
    from .db import connect, search_units

    conn = connect(args.db_url)
    try:
        if args.hybrid:
            from .tools import DbCorpus
            corpus = DbCorpus(conn, args.data_dir)
            if not corpus.hybrid.enabled:
                print("[warn] семантический индекс не построен (python -m taxcorpus embed) — "
                      "только лексический поиск", file=sys.stderr)
            rows = corpus.search(args.query, args.as_of, limit=args.limit)
        else:
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
    from dataclasses import replace
    from .providers import ProviderError, choose, make_client
    try:
        provider = choose("standard", getattr(args, "provider", None))
    except ProviderError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    if args.model:
        provider = replace(provider, model=args.model)
    client = make_client(provider)
    model = provider.model
    fallbacks = provider.fallbacks and not args.no_fallbacks
    print(f"[провайдер] {provider.badge()}", file=sys.stderr)

    conn = None
    if args.local:
        corpus = LocalCorpus(args.data_dir)
        print("[внимание] офлайн-корпус: поиск грубый (по совпадению слов), без БД", file=sys.stderr)
    else:
        from .db import connect
        conn = connect(args.db_url)
        corpus = DbCorpus(conn, args.data_dir)
    try:
        agent = TaxAgent(client, corpus, model=model, effort=args.effort, fallbacks=fallbacks,
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


def _make_agent(args: argparse.Namespace, corpus, workspace=None):
    """Агент по профилю провайдера (P4/F9): явный --provider > профиль дела > конфиденциальность > default."""
    from dataclasses import replace
    from .deadlines import ProductionCalendar
    from .providers import ProviderError, choose, make_agent

    manifest = getattr(workspace, "manifest", None)
    try:
        provider = choose(getattr(manifest, "confidentiality", "standard"),
                          getattr(args, "provider", None) or getattr(manifest, "provider", None))
    except ProviderError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if getattr(args, "model", None):
        provider = replace(provider, model=args.model)
    print(f"[провайдер] {provider.badge()}", file=sys.stderr)
    return make_agent(corpus, provider, effort=getattr(args, "effort", "high"),
                      calendar=ProductionCalendar.load())


def _open_corpus(args: argparse.Namespace):
    from .tools import DbCorpus, LocalCorpus
    if getattr(args, "local", False):
        return LocalCorpus(args.data_dir), None
    from .db import connect
    conn = connect(getattr(args, "db_url", None))
    return DbCorpus(conn, args.data_dir), conn


def cmd_workspace(args: argparse.Namespace) -> int:
    """Рабочее пространство дела: new / list / files / add / facts / timeline / deadlines / sessions / chat."""
    from .workspace import Workspace

    if args.ws_cmd == "new":
        ws = Workspace.create(args.slug, args.title, client=args.client or "", as_of=args.as_of,
                              root=args.root, jurisdiction=args.jurisdiction,
                              confidentiality=args.confidentiality, provider=args.provider)
        print(f"дело создано: {ws.path} (as_of {ws.manifest.as_of}); задача — notes/задача.md, "
              f"документы кладите в inbox/")
        return 0
    if args.ws_cmd == "list":
        for m in Workspace.list_all(args.root):
            print(f"{m['slug']:24s} {m['title']} — {m.get('client') or '—'} (as_of {m['as_of']})")
        return 0
    ws = Workspace.open(args.slug, args.root)
    if args.ws_cmd == "files":
        for f in ws.list_files():
            print(f"{f['owner']:6s} {f['size']:>8}  {f['path']}")
        for t in ws.tasks():
            print(f"задача #{t['id']} [{t['status']}] {t['title']}" + (f" до {t['due']}" if t.get("due") else ""))
        return 0
    if args.ws_cmd == "add":
        for f in args.files:
            print("добавлен:", ws.add_file(f, args.dest))
        return 0
    if args.ws_cmd in ("facts", "add-fact", "confirm-fact", "timeline", "deadlines"):
        return _facts(args, ws)
    if args.ws_cmd in ("notifications", "apply", "changes"):
        from .db import connect
        from .monitor import apply_notification, notifications, render_change, set_status, what_changed
        conn = connect(getattr(args, "db_url", None))
        try:
            if args.ws_cmd == "notifications":
                rows = notifications(conn, args.slug, args.status, 100)
                for n in rows:
                    print(f"#{n['notification_id']:<4} {n['status']:7s} {n['kind']:24s} {n['unit_id'] or n['doc_id'] or ''}  "
                          f"файлы: {', '.join(n['paths'] or [])}")
                if not rows:
                    print("уведомлений нет")
            elif args.ws_cmd == "changes":
                print(render_change(what_changed(conn, args.unit_id, args.since, ws.manifest.as_of)))
            else:
                if args.seen:
                    print(set_status(conn, args.id, "seen"))
                    return 0
                corpus, _conn2 = _open_corpus(args)
                agent = _make_agent(args, corpus, ws)
                r = apply_notification(conn, ws, agent, args.id)
                print(f"сессия {r['session_id']} ({r['status']}), файлы: {', '.join(r['files_written']) or '—'}\n\n{r['text']}")
        finally:
            conn.close()
        return 0
    if args.ws_cmd == "draft":
        from .deadlines import ProductionCalendar
        from .templates import draft_from_template
        values = dict(kv.split("=", 1) for kv in (args.set or []))
        r = draft_from_template(ws, args.template, args.path, values, ProductionCalendar.load())
        print(f"черновик {r['path']} (v{r['version']}): заполнено {len(r['filled'])}, пропуски: "
              f"{', '.join(r['missing']) or 'нет'}; секций для агента: {len(r['agent_sections'])}")
        return 0
    if args.ws_cmd == "export":
        from .export import export_workspace_file
        data = export_workspace_file(ws, args.path, args.reference)
        out = Path(args.out or (Path(args.path).stem + ".docx"))
        out.write_bytes(data)
        print(f"DOCX: {out} ({len(data)} байт)")
        return 0
    from .case_session import CaseSession
    if args.ws_cmd == "sessions":
        for s in CaseSession.list_sessions(ws):
            print(f"{s['session_id']}  {s['status']:12s} ходов: {s['turns']}  обновлено {s['updated_at']}")
        return 0
    if args.ws_cmd == "chat":
        return _chat(args, ws)
    return 1


def _facts(args: argparse.Namespace, ws) -> int:
    """Факты и таймлайн дела (F6)."""
    from .facts import FactError, FactStore
    store = FactStore(ws)
    if args.ws_cmd == "facts":
        facts = store.list(kind=args.kind)
        if getattr(args, "json", False):
            print(json.dumps(facts, ensure_ascii=False, indent=2))
            return 0
        for f in facts:
            mark = "✓" if f["confirmed"] else "?"
            src = f" ← {f['source_path']}" if f.get("source_path") else ""
            role = f" [{f['role']}]" if f.get("role") else ""
            print(f"{mark} #{f['fact_id']:<3} {f['kind']:7s} {str(f['value']):24s} {f['text']}{role}{src}")
        if not facts:
            print("фактов нет")
        return 0
    if args.ws_cmd == "add-fact":
        try:
            fact = store.add(args.kind, args.value, args.text, args.source, args.quote, args.page, args.role,
                             extracted_by="lawyer")
        except FactError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return 2
        print(f"факт #{fact.fact_id} добавлен: {fact.kind} {fact.value} — {fact.text}")
        return 0
    if args.ws_cmd == "confirm-fact":
        try:
            fact = store.confirm(args.id, not args.revoke)
        except FactError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return 2
        print(f"факт #{fact.fact_id}: {'подтверждён' if fact.confirmed else 'подтверждение снято'}")
        return 0
    if args.ws_cmd == "timeline":
        for t in store.timeline(args.confirmed_only):
            end = f" — {t['end']}" if t.get("end") else ""
            print(f"{t['date']}{end}  {'✓' if t['confirmed'] else '?'} {t['text']}" + (f" [{t['role']}]" if t.get("role") else ""))
        return 0
    if args.ws_cmd == "deadlines":
        from .deadlines import ProductionCalendar
        out = store.derive_deadlines(ProductionCalendar.load(), args.confirmed_only, not args.no_tasks)
        if getattr(args, "json", False):
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        for d in out["deadlines"]:
            print(f"до {d['due']}  {d['title']}")
        if out.get("limitation"):
            lim = out["limitation"]
            print(f"давность: три года истекают {lim['limitation_ends']}; решение {lim['decision_date']} — "
                  f"{'за пределами срока' if lim['expired'] else 'в пределах срока'}")
        for step in out["steps"]:
            print("  ·", step)
        for m in out["missing"]:
            print("не хватает:", m)
        if out["tasks"]:
            print(f"поставлено задач: {len(out['tasks'])}")
        return 0
    return 1


def _chat(args: argparse.Namespace, ws) -> int:
    from .case_session import CaseSession

    corpus, conn = _open_corpus(args)
    try:
        if conn is not None:
            from .db import latest_snapshot
            snap = latest_snapshot(conn)
            if snap and ws.manifest.corpus_snapshot != snap:
                ws.manifest.corpus_snapshot = snap  # номер снимка корпуса попадает в шапки файлов агента
                ws.save_manifest()
        agent = _make_agent(args, corpus, ws)
        session = CaseSession.load(ws, agent, args.session) if args.session else CaseSession(ws, agent)
        print(f"дело «{ws.manifest.title}», сессия {session.session_id}, модель {agent.model}, "
              f"as_of {ws.manifest.as_of}. Команды: /files /tasks /quit")
        if session.status == "waiting_user" and session.pending:
            print(f"\n[вопрос агента] {session.pending['question']}")
            if session.pending.get("options"):
                print("   варианты: " + " | ".join(session.pending["options"]))

        def show(turn) -> None:
            if turn.kind == "question":
                print(f"\n[вопрос агента] {turn.question['question']}")
                if turn.question.get("options"):
                    print("   варианты: " + " | ".join(turn.question["options"]))
            else:
                print("\n" + turn.text)
                if turn.verification is not None:
                    print("\n" + turn.verification.render())
                if turn.files_written:
                    print("файлы агента: " + ", ".join(sorted(set(turn.files_written))))

        def sync() -> None:
            if conn is not None:
                from .workspace_store import sync_workspace, upsert_session
                info = sync_workspace(conn, ws)
                upsert_session(conn, info["workspace_id"], session)

        if args.message:
            show(session.send(args.message))
            sync()
            return 0 if session.status != "waiting_user" else 3
        while True:
            try:
                line = input("\nюрист> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line == "/quit":
                break
            if line == "/files":
                for f in ws.list_files():
                    print(f"  {f['owner']:6s} {f['path']}")
                continue
            if line == "/tasks":
                for t in ws.tasks():
                    print(f"  #{t['id']} [{t['status']}] {t['title']}")
                continue
            show(session.send(line))
            sync()
        session.save()
        sync()
        print(f"сессия сохранена: {session.path}")
        return 0
    finally:
        if conn is not None:
            conn.close()


def cmd_audit(args: argparse.Namespace) -> int:
    """Аудит документа (F1): статус каждой ссылки, правки после даты документа, снятые письма."""
    from .audit import audit_text, render_markdown
    from .textract import extract_text

    text = extract_text(args.file)
    corpus, conn = _open_corpus(args)
    try:
        report = audit_text(corpus, text, args.as_of, args.doc_date)
    finally:
        if conn is not None:
            conn.close()
    md = render_markdown(report)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"отчёт: {args.out}")
    print(md)
    return 0 if report.ok else 2


def cmd_patch(args: argparse.Namespace) -> int:
    """Изменяющий закон -> патчи -> новые версии unit_text (F3); --dry-run — только разбор и проверка."""
    from .db import connect
    from .patcher import apply_law_to_db

    text = Path(args.law).read_text(encoding="utf-8")
    conn = connect(args.db_url)
    try:
        if args.queue:
            rows = conn.execute("SELECT p.patch_id, a.number, p.operation, p.target_unit_id, p.applied_status, p.reason, p.effective_date "
                                "FROM patch p LEFT JOIN amending_act a ON a.amending_act_id = p.amending_act_id "
                                "WHERE p.applied_status = 'failed' ORDER BY p.patch_id DESC LIMIT 100").fetchall()
            for r in rows:
                print(f"#{r['patch_id']:<5} {r['number'] or '?':10s} {r['operation']:16s} {r['target_unit_id'] or '—':28s} {r['reason']}")
            print(f"в очереди сверки: {len(rows)}")
            return 0
        r = apply_law_to_db(conn, args.act, text, args.number, args.date, args.effective, args.published,
                            dry_run=args.dry_run, source_url=args.source)
    finally:
        conn.close()
    print(f"закон {r['number']}: инструкций {r['instructions']}, применимо {r['ok']}, дата вступления "
          f"{r['effective_date'] or '—'} ({r['effective_note']}); статус {r['status']}, записано версий {r['applied']}")
    for x in r["results"]:
        print(f"  {x['status']:7s} {x['operation']:16s} {x['unit_id'] or '—'}" + (f"  {x['reason']}" if x.get("reason") else ""))
        if args.verbose and x.get("diff"):
            print("    " + x["diff"].replace("\n", "\n    ")[:1500])
    return 0 if not r["failed"] else 3


def cmd_load_regions(args: argparse.Namespace) -> int:
    """Региональные параметры (F10) из JSON -> parameter/regional_act."""
    from .db import connect, load_regional_parameters
    rows = json.loads(Path(args.file).read_text(encoding="utf-8"))["parameters"]
    conn = connect(args.db_url)
    try:
        n = load_regional_parameters(conn, rows)
    finally:
        conn.close()
    print(f"региональных параметров загружено: {n} (регионы: {sorted({r['region'] for r in rows})})")
    return 0


def cmd_users(args: argparse.Namespace) -> int:
    """Пользователи, токены и роли в делах (P5): add / list / token / tokens / revoke-token / grant / revoke / members."""
    from .auth import AuthError, UserStore
    store = UserStore(args.users_file)
    try:
        if args.users_cmd == "add":
            u = store.add_user(args.email, args.name or args.email, admin=args.admin)
            print(f"пользователь {u['email']} ({u['user_id']}){' admin' if u['admin'] else ''}")
        elif args.users_cmd == "list":
            for u in store.users():
                print(f"{u['user_id']:14s} {u['email']:32s} {u['name']}{' [admin]' if u.get('admin') else ''}")
        elif args.users_cmd == "token":
            token = store.issue_token(args.email, args.label or "")
            print("токен (показывается один раз, хранится sha256):", token)
        elif args.users_cmd == "tokens":
            for t in store.tokens(args.email):
                print(f"{t['token_id']}  {t['user_id']}  {t.get('label') or ''}  создан {t['created_at']}  "
                      f"{'ОТОЗВАН' if t['revoked'] else 'активен'}  последний вход {t.get('last_used_at') or '—'}")
        elif args.users_cmd == "revoke-token":
            print("отозван" if store.revoke_token(args.token_id) else "не найден")
        elif args.users_cmd == "grant":
            m = store.grant(args.slug, args.email, args.role)
            print(f"{args.email}: {m['role']} в деле {args.slug}")
        elif args.users_cmd == "revoke":
            print("доступ снят" if store.revoke(args.slug, args.email) else "не был участником")
        elif args.users_cmd == "members":
            for m in store.members(args.slug):
                print(f"{m['role']:7s} {m['email']}  {m['name']}")
        else:
            return 1
    except AuthError as exc:
        print(f"ошибка: {exc.detail}", file=sys.stderr)
        return 2
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    """Задачи и расписание (P3): list / run / history / cron."""
    from . import jobs as J

    if args.jobs_cmd == "list":
        for name, (desc, _) in sorted(J.JOBS.items()):
            print(f"{name:20s} {desc}")
        return 0
    if args.jobs_cmd == "cron":
        print(J.CRONTAB.format(root=J.ROOT, python=sys.executable))
        return 0
    if args.jobs_cmd == "history":
        from .db import connect
        conn = connect(args.db_url)
        try:
            for r in J.history(conn, args.name, args.limit):
                print(f"#{r['run_id']:<5} {r['name']:20s} {r['status']:7s} {r['started_at']:%Y-%m-%d %H:%M} "
                      f"{(r['error'] or '')[:80]}")
        finally:
            conn.close()
        return 0
    result = J.run_job(args.name, args.job_args)
    print(f"[{result['status']}] {result['name']} за {result['seconds']} с; "
          f"stats: {json.dumps(result['stats'], ensure_ascii=False, default=str)[:400]}")
    if result["error"]:
        print(result["error"], file=sys.stderr)
    return 0 if result["status"] == "ok" else 1


def cmd_calc(args: argparse.Namespace) -> int:
    """Калькуляторы с цитатами (F5): аргументы — JSON или key=value."""
    from .deadlines import ProductionCalendar
    from .tools import run_calculator

    params: dict = {}
    for item in args.params:
        if item.startswith("{"):
            params.update(json.loads(item))
        elif "=" in item:
            k, v = item.split("=", 1)
            try:
                params[k] = json.loads(v)      # числа, true/false, null, списки
            except json.JSONDecodeError:
                params[k] = v                  # строки и даты как есть
    try:
        result = run_calculator(args.name, params, ProductionCalendar.load())
    except (ValueError, KeyError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"{result['name']}: {json.dumps(result['value'], ensure_ascii=False)}")
    for step in result["steps"]:
        print(f"  - {step}")
    print("нормы: " + ", ".join(result["applied_units"]))
    for w in result["warnings"]:
        print(f"[внимание] {w}")
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
    p_search.add_argument("--hybrid", action="store_true",
                          help="лексический + семантический (RRF); нужен индекс: embed")
    p_search.add_argument("--data-dir", default="data/processed")
    p_search.add_argument("--db-url", default=None)
    p_search.set_defaults(func=cmd_search)

    p_embed = sub.add_parser("embed", help="построить семантический индекс чанков (extra [semantic])")
    p_embed.add_argument("--model", default="intfloat/multilingual-e5-small")
    p_embed.add_argument("--data-dir", default="data/processed")
    p_embed.add_argument("--max-chars", type=int, default=2000)
    p_embed.add_argument("--to-db", action="store_true", help="загрузить векторы в unit_embedding (нужен pgvector)")
    p_embed.add_argument("--rebuild", action="store_true", help="пересчитать даже если npz уже есть")
    p_embed.add_argument("--db-url", default=None)
    p_embed.set_defaults(func=cmd_embed)

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
        p_param.add_argument("--region", default=None, help="код субъекта РФ для региональной ставки (F10)")
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
    p_ask.add_argument("--model", default=None,
                       help="по умолчанию TAXCORPUS_MODEL из .env, иначе claude-opus-5")
    p_ask.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p_ask.add_argument("--local", action="store_true", help="офлайн-корпус из JSONL вместо БД")
    p_ask.add_argument("--data-dir", default="data/processed")
    p_ask.add_argument("--db-url", default=None)
    p_ask.add_argument("--calendar-dir", default=None)
    p_ask.add_argument("--provider", default=None, help="имя профиля из config/providers.json")
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

    p_ws = sub.add_parser("workspace", help="рабочее пространство дела (docs/workspace-plan.md)")
    p_ws.add_argument("--root", default="workspaces")
    ws_sub = p_ws.add_subparsers(dest="ws_cmd", required=True)
    w_new = ws_sub.add_parser("new", help="создать дело")
    w_new.add_argument("--slug", required=True)
    w_new.add_argument("--title", required=True)
    w_new.add_argument("--client", default=None)
    w_new.add_argument("--as-of", default=None, help="дата, на которую берутся нормы")
    w_new.add_argument("--jurisdiction", default=None)
    w_new.add_argument("--confidentiality", default="standard", choices=["standard", "sensitive"],
                       help="sensitive: локальный профиль модели или маскировка ПДн (F9)")
    w_new.add_argument("--provider", default=None, help="имя профиля из config/providers.json")
    ws_sub.add_parser("list", help="список дел")
    w_files = ws_sub.add_parser("files", help="файлы и задачи дела")
    w_files.add_argument("--slug", required=True)
    w_add = ws_sub.add_parser("add", help="добавить файлы юриста в дело")
    w_add.add_argument("--slug", required=True)
    w_add.add_argument("--dest", default="inbox", choices=["inbox", "notes"])
    w_add.add_argument("files", nargs="+")
    w_facts = ws_sub.add_parser("facts", help="таблица фактов дела (F6)")
    w_facts.add_argument("--slug", required=True)
    w_facts.add_argument("--kind", default=None)
    w_facts.add_argument("--json", action="store_true")
    w_af = ws_sub.add_parser("add-fact", help="добавить факт юриста")
    w_af.add_argument("--slug", required=True)
    w_af.add_argument("--kind", required=True, choices=["date", "amount", "party", "event", "period", "regime", "other"])
    w_af.add_argument("--value", required=True)
    w_af.add_argument("--text", required=True, help="подпись факта")
    w_af.add_argument("--role", default=None, help="act_received | decision_received | decision_date | …")
    w_af.add_argument("--source", default=None, help="файл дела")
    w_af.add_argument("--quote", default=None, help="дословная цитата из файла")
    w_af.add_argument("--page", type=int, default=None)
    w_cf = ws_sub.add_parser("confirm-fact", help="подтвердить факт агента")
    w_cf.add_argument("--slug", required=True)
    w_cf.add_argument("--id", type=int, required=True)
    w_cf.add_argument("--revoke", action="store_true")
    w_tl = ws_sub.add_parser("timeline", help="таймлайн дела")
    w_tl.add_argument("--slug", required=True)
    w_tl.add_argument("--confirmed-only", action="store_true")
    w_dl = ws_sub.add_parser("deadlines", help="сроки процедуры и давность из фактов -> задачи")
    w_dl.add_argument("--slug", required=True)
    w_dl.add_argument("--confirmed-only", action="store_true")
    w_dl.add_argument("--no-tasks", action="store_true")
    w_dl.add_argument("--json", action="store_true")
    w_nt = ws_sub.add_parser("notifications", help="уведомления мониторинга по делу (F2)")
    w_nt.add_argument("--slug", required=True); w_nt.add_argument("--status", default=None); w_nt.add_argument("--db-url", default=None)
    w_ap = ws_sub.add_parser("apply", help="применить уведомление: агент обновит задетые файлы")
    w_ap.add_argument("--slug", required=True); w_ap.add_argument("--id", type=int, required=True)
    w_ap.add_argument("--seen", action="store_true", help="только отметить прочитанным")
    w_ap.add_argument("--db-url", default=None); w_ap.add_argument("--model", default=None); w_ap.add_argument("--provider", default=None)
    w_ap.add_argument("--effort", default="high"); w_ap.add_argument("--local", action="store_true"); w_ap.add_argument("--data-dir", default="data/processed")
    w_ch = ws_sub.add_parser("changes", help="что изменилось в норме (diff редакций, правки, документы)")
    w_ch.add_argument("--slug", required=True); w_ch.add_argument("--unit-id", required=True); w_ch.add_argument("--since", default=None)
    w_ch.add_argument("--db-url", default=None)
    w_draft = ws_sub.add_parser("draft", help="черновик документа по шаблону (F7)")
    w_draft.add_argument("--slug", required=True)
    w_draft.add_argument("--template", required=True, help="имя из templates/*.md")
    w_draft.add_argument("--path", required=True, help="имя файла в drafts/")
    w_draft.add_argument("--set", action="append", help="значение плейсхолдера: authority=ИФНС № 1")
    w_export = ws_sub.add_parser("export", help="файл агента -> DOCX (F7)")
    w_export.add_argument("--slug", required=True)
    w_export.add_argument("--path", required=True)
    w_export.add_argument("--out", default=None)
    w_export.add_argument("--reference", default=None, help="docx со стилями фирмы")
    w_sess = ws_sub.add_parser("sessions", help="сессии дела")
    w_sess.add_argument("--slug", required=True)
    w_chat = ws_sub.add_parser("chat", help="диалог с агентом в деле (REPL или --message)")
    w_chat.add_argument("--slug", required=True)
    w_chat.add_argument("--session", default=None, help="продолжить сессию по id")
    w_chat.add_argument("--message", default=None, help="один ход без REPL (код 3 — агент ждёт ответа)")
    w_chat.add_argument("--model", default=None)
    w_chat.add_argument("--provider", default=None, help="профиль провайдера для этого запуска")
    w_chat.add_argument("--effort", default="high")
    w_chat.add_argument("--local", action="store_true")
    w_chat.add_argument("--data-dir", default="data/processed")
    w_chat.add_argument("--db-url", default=None)
    p_ws.set_defaults(func=cmd_workspace)

    p_audit = sub.add_parser("audit", help="аудит документа: ссылки на нормы и письма (F1)")
    p_audit.add_argument("--file", required=True, help="docx/pdf/md/txt")
    p_audit.add_argument("--as-of", default=date.today().isoformat())
    p_audit.add_argument("--doc-date", default=None, help="дата документа: показать правки после неё")
    p_audit.add_argument("--out", default=None, help="куда записать отчёт (md)")
    p_audit.add_argument("--local", action="store_true")
    p_audit.add_argument("--data-dir", default="data/processed")
    p_audit.add_argument("--db-url", default=None)
    p_audit.set_defaults(func=cmd_audit)

    p_lr = sub.add_parser("load-regions", help="региональные ставки и льготы из JSON -> БД (F10)")
    p_lr.add_argument("--file", default="data/parameters/regional.json")
    p_lr.add_argument("--db-url", default=None)
    p_lr.set_defaults(func=cmd_load_regions)

    p_patch = sub.add_parser("patch", help="изменяющий закон -> версии норм с даты вступления (F3)")
    p_patch.add_argument("--act", default="nk1", help="nk1 | nk2")
    p_patch.add_argument("--law", required=True, help="текстовый файл закона")
    p_patch.add_argument("--number", default="?", help="«281-ФЗ»")
    p_patch.add_argument("--date", default=None, help="дата принятия YYYY-MM-DD")
    p_patch.add_argument("--published", default=None, help="дата опубликования (для отсчёта вступления)")
    p_patch.add_argument("--effective", default=None, help="дата вступления в силу, если известна")
    p_patch.add_argument("--source", default=None)
    p_patch.add_argument("--dry-run", action="store_true")
    p_patch.add_argument("--queue", action="store_true", help="показать очередь сверки (failed)")
    p_patch.add_argument("--verbose", action="store_true")
    p_patch.add_argument("--db-url", default=None)
    p_patch.set_defaults(func=cmd_patch)

    p_users = sub.add_parser("users", help="пользователи, токены, роли в делах (P5)")
    p_users.add_argument("--users-file", default=None, help="config/users.json по умолчанию")
    u_sub = p_users.add_subparsers(dest="users_cmd", required=True)
    u_add = u_sub.add_parser("add"); u_add.add_argument("--email", required=True); u_add.add_argument("--name", default=None)
    u_add.add_argument("--admin", action="store_true")
    u_sub.add_parser("list")
    u_tok = u_sub.add_parser("token", help="выпустить токен"); u_tok.add_argument("--email", required=True); u_tok.add_argument("--label", default=None)
    u_toks = u_sub.add_parser("tokens"); u_toks.add_argument("--email", default=None)
    u_rt = u_sub.add_parser("revoke-token"); u_rt.add_argument("--token-id", required=True)
    u_gr = u_sub.add_parser("grant", help="роль в деле"); u_gr.add_argument("--slug", required=True); u_gr.add_argument("--email", required=True)
    u_gr.add_argument("--role", default="editor", choices=["viewer", "editor", "owner"])
    u_rv = u_sub.add_parser("revoke"); u_rv.add_argument("--slug", required=True); u_rv.add_argument("--email", required=True)
    u_mb = u_sub.add_parser("members"); u_mb.add_argument("--slug", required=True)
    p_users.set_defaults(func=cmd_users)

    p_jobs = sub.add_parser("jobs", help="задачи и расписание: list / run / history / cron (P3)")
    jobs_sub = p_jobs.add_subparsers(dest="jobs_cmd", required=True)
    jobs_sub.add_parser("list")
    jobs_sub.add_parser("cron")
    j_hist = jobs_sub.add_parser("history")
    j_hist.add_argument("--name", default=None)
    j_hist.add_argument("--limit", type=int, default=20)
    j_hist.add_argument("--db-url", default=None)
    j_run = jobs_sub.add_parser("run")
    j_run.add_argument("name")
    j_run.add_argument("job_args", nargs=argparse.REMAINDER, help="аргументы задачи после --")
    p_jobs.set_defaults(func=cmd_jobs)

    p_calc = sub.add_parser("calc", help="калькуляторы с цитатами: compute_penalty / compute_fine / "
                                         "appeal_deadlines / limitation_status (F5)")
    p_calc.add_argument("name")
    p_calc.add_argument("params", nargs="*", help="key=value или JSON-объект")
    p_calc.set_defaults(func=cmd_calc)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "job_args", None) and args.job_args[:1] == ["--"]:
        args.job_args = args.job_args[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
