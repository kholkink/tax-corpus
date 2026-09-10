"""Шаблоны документов (F7 плана ПО): markdown с плейсхолдерами и секциями для агента.

templates/<name>.md: шапка (name, title, description, facts — роли фактов, которые нужны),
плейсхолдеры {{facts.<role>|запасное значение}}, {{manifest.<поле>}}, {{deadlines.<ключ>}},
{{today}}, {{<любое имя>|запасное}} (значение из аргументов), секции агента —
HTML-комментарии <!-- agent: инструкция -->. Рендер детерминирован: значения берутся из
подтверждённых фактов дела (по роли) и derive_deadlines; чего нет — запасное значение или
пометка [[нет факта: role]] и запись в missing. Секции агента остаются комментариями:
их заполняет модель через write_file, проверка цитат — как у любого файла агента.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
RE_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)
RE_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*(?:\|([^}]*))?\}\}")
RE_AGENT = re.compile(r"<!--\s*agent:\s*(.*?)\s*-->", re.S)


@dataclass
class Template:
    name: str
    title: str
    description: str
    facts: list[str]
    body: str
    path: Path

    def summary(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description, "facts": self.facts,
                "placeholders": sorted({m.group(1) for m in RE_PLACEHOLDER.finditer(self.body)}),
                "agent_sections": len(RE_AGENT.findall(self.body))}


@dataclass
class Rendered:
    text: str
    filled: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    agent_sections: list[str] = field(default_factory=list)


def _parse_front(text: str) -> tuple[dict, str]:
    m = RE_FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def load_template(name_or_path: str | Path, root: str | Path | None = None) -> Template:
    p = Path(name_or_path)
    if not p.suffix:
        p = Path(root or TEMPLATES_DIR) / f"{name_or_path}.md"
    if not p.exists():
        raise FileNotFoundError(f"нет шаблона {name_or_path}; есть: {', '.join(t.name for t in list_templates(root))}")
    meta, body = _parse_front(p.read_text(encoding="utf-8"))
    facts = [x.strip() for x in (meta.get("facts") or "").split(",") if x.strip()]
    return Template(meta.get("name") or p.stem, meta.get("title") or p.stem, meta.get("description") or "",
                    facts, body.strip("\n") + "\n", p)


def list_templates(root: str | Path | None = None) -> list[Template]:
    d = Path(root or TEMPLATES_DIR)
    return [load_template(p, d) for p in sorted(d.glob("*.md"))] if d.exists() else []


def _fmt_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def _fmt_value(kind: str, value) -> str:
    if kind in ("date", "event"):
        return _fmt_date(value)
    if kind == "period":
        a, b = str(value).split("..")
        return f"{_fmt_date(a)} — {_fmt_date(b)}"
    if kind == "amount":
        return f"{float(value):,.2f}".replace(",", " ").replace(".", ",") + " руб."
    return str(value)


def render(template: Template, manifest: dict, facts: list[dict], deadlines: dict | None = None,
           values: dict | None = None, confirmed_only: bool = True, today: date | None = None) -> Rendered:
    """Подставляет плейсхолдеры; неподтверждённые факты по умолчанию не используются."""
    by_role: dict[str, dict] = {}
    for f in facts:
        if f.get("role") and (f.get("confirmed") or not confirmed_only) and f["role"] not in by_role:
            by_role[f["role"]] = f
    dl = {d["key"]: d["due"] for d in (deadlines or {}).get("deadlines", [])} if deadlines else {}
    values = values or {}
    out = Rendered(text="")
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        key, fallback = m.group(1), m.group(2)
        val = None
        if key == "today":
            val = (today or date.today()).strftime("%d.%m.%Y")
        elif key.startswith("facts."):
            role = key[6:]
            if role in values:
                val = str(values[role])
            elif role in by_role:
                val = _fmt_value(by_role[role]["kind"], by_role[role]["value"])
        elif key.startswith("manifest."):
            v = manifest.get(key[9:])
            val = _fmt_date(v) if key == "manifest.as_of" and v else (str(v) if v else None)
        elif key.startswith("deadlines."):
            v = dl.get(key[10:])
            val = _fmt_date(v) if v else None
        elif key in values:
            val = str(values[key])
        if val is None or val == "":
            if key not in missing:
                missing.append(key)
            return fallback.strip() if fallback is not None else f"[[нет: {key}]]"
        out.filled[key] = val
        return val

    out.text = RE_PLACEHOLDER.sub(sub, template.body)
    out.missing = missing
    out.agent_sections = [s.strip() for s in RE_AGENT.findall(template.body)]
    return out


def draft_from_template(ws, name: str, path: str, values: dict | None = None, calendar=None,
                        root: str | Path | None = None) -> dict:
    """Черновик в drafts/ из шаблона с фактами дела; возвращает список секций для агента и пробелы."""
    from .facts import FactStore
    tpl = load_template(name, root)
    store = FactStore(ws)
    deadlines = None
    try:
        deadlines = store.derive_deadlines(calendar, confirmed_only=True, create_tasks=False)
    except Exception:  # noqa: BLE001 — без дат сроков просто нет
        deadlines = None
    rendered = render(tpl, ws.manifest.to_dict(), store.list(), deadlines, values)
    if not path.startswith("drafts/"):
        path = "drafts/" + path
    if not path.endswith(".md"):
        path += ".md"
    result = ws.write_file(path, rendered.text, {"summary": f"черновик по шаблону {tpl.name}", "template": tpl.name,
                                                 "missing": rendered.missing})
    return {**result, "template": tpl.name, "filled": rendered.filled, "missing": rendered.missing,
            "agent_sections": rendered.agent_sections,
            "note": "заполни секции <!-- agent: … --> и перезапиши файл через write_file; "
                    "пропуски [[нет: …]] и запасные значения ________ — уточни у юриста (ask_user) или add_fact"}


def template_tools() -> list[dict]:
    names = [t.name for t in list_templates()]
    return [{
        "name": "draft_document",
        "description": "Создать черновик документа в drafts/ по шаблону фирмы: плейсхолдеры заполняются "
                       "подтверждёнными фактами дела (по ролям) и сроками; возвращает список секций "
                       "<!-- agent: … -->, которые нужно написать, и пропуски. Затем прочитай файл, заполни "
                       "секции и перезапиши через write_file (цитаты проверяются). Шаблоны: " + ", ".join(names) + ".",
        "input_schema": {"type": "object",
                         "properties": {"template": {"type": "string", "enum": names or ["меморандум"]},
                                        "path": {"type": "string", "description": "имя файла в drafts/, напр. возражения.md"},
                                        "values": {"type": "object", "description": "значения плейсхолдеров без фактов "
                                                   "(authority, signer, topic …)", "additionalProperties": {"type": "string"}}},
                         "required": ["template", "path", "values"], "additionalProperties": False},
        "strict": True,
    }]


TEMPLATE_TOOLS: list[dict] = template_tools()
TEMPLATE_TOOL_NAMES = {t["name"] for t in TEMPLATE_TOOLS}
