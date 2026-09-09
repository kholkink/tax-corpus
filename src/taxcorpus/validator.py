"""Валидатор иерархии структурных единиц.

Проверяет то, что нельзя понять по отдельной записи: уникальность ID,
существование родителей, сквозную последовательность номеров статей,
последовательность пунктов и подпунктов, непустоту текстов.
Результат — отчёт с issue-списком: error | warning | info.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import number_key

# какой родитель ожидается у каждого вида единицы
EXPECTED_PARENT = {
    "part": {None},
    "section": {"part"},
    "subsection": {"section"},
    "chapter": {"part", "section", "subsection"},
    "article": {"chapter", "section", "subsection", "part"},
    "point": {"article"},
    "subpoint": {"point"},
    "paragraph": {"article", "point", "subpoint"},
}

# единицы, обязанные иметь непустой текст
TEXT_REQUIRED = {"article", "point", "subpoint", "paragraph"}


@dataclass
class Issue:
    severity: str  # error | warning | info
    rule: str
    unit_id: str | None
    message: str


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def render_markdown(self, title: str = "Отчёт валидации корпуса") -> str:
        lines = [f"# {title}", ""]
        lines.append("## Состав корпуса")
        lines.append("")
        lines.append("| вид единицы | количество |")
        lines.append("|---|---|")
        for kind, count in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {kind} | {count} |")
        lines.append("")
        lines.append(
            f"**Итог: {len(self.errors)} ошибок, {len(self.warnings)} предупреждений,"
            f" {len(self.infos)} замечаний.**"
        )
        lines.append("")

        for severity, heading in (("error", "Ошибки"), ("warning", "Предупреждения"),
                                  ("info", "Замечания")):
            group = [i for i in self.issues if i.severity == severity]
            if not group:
                continue
            lines.append(f"## {heading} ({len(group)})")
            lines.append("")
            for issue in group:
                where = f" `{issue.unit_id}`" if issue.unit_id else ""
                lines.append(f"- **{issue.rule}**{where} — {issue.message}")
            lines.append("")
        return "\n".join(lines)


def validate(records: list[dict]) -> ValidationReport:
    report = ValidationReport()

    def add(severity: str, rule: str, unit_id: str | None, message: str) -> None:
        report.issues.append(Issue(severity, rule, unit_id, message))

    # --- состав корпуса ---
    for record in records:
        kind = record["kind"]
        report.counts[kind] = report.counts.get(kind, 0) + 1

    # --- уникальность unit_id ---
    seen: dict[str, dict] = {}
    for record in records:
        uid = record["unit_id"]
        if uid in seen:
            add("error", "duplicate_unit_id", uid,
                f"ID встречается второй раз (kind={record['kind']}, number={record['number']})")
        else:
            seen[uid] = record

    # --- существование и совместимость родителя ---
    for record in records:
        parent_id = record["parent_unit_id"]
        if parent_id is None:
            if record["kind"] != "part":
                add("error", "orphan_unit", record["unit_id"],
                    f"единица вида {record['kind']} без родителя")
            continue
        parent = seen.get(parent_id)
        if parent is None:
            add("error", "missing_parent", record["unit_id"],
                f"родитель {parent_id} не найден")
            continue
        allowed = EXPECTED_PARENT.get(record["kind"], set())
        if parent["kind"] not in allowed:
            add("error", "bad_parent_kind", record["unit_id"],
                f"у {record['kind']} родитель {parent['kind']}, ожидался {sorted(allowed)}")

    # --- сквозная последовательность статей ---
    prev_number: str | None = None
    prev_id: str | None = None
    for record in records:
        if record["kind"] != "article" or record["number"] is None:
            continue
        key = number_key(record["number"])
        if prev_number is not None:
            prev_key = number_key(prev_number)
            if key <= prev_key:
                add("error", "article_order", record["unit_id"],
                    f"статья {record['number']} идёт после {prev_number} (нумерация сквозная)")
            elif prev_key and key[0] > prev_key[0] + 1 and len(key) == 1 and len(prev_key) == 1:
                add("info", "article_gap", record["unit_id"],
                    f"пропуск: между статьями {prev_number} и {record['number']} нет статей"
                    " (проверьте: возможно, «утратила силу» без текста или сбой разбора)")
        prev_number, prev_id = record["number"], record["unit_id"]

    # --- последовательность пунктов в статье и подпунктов в пункте ---
    # абзацы-дети не меняют контекст нумерации, проверки сквозные; номера могут
    # быть дробными («подпункт 3.1»), поэтому сравнение по числовым ключам
    prev_point_key: tuple[int, ...] | None = None
    prev_subpoint_key: tuple[int, ...] | None = None

    for record in records:
        kind, uid = record["kind"], record["unit_id"]
        number = record["number"]
        if kind == "article":
            prev_point_key = None
            prev_subpoint_key = None
        elif kind == "point":
            prev_subpoint_key = None
            key = number_key(number) if number is not None else None
            if key and prev_point_key is not None and key <= prev_point_key:
                add("error", "point_order", uid,
                    f"пункт {number} нарушает порядок (предыдущий ключ {prev_point_key})")
            if key:
                prev_point_key = key
        elif kind == "subpoint":
            key = number_key(number) if number is not None else None
            if not key:
                add("error", "subpoint_number", uid, f"нечисловой номер подпункта: {number!r}")
                continue
            if prev_subpoint_key is not None and key <= prev_subpoint_key:
                add("error", "subpoint_order", uid,
                    f"подпункт {number} нарушает последовательность (предыдущий ключ "
                    f"{prev_subpoint_key})")
            prev_subpoint_key = key

    # --- непустой текст ---
    # статья, состоящая только из пунктов, и отменённая единица («<Утратил силу …>»
    # в edit_note) текста иметь не обязаны — это не дефект разбора
    has_children = {r["parent_unit_id"] for r in records if r["parent_unit_id"]}
    for record in records:
        if record["kind"] not in TEXT_REQUIRED or (record.get("text") or "").strip():
            continue
        if record["unit_id"] in has_children:
            continue
        note = (record.get("edit_note") or "").lower()
        if "утратил" in note:
            continue
        add("warning", "empty_text", record["unit_id"],
            f"единица вида {record['kind']} без текста")

    # --- «утратила силу» ---
    repealed = [r for r in records
                if r["kind"] == "article" and "утратил" in (r.get("text") or "").lower()]
    if repealed:
        add("info", "repealed_units", None,
            f"статей с пометкой «утратила силу»: {len(repealed)} —"
            f" {', '.join(r['unit_id'] for r in repealed[:10])}"
            + (" …" if len(repealed) > 10 else ""))

    # --- дробные номера ---
    fractional = [r for r in records
                  if r.get("number") and "." in r["number"]
                  and r["kind"] in ("article", "point")]
    if fractional:
        add("info", "fractional_numbers", None,
            f"дробные номера ({len(fractional)}): "
            + ", ".join(r["unit_id"] for r in fractional[:15])
            + (" …" if len(fractional) > 15 else ""))

    return report
