"""RRF-слияние и гибридный поиск с подменным семантическим индексом (без модели)."""

from taxcorpus.embeddings import rrf
from taxcorpus.parser import parse_document
from taxcorpus.tools import HybridSearch, LocalCorpus
from tests.test_agent import TEXT


def test_rrf_prefers_items_present_in_both_lists():
    fused = rrf([["a", "b", "c"], ["c", "d", "a"]])
    assert [u for u, _ in fused][:2] == ["a", "c"]
    assert fused[0][1] == 1 / 61 + 1 / 63


class FakeDense:
    ready = True

    def __init__(self, hits):
        self.hits = hits

    def search(self, query, limit=20, allowed=None):
        return [(u, s) for u, s in self.hits if allowed is None or u in allowed][:limit]


def test_hybrid_merges_lexical_and_dense_and_filters_by_date():
    _, records, _ = parse_document(TEXT, "nk1")
    corpus = LocalCorpus.from_records(records, {"nk1": "2026-08-04"})
    # dense «знает», что п. 2 (три месяца) — самый близкий, и предлагает отменённый п. 3
    corpus.hybrid = HybridSearch(corpus.units, corpus.verifier(),
                                 FakeDense([("nk1.ch14.art88.p2", 0.9), ("nk1.ch14.art88.p3", 0.8)]))
    rows = corpus.search("срок проверки", "2026-09-10", 5)
    ids = [r["unit_id"] for r in rows]
    assert ids[0] == "nk1.ch14.art88.p2"
    assert "nk1.ch14.art88.p3" not in ids  # отменён на дату — отфильтрован
    assert "dense" in rows[0]["sources"]
    # без индекса — только лексика
    corpus.hybrid = HybridSearch(corpus.units, corpus.verifier(), None)
    assert all("sources" not in r for r in corpus.search("проверка", "2026-09-10", 5))
