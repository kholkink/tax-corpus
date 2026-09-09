"""Отчёт по partial-ссылкам: материал для ручной/LLM-сверки (слой 2а плана).

Запуск: python scripts/report_partial.py
Выход:  reports/partial_references.md
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "data/processed/nk1_references.jsonl"
OUT = ROOT / "reports/partial_references.md"


def main() -> int:
    rows = [json.loads(line) for line in REFS.read_text(encoding="utf-8").splitlines() if line]
    partial = [r for r in rows if r.get("status") == "partial"]
    if not partial:
        OUT.write_text("partial-ссылок нет.\n", encoding="utf-8")
        print("partial-ссылок нет")
        return 0

    # классификация причин по пометке резолюции
    by_cause: dict[str, list[dict]] = defaultdict(list)
    for r in partial:
        note = r.get("resolution_note") or ""
        if "подпункт" in note:
            cause = "подпункт не существует в пункте"
        elif "абзац" in note:
            cause = "абзац не существует в единице"
        elif "пункт" in note:
            cause = "пункт не существует в статье"
        else:
            cause = note or "прочее"
        by_cause[cause].append(r)

    lines = [
        "# Partial-ссылки: очередь сверки",
        "",
        f"Всего ссылок: {len(rows)}; partial: {len(partial)} "
        f"({len(partial) / len(rows):.1%}). Частично разрешённые ссылки указывают "
        "на существующую статью/пункт, но цитируемый уровень в ней не найден.",
        "",
    ]

    explanations = {
        "абзац не существует в единице":
            "Цитата «абзац N» адресует построчный текст статьи, а абзацные единицы "
            "создаются только для текста вне пунктов. Задача: абзацная нумерация "
            "всех статей (следующий шаг парсера).",
        "подпункт не существует в пункте":
            "Чаще всего «висячая ссылка» кодекса: цитата написана под редакцию, в "
            "которой подпункт существовал. Проверять по списку редакций.",
        "пункт не существует в статье":
            "Аналогично — цитата на пункт из старой редакции либо артефакт подачи "
            "банка. Проверять по списку редакций.",
    }

    for cause, group in sorted(by_cause.items(), key=lambda kv: -len(kv[1])):
        uniq_citations = Counter(r["raw_citation"].strip() for r in group)
        lines.append(f"## {cause} — {len(group)} ссылок, {len(uniq_citations)} уникальных цитат")
        lines.append("")
        if cause in explanations:
            lines.append(f"**Гипотеза:** {explanations[cause]}")
            lines.append("")
        lines.append("| цитата | откуда | куда указывает |")
        lines.append("|---|---|---|")
        # топ уникальных цитат с одним примером-источником
        seen: dict[str, dict] = {}
        for r in group:
            seen.setdefault(r["raw_citation"].strip(), r)
        for citation, r in sorted(seen.items(), key=lambda kv: -1)[:0] or \
                list(sorted(seen.items()))[:25]:
            lines.append(f"| {citation} | {r['from_unit_id']} | {r.get('to_unit_id')} |")
        if len(seen) > 25:
            lines.append(f"| … | ещё {len(seen) - 25} уникальных цитат | |")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"написано: {OUT}; partial: {len(partial)} из {len(rows)}")
    print("по причинам:", {k: len(v) for k, v in by_cause.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
