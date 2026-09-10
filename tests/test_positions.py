"""Карта позиций по норме (F4): извлечение с дословной цитатой, группировка, конфликт, инструмент и API."""

import json

from taxcorpus.interpretations import Document
from taxcorpus.positions import (Position, PositionStore, extract_position, extract_positions,
                                 position_map, quote_in_text)
from taxcorpus.tools import LocalCorpus, execute_tool
from tests.test_agent import _block, _response
from tests.test_interpretations import LETTER, PLENUM, _records


class FakeExtractClient:
    """messages.create возвращает заранее заданные JSON-ответы по очереди."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

        class _Messages:
            def __init__(self, outer):
                self.outer = outer

            def create(self, **kwargs):
                self.outer.requests.append(kwargs)
                return _response([_block(type="text", text=self.outer.answers.pop(0))], "end_turn")
        self.messages = _Messages(self)


UNIT = {"unit_id": "nk1.ch14.art88.p2", "label": "пункт 2 статьи 88 НК РФ", "text": "Проверка в течение трех месяцев."}


def test_extract_position_requires_verbatim_quote():
    good = json.dumps({"stance": "pro_taxpayer", "summary": "срок проверки — три месяца",
                       "quote": "камеральная проверка проводится в течение трех месяцев", "confidence": 0.9})
    bad = json.dumps({"stance": "pro_authority", "summary": "x", "quote": "проверка длится полгода", "confidence": 0.9})
    none = json.dumps({"stance": "none", "summary": "", "quote": "", "confidence": 0.3})
    client = FakeExtractClient([good, bad, none, "не json"])
    pos, why = extract_position(client, "m", LETTER, UNIT)
    assert pos and pos.stance == "pro_taxpayer" and pos.position_id == "fns-2024-03-11-bs-4-11-2702#nk1.ch14.art88.p2"
    assert "НОРМА: пункт 2 статьи 88" in client.requests[0]["messages"][0]["content"]
    assert extract_position(client, "m", LETTER, UNIT) == (None, "цитата не найдена дословно в документе — позиция отклонена")
    assert extract_position(client, "m", LETTER, UNIT)[1] == "позиции по норме нет"
    assert extract_position(client, "m", LETTER, UNIT)[1] == "модель не вернула JSON"
    assert quote_in_text("Камеральная  проверка проводится", LETTER.text) and not quote_in_text("", LETTER.text)


def test_extract_positions_batch_skips_done_and_respects_budget(tmp_path):
    store = PositionStore(tmp_path / "positions.jsonl")
    edges = [{"doc_id": LETTER.doc_id, "to_unit_id": "nk1.ch14.art88.p2"},
             {"doc_id": LETTER.doc_id, "to_unit_id": "nk1.ch14.art88.p3"},
             {"doc_id": PLENUM.doc_id, "to_unit_id": "nk1.ch14.art88.p2"},
             {"doc_id": "нет-такого", "to_unit_id": "nk1.ch14.art88.p2"}]
    units = {r["unit_id"]: r for r in _records()}
    client = FakeExtractClient([
        json.dumps({"stance": "pro_taxpayer", "summary": "три месяца", "quote": "в течение трех месяцев", "confidence": 0.8}),
        json.dumps({"stance": "pro_authority", "summary": "выдумка", "quote": "нет такого текста", "confidence": 0.8}),
    ])
    stats = extract_positions(client, "m", [LETTER, PLENUM], edges, units, store, limit=2, log=lambda *_: None)
    assert stats == {"calls": 2, "added": 1, "rejected": 1, "skipped": 0, "errors": 0}   # бюджет исчерпан до 3-й пары
    store2 = PositionStore(tmp_path / "positions.jsonl")
    assert {p.stance for p in store2.positions.values()} == {"pro_taxpayer", "none"}   # отказ запомнен
    # повторный запуск: сделанные пары пропущены, остаётся пленум × п. 2
    client2 = FakeExtractClient([json.dumps({"stance": "pro_authority", "summary": "s", "quote": "Согласно пункту 2 статьи 88 НК РФ", "confidence": 0.7})])
    stats2 = extract_positions(client2, "m", [LETTER, PLENUM], edges, units, store2, log=lambda *_: None)
    assert stats2 == {"calls": 1, "added": 1, "rejected": 0, "skipped": 3, "errors": 0}   # 2 сделаны + документ вне реестра
    assert len(store2.for_unit("nk1.ch14.art88.p2")) == 2
    assert len(store2.for_unit("nk1.ch14.art88")) == 3 and store2.for_doc(PLENUM.doc_id)[0].stance == "pro_authority"


def test_position_map_groups_conflicts_and_old_editions():
    docs = {LETTER.doc_id: LETTER.summary(), PLENUM.doc_id: PLENUM.summary()}
    positions = [Position("a#u", LETTER.doc_id, "nk1.ch14.art88.p2", "pro_taxpayer", "три месяца", "q", 0.9),
                 Position("b#u", PLENUM.doc_id, "nk1.ch14.art88.p2", "pro_authority", "иначе", "q2", 0.8),
                 Position("c#u", PLENUM.doc_id, "nk1.ch14.art88.p2", "none", "нет позиции", "", 0.0)]
    amendments = [{"effective_date": "2020-01-01"}, {"effective_date": "2030-01-01"}]
    pm = position_map("nk1.ch14.art88.p2", "2026-09-10", positions, docs, amendments)
    assert pm["conflict"] and pm["counts"] == {"pro_taxpayer": 1, "pro_authority": 1, "neutral": 0}
    assert pm["last_amendment"] == "2020-01-01" and pm["leading_stance"] == "pro_authority"   # пленум авторитетнее письма
    assert pm["positions"]["pro_authority"][0]["older_than_last_amendment"] is True          # 2013 < 2020
    assert pm["positions"]["pro_taxpayer"][0]["older_than_last_amendment"] is False and pm["note"]
    # документы после даты — не показываются
    assert position_map("nk1.ch14.art88.p2", "2013-01-01", positions, docs, [])["counts"]["pro_authority"] == 0


def test_local_corpus_tool_and_api(tmp_path, monkeypatch):
    corpus = LocalCorpus.from_records(_records(), {"nk1": "2026-08-04"}, documents=[LETTER, PLENUM])
    store = PositionStore(tmp_path / "positions.jsonl")
    store.upsert(Position("x#u", LETTER.doc_id, "nk1.ch14.art88.p2", "pro_taxpayer", "три месяца", "в течение трех месяцев", 0.9))
    store.save()
    corpus._position_store = store
    out, is_error = execute_tool(corpus, "get_position_map", {"unit_id": "nk1.ch14.art88.p2"}, "2026-09-10", None)
    data = json.loads(out)
    assert not is_error and data["counts"]["pro_taxpayer"] == 1 and not data["conflict"]
    empty = json.loads(execute_tool(corpus, "get_position_map", {"unit_id": "nk1.ch14.art89.p1"}, "2026-09-10", None)[0])
    assert "note" in empty
    inter = {r["doc_id"]: r for r in corpus.get_interpretations("nk1.ch14.art88.p2", "2026-09-10", 5)}
    assert inter[LETTER.doc_id]["positions"] == {"nk1.ch14.art88.p2": "pro_taxpayer"} and inter[PLENUM.doc_id]["positions"] == {}
    from fastapi.testclient import TestClient
    from taxcorpus import api
    monkeypatch.setattr(api, "corpus", lambda: corpus)
    c = TestClient(api.app)
    assert c.get("/units/nk1.ch14.art88.p2/positions?as_of=2026-09-10").json()["counts"]["pro_taxpayer"] == 1
    card = c.get("/units/nk1.ch14.art88.p2/card?as_of=2026-09-10").json()
    assert card["explain"]["positions"] == 1 and card["positions"]["leading_stance"] == "pro_taxpayer"
