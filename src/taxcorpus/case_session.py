"""Сессия агента в деле (docs/workspace-plan.md, фаза A2): персистентный диалог,
инструменты корпуса + инструменты дела, пауза на вопрос юристу (ask_user).

Цикл: юрист пишет -> модель зовёт инструменты -> ask_user приостанавливает сессию
(status waiting_user), ответ юриста подаётся как tool_result и цикл продолжается ->
финальный текст проверяется на цитаты (один круг переработки) -> всё сохраняется в
workspaces/<slug>/sessions/<id>.json. Файлы агента (write_file) проверяются так же.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .agent import FINAL_PROMPT, REWORK_PROMPT, SYSTEM_PROMPT, TaxAgent, _text_of
from .citations import VerificationReport
from .tools import TOOL_DEFINITIONS, execute_tool
from .facts import FACT_TOOL_NAMES, FACT_TOOLS, FactStore
from .collab import COLLAB_TOOL_NAMES, COLLAB_TOOLS, CollabStore
from .monitor import MONITOR_TOOL_NAMES, MONITOR_TOOLS
from .templates import TEMPLATE_TOOL_NAMES, TEMPLATE_TOOLS, draft_from_template
from .workspace import WORKSPACE_TOOL_NAMES, WORKSPACE_TOOLS, Workspace

WORKSPACE_RULES = """

Ты работаешь в деле «{title}» (клиент: {client}; нормы на дату {as_of}{jurisdiction}).
Правила дела:
- Факты дела (даты, суммы, контрагенты, режим налогообложения) бери из файлов дела (list_files, read_file, search_files), а не из предположений. Нормы права — только из корпуса (search, get_unit …).
- Начни с notes/задача.md и файлов inbox/. Если для ответа не хватает факта, который нельзя взять из файлов, — ask_user (один конкретный вопрос).
- Результат исследования сохраняй в research/<тема>.md через write_file в формате ответа (Вывод, Обоснование, Риски и оговорки, Что изменилось, Уверенность, Дата) плюс раздел «Факты дела» со ссылками на файлы. Черновики документов — в drafts/.
- Файлы в inbox/ и notes/ не изменяй; выводы для протокола дописывай в notes/протокол.md через append_note.
- Факты из документов фиксируй в таблице фактов (add_fact) с дословной цитатой из файла и ролью для дат процедуры (act_received, decision_received, decision_date …); сроки и давность считай derive_deadlines, а не вручную. В выводах опирайся на подтверждённые юристом факты (list_facts) или текст файлов; неподтверждённые — как гипотезы с оговоркой.
- Перед перезаписью файла агента прочитай list_comments: учти открытые замечания юристов и ответь на каждое reply_comment (что изменено или почему не принято).
- Документы (возражения, жалобы, ответы на требования, меморандум) начинай с draft_document по шаблону фирмы, затем заполняй секции агента и перезаписывай файл через write_file; экспорт в DOCX делает юрист кнопкой.
- Задачи юристу (получить документ, уточнить у клиента) ставь через create_task.
- В конце хода кратко скажи, что сделано, какие файлы созданы/обновлены и что ждёт юриста."""

REDACTION_RULES = """
Конфиденциальность: персональные данные в этом деле заменены плейсхолдерами вида [ФИО-1], [ИНН-1],
[СЧЁТ-1], [ТЕЛ-1] — это и есть реальные реквизиты, скрытые от провайдера. Используйте плейсхолдеры
как обычные значения (в тексте, аргументах инструментов, файлах), не комментируйте их, не пытайтесь
восстановить или запросить исходные данные: юрист увидит настоящие значения автоматически.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _block_to_dict(block) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return {k: v for k, v in vars(block).items() if not k.startswith("_")}


@dataclass
class Turn:
    kind: str                       # answer | question | refusal
    text: str = ""
    question: dict | None = None
    verification: VerificationReport | None = None
    files_written: list[str] = field(default_factory=list)


