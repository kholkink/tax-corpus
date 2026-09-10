"""Рабочее пространство дела (docs/workspace-plan.md, фаза A1): папка с файлами юриста и
агента, манифест, извлечённый текст, поиск по документам дела, версии файлов агента,
задачи и вопросы.

Раскладка workspaces/<slug>/:
  workspace.json  inbox/  notes/  research/  drafts/  sessions/  index/  tasks.json
Файлы в inbox/ и notes/ агент не изменяет; research/ и drafts/ — его, с версиями в
.versions/ и шапкой провенанса (as_of, снимок корпуса, источники, отчёт проверки цитат).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .textract import extract_text

ROOT_DIR = Path("workspaces")
LAWYER_DIRS = ("inbox", "notes")
AGENT_DIRS = ("research", "drafts")
ALL_DIRS = (*LAWYER_DIRS, *AGENT_DIRS, "sessions", "index")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _stem(word: str) -> str:
    return word.lower().replace("ё", "е")[:5]


@dataclass
class Manifest:
    slug: str
    title: str
    client: str = ""
    as_of: str = field(default_factory=lambda: date.today().isoformat())
    jurisdiction: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    corpus_snapshot: int | None = None
    confidentiality: str = "standard"   # standard | sensitive (F9: локальная модель или маскировка)
    provider: str | None = None         # явный профиль провайдера (providers.py)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Workspace:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = json.loads((self.path / "workspace.json").read_text(encoding="utf-8"))
        known = {f for f in Manifest.__dataclass_fields__}
        self.manifest = Manifest(**{k: v for k, v in data.items() if k in known})

    # --- жизненный цикл ---------------------------------------------------------------
    @classmethod
    def create(cls, slug: str, title: str, client: str = "", as_of: str | None = None,
               root: str | Path = ROOT_DIR, jurisdiction: str | None = None,
               confidentiality: str = "standard", provider: str | None = None) -> "Workspace":
        if confidentiality not in ("standard", "sensitive"):
            raise ValueError("confidentiality: standard | sensitive")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,60}", slug):
            raise ValueError("slug: латиница, цифры, дефис/подчёркивание, от 2 символов")
        path = Path(root) / slug
        if path.exists():
            raise FileExistsError(f"дело {slug} уже существует: {path}")
        for d in ALL_DIRS:
            (path / d).mkdir(parents=True)
        manifest = Manifest(slug=slug, title=title, client=client,
                            as_of=as_of or date.today().isoformat(), jurisdiction=jurisdiction,
                            confidentiality=confidentiality, provider=provider)
        (path / "workspace.json").write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        (path / "tasks.json").write_text("[]", encoding="utf-8")
        (path / "notes" / "задача.md").write_text(
            f"# {title}\n\nОпишите здесь вопрос юриста, факты дела и что нужно от агента.\n",
            encoding="utf-8")
        return cls(path)

    @classmethod
    def open(cls, slug: str, root: str | Path = ROOT_DIR) -> "Workspace":
        path = Path(root) / slug
        if not (path / "workspace.json").exists():
            raise FileNotFoundError(f"дела {slug} нет в {Path(root).resolve()}")
        return cls(path)

    @staticmethod
    def list_all(root: str | Path = ROOT_DIR) -> list[dict]:
        out = []
        for p in sorted(Path(root).glob("*/workspace.json")):
            out.append(json.loads(p.read_text(encoding="utf-8")))
        return out

    def save_manifest(self) -> None:
        (self.path / "workspace.json").write_text(
            json.dumps(self.manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    # --- файлы ------------------------------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        p = (self.path / rel).resolve()
        if self.path.resolve() not in p.parents and p != self.path.resolve():
            raise PermissionError(f"путь вне дела: {rel}")
        return p

    def list_files(self, subdir: str | None = None) -> list[dict]:
        base = self._resolve(subdir) if subdir else self.path
        dirs = [base] if subdir else [self.path / d for d in (*LAWYER_DIRS, *AGENT_DIRS)]
        out = []
        for d in dirs:
            for p in sorted(d.rglob("*")):
                if p.is_file() and ".versions" not in p.parts and not p.name.startswith("."):
                    st = p.stat()
                    out.append({"path": str(p.relative_to(self.path)), "size": st.st_size,
                                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                                .isoformat(timespec="seconds"),
                                "owner": "agent" if p.relative_to(self.path).parts[0] in AGENT_DIRS else "lawyer"})
        return out

    def add_file(self, source: str | Path, dest_subdir: str = "inbox") -> str:
        if dest_subdir not in LAWYER_DIRS:
            raise ValueError("файлы юриста кладутся в inbox/ или notes/")
        src = Path(source)
        dest = self.path / dest_subdir / src.name
        shutil.copyfile(src, dest)
        return str(dest.relative_to(self.path))

    def text_of(self, rel: str) -> str:
        """Текст файла с кэшем по sha256 в index/."""
        p = self._resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        digest = _sha256(p.read_bytes())
        cache = self.path / "index" / (digest.split(":")[1] + ".txt")
        if cache.exists():
            return cache.read_text(encoding="utf-8")
        text = extract_text(p)
        cache.write_text(text, encoding="utf-8")
        return text

    def read_file(self, rel: str, offset: int = 0, max_chars: int = 12000) -> dict:
        text = self.text_of(rel)
        chunk = text[offset:offset + max_chars]
        return {"path": rel, "offset": offset, "chars": len(text), "text": chunk,
                "truncated": offset + max_chars < len(text)}

    def search_files(self, query: str, limit: int = 5, window: int = 400) -> list[dict]:
        """Грубый поиск по документам дела: совпадение основ слов, фрагмент вокруг лучшего места."""
        stems = {_stem(w) for w in re.findall(r"[а-яёa-z0-9]+", query.lower()) if len(w) > 2}
        hits = []
        for f in self.list_files():
            try:
                text = self.text_of(f["path"])
            except Exception:  # noqa: BLE001 — нечитаемый файл не ломает поиск
                continue
            low = text.lower()
            words = {_stem(w) for w in re.findall(r"[а-яёa-z0-9]+", low)}
            score = len(stems & words)
            if not score:
                continue
            first = min((low.find(s) for s in stems if low.find(s) >= 0), default=0)
            start = max(0, first - window // 2)
            hits.append({"path": f["path"], "score": score, "snippet": text[start:start + window]})
        hits.sort(key=lambda h: -h["score"])
        return hits[:limit]

    def write_file(self, rel: str, content: str, header: dict | None = None) -> dict:
        """Файл агента (research/ или drafts/) с шапкой провенанса; прежняя версия — в .versions/."""
        parts = Path(rel).parts
        if not parts or parts[0] not in AGENT_DIRS:
            raise PermissionError("агент пишет только в research/ и drafts/")
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        version = 1
        if p.exists():
            versions = p.parent / ".versions"
            versions.mkdir(exist_ok=True)
            existing = sorted(versions.glob(f"{p.name}.v*"))
            version = len(existing) + 2
            shutil.copyfile(p, versions / f"{p.name}.v{version - 1}")
        head = {"as_of": self.manifest.as_of, "corpus_snapshot": self.manifest.corpus_snapshot,
                "written_at": _now(), "version": version, **(header or {})}
        front = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in head.items()) + "\n---\n\n"
        p.write_text(front + content.strip() + "\n", encoding="utf-8")
        return {"path": rel, "version": version, "chars": len(content)}

    def append_note(self, rel: str, text: str) -> dict:
        parts = Path(rel).parts
        if not parts or parts[0] not in ("notes", *AGENT_DIRS):
            raise PermissionError("дописывать можно в notes/, research/ и drafts/")
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"\n\n<!-- агент, {_now()} -->\n{text.strip()}\n")
        return {"path": rel, "appended": len(text)}

    # --- задачи -----------------------------------------------------------------------
    def tasks(self) -> list[dict]:
        return json.loads((self.path / "tasks.json").read_text(encoding="utf-8"))

    def create_task(self, title: str, due: str | None = None, details: str = "") -> dict:
        tasks = self.tasks()
        task = {"id": len(tasks) + 1, "title": title, "due": due, "details": details,
                "status": "open", "created_at": _now()}
        tasks.append(task)
        (self.path / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        return task

    def close_task(self, task_id: int) -> None:
        tasks = self.tasks()
        for t in tasks:
            if t["id"] == task_id:
                t["status"] = "done"
        (self.path / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2),
                                              encoding="utf-8")


WORKSPACE_TOOLS: list[dict] = [
    {"name": "list_files",
     "description": "Файлы дела: inbox/ и notes/ — материалы юриста (только чтение), research/ и "
                    "drafts/ — материалы агента. Возвращает путь, размер, дату, владельца.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
     "strict": True},
    {"name": "read_file",
     "description": "Текст файла дела (docx/pdf/xlsx/md/txt). Большие файлы читай порциями: "
                    "offset — с какого символа, в ответе chars — полная длина и truncated.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "offset": {"type": "integer", "minimum": 0, "default": 0}},
                      "required": ["path"], "additionalProperties": False},
     "strict": True},
    {"name": "search_files",
     "description": "Поиск по документам дела (факты, даты, суммы, названия контрагентов). "
                    "Возвращает файл и фрагмент. Нормы права ищи не здесь, а в search.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False},
     "strict": True},
    {"name": "write_file",
     "description": "Создать или обновить файл агента в research/ (ресёрч, позиция) или drafts/ "
                    "(черновик документа), markdown. Все ссылки на нормы в тексте проверяются; "
                    "результат проверки и источники записываются в шапку файла. Прежняя версия "
                    "сохраняется.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string", "description": "research/позиция.md"},
                                     "content": {"type": "string"},
                                     "summary": {"type": "string", "description": "что изменено, 1 фраза"}},
                      "required": ["path", "content", "summary"], "additionalProperties": False},
     "strict": True},
    {"name": "append_note",
     "description": "Дописать текст в конец заметки (notes/…, research/…): протокол решений, "
                    "извлечённые факты, вопросы к юристу.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}, "text": {"type": "string"}},
                      "required": ["path", "text"], "additionalProperties": False},
     "strict": True},
    {"name": "ask_user",
     "description": "Задать юристу уточняющий вопрос, без ответа на который нельзя продолжать "
                    "(факт дела, дата, режим налогообложения, выбор варианта). Работа "
                    "приостанавливается до ответа. Задавай один конкретный вопрос за раз; если "
                    "уместны варианты — перечисли их в options.",
     "input_schema": {"type": "object",
                      "properties": {"question": {"type": "string"},
                                     "options": {"type": "array", "items": {"type": "string"}}},
                      "required": ["question"], "additionalProperties": False},
     "strict": True},
    {"name": "audit_document",
     "description": "Аудит файла дела (свой или чужой меморандум, возражения, консультация): по "
                    "каждой ссылке — существует ли норма, действует ли на дату дела, менялась ли "
                    "после даты документа, сняты ли письма по ней; какие обязательные письма ФНС не "
                    "упомянуты. Отчёт сохраняется в research/аудит-<файл>.md.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "doc_date": {"type": ["string", "null"],
                                                  "description": "дата документа YYYY-MM-DD или null"}},
                      "required": ["path", "doc_date"], "additionalProperties": False},
     "strict": True},
    {"name": "create_task",
     "description": "Поставить юристу задачу (собрать документ, запросить у клиента, проверить "
                    "факт), при необходимости с датой.",
     "input_schema": {"type": "object",
                      "properties": {"title": {"type": "string"},
                                     "due": {"type": ["string", "null"], "description": "YYYY-MM-DD или null"},
                                     "details": {"type": "string"}},
                      "required": ["title", "due", "details"], "additionalProperties": False},
     "strict": True},
]
WORKSPACE_TOOL_NAMES = {t["name"] for t in WORKSPACE_TOOLS}
