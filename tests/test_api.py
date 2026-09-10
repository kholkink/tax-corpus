"""HTTP API над офлайн-корпусом (пропускается без fastapi)."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from taxcorpus import api  # noqa: E402
from taxcorpus.parser import parse_document  # noqa: E402
from taxcorpus.tools import LocalCorpus  # noqa: E402
from tests.test_agent import TEXT  # noqa: E402


@pytest.fixture(autouse=True)
def offline_corpus(monkeypatch):
    _, records, _ = parse_document(TEXT, "nk1")
    c = LocalCorpus.from_records(records, {"nk1": "2026-08-04"})
    monkeypatch.setattr(api, "corpus", lambda: c)


def test_endpoints():
    client = TestClient(api.app)
    assert client.get("/health").json()["backend"] == "LocalCorpus"
    r = client.get("/units/nk1.ch14.art88.p2", params={"as_of": "2026-09-10"})
    assert r.status_code == 200 and "трех месяцев" in r.json()["full_text"]
    assert client.get("/units/nk1.ch14.art88.p3", params={"as_of": "2026-09-10"}).status_code == 404
    assert client.get("/resolve", params={"citation": "п. 2 ст. 88"}).json()["unit_id"] == "nk1.ch14.art88.p2"
    assert client.get("/search", params={"q": "камеральная проверка"}).json()["results"]
    d = client.post("/deadline", json={"start": "2025-03-20", "amount": 3, "unit": "months"}).json()
    assert d["end"] == "2025-06-20"
    assert client.post("/deadline", json={"start": "2025-03-20", "amount": 0, "unit": "months"}).status_code == 422
