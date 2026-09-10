"""Региональный слой параметров (F10): приоритет регионального значения, инструменты, регион дела."""

import json

from taxcorpus.parser import parse_document
from taxcorpus.tools import LocalCorpus, execute_tool
from tests.test_agent import TEXT

PARAMS = [
    {"name": "desk_audit_duration", "value": 3, "unit": "months", "valid_from": "2020-01-01", "source_unit_id": "nk1.ch14.art88.p2", "anchor": "трех месяцев"},
    {"name": "desk_audit_duration", "value": 2, "unit": "months", "valid_from": "2025-01-01", "region": "77", "tax": "usn",
     "source_unit_id": "nk1.ch14.art88.p2", "anchor": "x", "regional_act": {"region": "77", "number": "1-ОЗ"}},
]


def _corpus():
    _, records, _ = parse_document(TEXT, "nk1")
    return LocalCorpus.from_records(records, {"nk1": "2026-08-04"}, parameters=PARAMS)


def test_regional_value_preferred_and_federal_fallback():
    c = _corpus()
    assert c.get_parameter("desk_audit_duration", "2026-09-10")["value"] == 3
    assert c.get_parameter("desk_audit_duration", "2026-09-10", "77")["value"] == 2
    assert c.get_parameter("desk_audit_duration", "2026-09-10", "50")["value"] == 3      # чужой регион -> федеральное
    assert c.get_parameter("desk_audit_duration", "2024-06-01", "77")["value"] == 3      # до начала регионального
    assert [p.get("region") for p in c.list_parameters("2026-09-10", "77")] == [None, "77"]
    assert c.list_parameters("2026-09-10", "77", tax="usn")[0]["region"] == "77" and c.list_parameters("2026-09-10", "50", tax="usn") == []
    out = json.loads(execute_tool(c, "get_parameter", {"name": "desk_audit_duration", "region": "50"}, "2026-09-10", None)[0])
    assert out["value"] == 3 and "федеральное" in out["note"]
    out = json.loads(execute_tool(c, "get_parameter", {"name": "desk_audit_duration", "region": "77"}, "2026-09-10", None)[0])
    assert out["value"] == 2 and "note" not in out
    lst = json.loads(execute_tool(c, "list_regional_benefits", {"region": "77", "tax": "usn"}, "2026-09-10", None)[0])
    assert lst["regional_rows"] == 1 and "note" not in lst
    lst = json.loads(execute_tool(c, "list_regional_benefits", {"region": "50", "tax": None}, "2026-09-10", None)[0])
    assert lst["regional_rows"] == 0 and "федеральные" in lst["note"]


def test_session_injects_workspace_region(tmp_path):
    from taxcorpus.agent import TaxAgent
    from taxcorpus.case_session import CaseSession
    from taxcorpus.workspace import Workspace
    from tests.test_agent import FakeClient, _block, _response

    ws = Workspace.create("reg", "Дело", as_of="2026-09-10", root=tmp_path, jurisdiction="77")
    responses = [
        _response([_block(type="tool_use", id="t1", name="get_parameter", input={"name": "desk_audit_duration", "region": None})], "tool_use"),
        _response([_block(type="text", text="Срок — два месяца по региональному значению.")], "end_turn"),
    ]
    agent = TaxAgent(FakeClient(responses), _corpus(), fallbacks=False)
    session = CaseSession(ws, agent)
    session.send("Какой срок?")
    assert session.tool_log[0]["input"]["region"] == "77" and json.loads(session.tool_log[0]["output"])["value"] == 2
