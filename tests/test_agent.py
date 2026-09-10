"""Агент с подменным клиентом: цикл tool use, проверка цитат, переработка, отказ."""

import json
from types import SimpleNamespace

from taxcorpus.agent import SYSTEM_PROMPT, TaxAgent, render
from taxcorpus.parser import parse_document
from taxcorpus.tools import TOOL_DEFINITIONS, LocalCorpus, execute_tool

TEXT = """Глава 14. Налоговый контроль

Статья 88. Камеральная налоговая проверка

1. Камеральная налоговая проверка проводится по месту нахождения налогового органа.

2. Камеральная налоговая проверка проводится в течение трех месяцев со дня представления декларации.

3. <Утратил силу с 1 января 2020 г.: Федеральный закон от 29 сентября 2019 N 325-ФЗ>
"""


def corpus() -> LocalCorpus:
    _, records, _ = parse_document(TEXT, "nk1")
    return LocalCorpus.from_records(records, {"nk1": "2026-08-04"})


def _block(**kw):
    return SimpleNamespace(**kw)


def _response(content, stop_reason):
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                                 cache_read_input_tokens=0))


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.response


class FakeClient:
    """Возвращает заранее заданные ответы; записывает запросы."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(stream=self._stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        # агент дописывает один и тот же список messages — фиксируем снимок
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return FakeStream(self.responses.pop(0))


def test_tool_definitions_are_strict_and_executable():
    c = corpus()
    for tool in TOOL_DEFINITIONS:
        assert tool["strict"] and tool["input_schema"]["additionalProperties"] is False
    out, err = execute_tool(c, "resolve_citation", {"citation": "п. 2 ст. 88"}, "2026-09-10")
    assert not err and json.loads(out)["unit_id"] == "nk1.ch14.art88.p2"
    out, err = execute_tool(c, "get_unit", {"unit_id": "nk1.ch14.art88.p3"}, "2026-09-10")
    assert not err and json.loads(out)["found"] is False
    out, err = execute_tool(c, "search", {"query": "камеральная проверка три месяца"}, "2026-09-10")
    assert not err and json.loads(out)[0]["unit_id"] == "nk1.ch14.art88.p2"
    out, err = execute_tool(c, "compute_deadline", {"start": "2025-03-20", "amount": 3, "unit": "months"}, "2026-09-10")
    assert not err and json.loads(out)["end"] == "2025-06-20"
    out, err = execute_tool(c, "nope", {}, "2026-09-10")
    assert err


def test_agent_loop_verifies_and_reworks():
    tool_use = _block(type="tool_use", id="t1", name="resolve_citation", input={"citation": "п. 2 ст. 88"})
    bad_answer = _block(type="text", text="**Вывод** Три месяца — п. 2 ст. 88 НК РФ; см. также п. 3 ст. 88 НК РФ и ст. 999 НК РФ.")
    good_answer = _block(type="text", text="**Вывод** Три месяца — п. 2 ст. 88 НК РФ.\n**Дата** 2026-09-10")
    client = FakeClient([
        _response([tool_use], "tool_use"),
        _response([bad_answer], "end_turn"),
        _response([good_answer], "end_turn"),
    ])
    agent = TaxAgent(client, corpus(), fallbacks=False)
    result = agent.ask("Сколько длится камеральная проверка?", "2026-09-10")

    assert result.reworked and result.verification.ok
    assert [c.name for c in result.tool_calls] == ["resolve_citation"]
    assert json.loads(result.tool_calls[0].output)["unit_id"] == "nk1.ch14.art88.p2"
    # системный промпт содержит дату и правило «цитируй или откажись»
    assert "2026-09-10" in client.requests[0]["system"][0]["text"]
    assert client.requests[0]["tools"] is TOOL_DEFINITIONS
    # второй запрос несёт результат инструмента, третий — замечания проверки
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "user" and second[-1]["content"][0]["type"] == "tool_result"
    rework = client.requests[2]["messages"][-1]["content"]
    assert "STALE" in rework and "MISS" in rework and "п. 3 ст. 88" in rework
    text = render(result)
    assert "переработан" in text and "OK" in text


def test_agent_refusal_is_reported():
    client = FakeClient([_response([], "refusal")])
    result = TaxAgent(client, corpus(), fallbacks=False).ask("вопрос", "2026-09-10")
    assert result.refused and "отказалась" in result.answer


def test_fallbacks_go_through_beta_endpoint():
    client = FakeClient([_response([_block(type="text", text="**Вывод** нет оснований")], "end_turn")])
    TaxAgent(client, corpus(), fallbacks=True).ask("вопрос", "2026-09-10")
    req = client.requests[0]
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["thinking"] == {"type": "adaptive"} and req["output_config"] == {"effort": "high"}
    assert "{as_of}" not in SYSTEM_PROMPT.format(as_of="x")
