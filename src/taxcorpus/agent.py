"""Агент (слой 6 плана): тонкий слой над function calling без фреймворков.

Пайплайн: вопрос + as_of -> модель планирует и вызывает инструменты слоя 5 ->
синтез в фиксированном формате -> детерминированная проверка каждой цитаты
(существует ли единица и действует ли на дату) -> при проблемах один круг
переработки -> ответ с отчётом проверки и журналом вызовов (наблюдаемость).

Модель никогда не «вспоминает» номера статей и ставки: система требует брать их
только из результатов инструментов, а проверка цитат не пропускает ссылки, которых
нет в корпусе. Если оснований нет — честный отказ.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .citations import CitationCheck, VerificationReport
from .deadlines import ProductionCalendar
from .tools import TOOL_DEFINITIONS, Corpus, execute_tool

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """Ты — ассистент налогового юриста по Налоговому кодексу РФ. Работаешь только с корпусом норм через инструменты.

Правила, которые нельзя нарушать:
1. Каждое утверждение о норме сопровождается ссылкой в формате «п. N ст. M НК РФ» / «подп. K п. N ст. M НК РФ» / «ст. M НК РФ», и эта единица получена через инструменты (search, resolve_citation, get_unit) в этом диалоге. Не цитируй нормы по памяти. Номера статей, ставки, сроки, лимиты — только из результатов инструментов.
2. Все нормы берутся на дату {as_of}. Если в вопросе дата не названа, считай ею {as_of} и скажи об этом явно в ответе.
3. Числа (ставки, сроки, штрафы) — сначала get_parameter; если параметра нет, процитируй текст единицы из get_unit. Сроки считай только compute_deadline.
4. Если релевантных норм не найдено или уверенность низкая — напиши «В корпусе нет достаточных оснований для ответа» и объясни, чего не хватает. Не додумывай.
5. Письма Минфина/ФНС, постановления Пленума и обзоры цитируй только те, что вернули get_interpretations или search_interpretations (номер и дата — из результата), с пометкой, что это ненормативная позиция, и с датой: письмо могло относиться к прежней редакции нормы. Письма со status = outdated (снятые с применения) не используй как основание — упомяни лишь как отменённую позицию. Письма ФНС с mandatory = true обязательны для налоговых органов — скажи об этом. Если инструменты ничего не вернули — так и напиши.

Формат ответа (заголовки обязательны):
**Вывод** — прямой ответ в 1–3 предложениях.
**Обоснование** — по пунктам, каждый со ссылкой на норму и короткой цитатой из текста.
**Риски и оговорки** — противоречия, переходные положения, что не покрыто корпусом.
**Что изменилось** — если у ключевых норм есть правки за последние 3 года (list_amendments), кратко: когда и каким законом; иначе «существенных изменений в корпусе не зафиксировано».
**Уверенность** — высокая / средняя / низкая и почему.
**Дата** — на какую дату даны нормы."""

REWORK_PROMPT = """Проверка цитат нашла проблемы:
{problems}