class CaseSession:
    def __init__(self, workspace: Workspace, agent: TaxAgent, session_id: str | None = None):
        self.ws = workspace
        self.agent = agent
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.messages: list[dict] = []
        self.tool_log: list[dict] = []
        self.status = "active"       # active | waiting_user | done
        self.pending: dict | None = None   # {"tool_use_id", "question", "options", "results": [...]}
        self.questions: list[dict] = []
        self.started_at = _now()
        self.files_written: list[str] = []
        # F9: чувствительное дело у облачного провайдера — персональные данные маскируются
        provider = getattr(agent, "provider", None)
        location = getattr(provider, "location", "cloud")
        self.redactor = None
        if workspace.manifest.confidentiality == "sensitive" and location == "cloud":
            from .redact import Redactor
            self.redactor = Redactor(workspace.path / "redaction_map.json")

    def _out(self, text: str) -> str:
        """Текст к модели: маскировка ПДн, если включена."""
        return self.redactor.redact(text) if self.redactor else text

    def _in(self, text: str) -> str:
        """Текст от модели: демаскировка."""
        return self.redactor.unredact(text) if self.redactor else text

    # --- персистентность ----------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self.ws.path / "sessions" / f"{self.session_id}.json"

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "session_id": self.session_id, "started_at": self.started_at, "updated_at": _now(),
            "status": self.status, "messages": self.messages, "tool_log": self.tool_log,
            "pending": self.pending, "questions": self.questions, "files_written": self.files_written,
            "model": self.agent.model,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, workspace: Workspace, agent: TaxAgent, session_id: str) -> "CaseSession":
        data = json.loads((workspace.path / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))
        s = cls(workspace, agent, session_id)
        for key in ("messages", "tool_log", "status", "pending", "questions", "started_at", "files_written"):
            setattr(s, key, data.get(key, getattr(s, key)))
        return s

    @staticmethod
    def list_sessions(workspace: Workspace) -> list[dict]:
        out = []
        for p in sorted((workspace.path / "sessions").glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"session_id": d["session_id"], "status": d["status"],
                        "started_at": d["started_at"], "updated_at": d.get("updated_at"),
                        "turns": sum(1 for m in d["messages"] if m["role"] == "user" and isinstance(m["content"], str))})
        return out

    # --- промпт и инструменты -----------------------------------------------------------
    def system(self) -> str:
        m = self.ws.manifest
        text = SYSTEM_PROMPT.format(as_of=m.as_of) + WORKSPACE_RULES.format(
            title=m.title, client=m.client or "не указан", as_of=m.as_of,
            jurisdiction=f"; регион: {m.jurisdiction}" if m.jurisdiction else "")
        if self.redactor:
            text += REDACTION_RULES
        return text

    def tools(self) -> list[dict]:
        return [*TOOL_DEFINITIONS, *WORKSPACE_TOOLS, *FACT_TOOLS, *TEMPLATE_TOOLS, *COLLAB_TOOLS, *MONITOR_TOOLS]

    def _run_workspace_tool(self, name: str, args: dict) -> tuple[str, bool]:
        try:
            if name == "list_files":
                result = self.ws.list_files()
            elif name == "read_file":
                result = self.ws.read_file(args["path"], int(args.get("offset") or 0))
            elif name == "search_files":
                result = self.ws.search_files(args["query"])
            elif name == "write_file":
                report = self.agent._verify(args["content"], self.ws.manifest.as_of)
                header = {
                    "summary": args.get("summary", ""),
                    "sources": sorted({c.unit_id for c in report.checks if c.status == "ok" and c.unit_id}),
                    "verification": {"ok": report.ok,
                                     "problems": [f"{c.status}: {c.raw}" for c in report.problems]},
                }
                result = self.ws.write_file(args["path"], args["content"], header)
                self.files_written.append(args["path"])
                CollabStore(self.ws).log("agent", "write_file", args["path"],
                                         {"version": result["version"], "session_id": self.session_id,
                                          "verification_ok": report.ok})
                result["verification"] = header["verification"]
                if not report.ok:
                    result["note"] = ("файл записан, но в нём ссылки, не прошедшие проверку "
                                      "(MISS — нет в корпусе, STALE — не действует на дату): "
                                      "исправь и перезапиши файл")
            elif name == "append_note":
                result = self.ws.append_note(args["path"], args["text"])
            elif name == "create_task":
                result = self.ws.create_task(args["title"], args.get("due"), args.get("details", ""))
            elif name == "list_facts":
                result = FactStore(self.ws).list(kind=args.get("kind"),
                                                 confirmed=True if args.get("confirmed_only") else None)
            elif name == "add_fact":
                fact = FactStore(self.ws).add(args["kind"], args["value"], args["text"], args.get("source_path"),
                                              args.get("quote"), args.get("page"), args.get("role"),
                                              extracted_by="agent")
                result = {**fact.to_dict(), "note": "факт записан как неподтверждённый; юрист подтвердит в таблице фактов"}
            elif name == "list_notifications":
                conn = getattr(self.agent.corpus, "conn", None)
                if conn is None:
                    result = {"notifications": [], "note": "мониторинг доступен только с БД"}
                else:
                    from .monitor import notifications
                    result = notifications(conn, self.ws.manifest.slug, "open", 20)
                    if not result:
                        result = {"notifications": [], "note": "открытых уведомлений нет"}
            elif name == "list_comments":
                result = CollabStore(self.ws).threads(args["path"], open_only=True)
                if not result:
                    result = {"path": args["path"], "comments": [], "note": "открытых комментариев нет"}
            elif name == "reply_comment":
                result = CollabStore(self.ws).add_comment(
                    CollabStore(self.ws).get(args["comment_id"])["path"], args["text"], "agent",
                    parent_id=args["comment_id"])
            elif name == "draft_document":
                result = draft_from_template(self.ws, args["template"], args["path"], args.get("values") or {},
                                             self.agent.calendar)
                self.files_written.append(result["path"])
                CollabStore(self.ws).log("agent", "draft_document", result["path"],
                                         {"template": result["template"], "session_id": self.session_id})
            elif name == "derive_deadlines":
                result = FactStore(self.ws).derive_deadlines(self.agent.calendar,
                                                             confirmed_only=bool(args.get("confirmed_only")))
            elif name == "audit_document":
                from .audit import audit_text, render_markdown
                text = self.ws.text_of(args["path"])
                report = audit_text(self.agent.corpus, text, self.ws.manifest.as_of, args.get("doc_date"))
                out_path = "research/аудит-" + Path(args["path"]).stem + ".md"
                self.ws.write_file(out_path, render_markdown(report),
                                   {"summary": f"аудит {args['path']}", "audit": report.counts})
                self.files_written.append(out_path)
                result = {**report.to_dict(), "report_path": out_path}
                result["items"] = [{k: v for k, v in it.items() if k not in ("start", "end")}
                                   for it in result["items"]]
            else:
                return json.dumps({"error": f"неизвестный инструмент {name}"}, ensure_ascii=False), True
            return json.dumps(result, ensure_ascii=False, default=str), False
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), True

    # --- цикл ---------------------------------------------------------------------------
    def send(self, text: str) -> Turn:
        if self.status == "waiting_user":
            return self.answer(text)
        self.messages.append({"role": "user", "content": self._out(text)})
        self.status = "active"
        return self._run()

    def answer(self, text: str) -> Turn:
        if self.status != "waiting_user" or not self.pending:
            raise RuntimeError("нет открытого вопроса агента")
        for q in self.questions:
            if q["tool_use_id"] == self.pending["tool_use_id"]:
                q["answer"], q["answered_at"] = text, _now()
        results = [*self.pending["results"],
                   {"type": "tool_result", "tool_use_id": self.pending["tool_use_id"],
                    "content": json.dumps({"answer": self._out(text)}, ensure_ascii=False)}]
        self.messages.append({"role": "user", "content": results})
        self.pending = None
        self.status = "active"
        return self._run()

    def _call(self, tool_choice: dict | None = None):
        response = self.agent._create(self.system(), self.messages, tool_choice=tool_choice,
                                      tools=self.tools())
        return response

    def _run(self) -> Turn:
        as_of = self.ws.manifest.as_of
        turn = None
        for _ in range(self.agent.max_tool_rounds + 1):
            response = self._call()
            content = [_block_to_dict(b) for b in response.content]
            if response.stop_reason == "refusal":
                self.messages.append({"role": "assistant", "content": content})
                self.status = "done"
                turn = Turn("refusal", "Модель отказалась отвечать на этот запрос.")
                break
            if response.stop_reason == "pause_turn":
                self.messages.append({"role": "assistant", "content": content})
                continue
            if response.stop_reason != "tool_use":
                self.messages.append({"role": "assistant", "content": content})
                turn = self._finish(_text_of(response), as_of)
                break
            self.messages.append({"role": "assistant", "content": content})
            results, question = [], None
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                if block.name == "ask_user":
                    question = {"tool_use_id": block.id, "question": self._in(args.get("question", "")),
                                "options": [self._in(o) for o in (args.get("options") or [])], "asked_at": _now()}
                    continue
                if self.redactor:  # аргументы модели содержат плейсхолдеры — вернуть реальные значения
                    args = json.loads(self._in(json.dumps(args, ensure_ascii=False)))
                if (block.name in WORKSPACE_TOOL_NAMES or block.name in FACT_TOOL_NAMES
                        or block.name in TEMPLATE_TOOL_NAMES or block.name in COLLAB_TOOL_NAMES
                        or block.name in MONITOR_TOOL_NAMES):
                    output, is_error = self._run_workspace_tool(block.name, args)
                else:
                    output, is_error = execute_tool(self.agent.corpus, block.name, args, as_of,
                                                    self.agent.calendar)
                output = self._out(output)
                self.tool_log.append({"name": block.name, "input": args, "output": output[:4000],
                                      "is_error": is_error, "at": _now()})
                item = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                if is_error:
                    item["is_error"] = True
                results.append(item)
            if question is not None:
                self.pending = {**question, "results": results}
                self.questions.append(question)
                self.status = "waiting_user"
                self.save()
                return Turn("question", question=question)
            self.messages.append({"role": "user", "content": results})
        if turn is None:
            self.messages.append({"role": "user", "content": FINAL_PROMPT})
            response = self._call(tool_choice={"type": "none"})
            self.messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in response.content]})
            turn = self._finish(_text_of(response), as_of)
        self.save()
        return turn

    def _finish(self, text: str, as_of: str) -> Turn:
        text = self._in(text)
        report = self.agent._verify(text, as_of)
        if not report.ok:
            problems = "\n".join(f"- {c.status.upper()}: «{c.raw}»" + (f" — {c.note}" if c.note else "")
                                 for c in report.problems)
            self.messages.append({"role": "user", "content": REWORK_PROMPT.format(problems=problems)})
            response = self._call(tool_choice={"type": "none"})
            self.messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in response.content]})
            if response.stop_reason != "refusal":
                text = self._in(_text_of(response))
                report = self.agent._verify(text, as_of)
        self.status = "active"
        return Turn("answer", text=text, verification=report, files_written=list(self.files_written))