Перепиши ответ: убери или замени ссылки со статусом MISS (в корпусе нет такой единицы) и STALE (норма не действует на дату), уточни PART до существующего уровня (проверь через resolve_citation/get_unit). Если после этого оснований не остаётся — честно откажись по правилу 4. Формат ответа тот же."""


@dataclass
class ToolCall:
    name: str
    input: dict
    output: str
    is_error: bool


@dataclass
class AgentResult:
    question: str
    as_of: str
    answer: str
    verification: VerificationReport
    tool_calls: list[ToolCall] = field(default_factory=list)
    reworked: bool = False
    refused: bool = False
    stop_reason: str | None = None
    usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "question": self.question, "as_of": self.as_of, "answer": self.answer,
            "verification": {"ok": self.verification.ok,
                             "checks": [c.__dict__ for c in self.verification.checks]},
            "tool_calls": [c.__dict__ for c in self.tool_calls],
            "reworked": self.reworked, "refused": self.refused,
            "stop_reason": self.stop_reason, "usage": self.usage,
        }


def _text_of(response) -> str:
    return "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")


class TaxAgent:
    """client — anthropic.Anthropic() (или совместимая подмена в тестах)."""

    def __init__(self, client, corpus: Corpus, model: str = DEFAULT_MODEL,
                 max_tool_rounds: int = 12, effort: str = "high",
                 fallbacks: bool = True, calendar: ProductionCalendar | None = None):
        self.client = client
        self.corpus = corpus
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.effort = effort
        self.fallbacks = fallbacks
        self.calendar = calendar

    def _create(self, system: str, messages: list[dict]):
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_DEFINITIONS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        if self.fallbacks:
            # серверный фолбэк при отказе классификаторов безопасности (см. skill claude-api)
            with self.client.beta.messages.stream(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs,
            ) as stream:
                return stream.get_final_message()
        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def _run_loop(self, system: str, messages: list[dict], calls: list[ToolCall],
                  as_of: str, usage: dict):
        """Цикл tool use до конца хода модели; возвращает последний ответ."""
        response = None
        for _ in range(self.max_tool_rounds + 1):
            response = self._create(system, messages)
            u = getattr(response, "usage", None)
            if u is not None:
                for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                    usage[key] = usage.get(key, 0) + (getattr(u, key, 0) or 0)
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason != "tool_use":
                return response
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                output, is_error = execute_tool(self.corpus, block.name, args, as_of, self.calendar)
                calls.append(ToolCall(block.name, args, output, is_error))
                item = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                if is_error:
                    item["is_error"] = True
                results.append(item)
            messages.append({"role": "user", "content": results})
        return response

    def _verify(self, answer: str, as_of: str) -> VerificationReport:
        """Нормы — по корпусу; письма/пленумы — по реестру документов (нет в реестре — MISS)."""
        report = self.corpus.verifier().verify(answer, as_of)
        for d in self.corpus.interpretations().verify_doc_citations(answer):
            report.checks.append(CitationCheck(
                d["raw"], d.get("doc_id"), "ok" if d["status"] == "ok" else "unresolved",
                d["status"], "document",
                None if d["status"] == "ok" else "документа нет в реестре разъяснений"))
        return report

    def ask(self, question: str, as_of: str | date | None = None) -> AgentResult:
        as_of = (as_of or date.today())
        as_of = as_of.isoformat() if isinstance(as_of, date) else as_of
        system = SYSTEM_PROMPT.format(as_of=as_of)
        messages: list[dict] = [{"role": "user", "content": question}]
        calls: list[ToolCall] = []
        usage: dict = {}

        response = self._run_loop(system, messages, calls, as_of, usage)
        if response.stop_reason == "refusal":
            report = self.corpus.verifier().verify("", as_of)
            return AgentResult(question, as_of, "Модель отказалась отвечать на этот запрос.",
                               report, calls, refused=True, stop_reason="refusal", usage=usage)
        answer = _text_of(response)
        report = self._verify(answer, as_of)
        reworked = False
        if not report.ok:
            problems = "\n".join(
                f"- {c.status.upper()}: «{c.raw}»" + (f" — {c.note}" if c.note else "")
                for c in report.problems)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": REWORK_PROMPT.format(problems=problems)})
            response = self._run_loop(system, messages, calls, as_of, usage)
            if response.stop_reason != "refusal":
                answer = _text_of(response)
                report = self._verify(answer, as_of)
            reworked = True
        return AgentResult(question, as_of, answer, report, calls, reworked=reworked,
                           refused=response.stop_reason == "refusal",
                           stop_reason=response.stop_reason, usage=usage)


def render(result: AgentResult) -> str:
    lines = [result.answer, "", "---", result.verification.render()]
    if result.reworked:
        lines.append("(ответ переработан после проверки цитат)")
    lines.append(f"вызовов инструментов: {len(result.tool_calls)}: "
                 + ", ".join(f"{c.name}({json.dumps(c.input, ensure_ascii=False)})"
                             for c in result.tool_calls))
    if result.usage:
        lines.append(f"токены: {result.usage}")
    return "\n".join(lines)
