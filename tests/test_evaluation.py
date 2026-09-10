"""Метрики оценки ответов агента (план, §6) на синтетических проверках."""

from taxcorpus.evaluation import EvalSummary, score_answer


def _check(raw, unit_id, status):
    return {"raw": raw, "unit_id": unit_id, "status": status}


def test_precision_recall_and_hallucinations():
    checks = [
        _check("п. 2 ст. 88 НК РФ", "nk1.ch14.art88.p2", "ok"),
        _check("п. 1 ст. 88 НК РФ", "nk1.ch14.art88.p1", "ok"),
        _check("ст. 999 НК РФ", None, "unresolved"),
        _check("п. 3 ст. 10 НК РФ", "nk1.ch1.art10.p3", "not_in_force"),
    ]
    s = score_answer("q01", ["nk1.ch14.art88.p2"], "**Вывод** три месяца", checks, tool_calls=3)
    assert s.cited == ["nk1.ch14.art88.p1", "nk1.ch14.art88.p2"]
    assert s.precision_unit == 0.5 and s.recall_unit == 1.0
    assert s.precision_article == 1.0 and s.recall_article == 1.0
    assert s.hallucinations == 2 and not s.temporal_ok and not s.abstained


def test_parent_or_child_citation_counts_as_hit():
    # ожидался подпункт, процитирован пункт-родитель — засчитывается (и наоборот)
    s = score_answer("q", ["nk2.ch23.art218.p1.sp4"], "",
                     [_check("п. 1 ст. 218 НК РФ", "nk2.ch23.art218.p1", "ok")])
    assert s.precision_unit == 1.0 and s.recall_unit == 1.0
    s = score_answer("q", ["nk1.ch14.art88.p2"], "",
                     [_check("абзац первый п. 2 ст. 88", "nk1.ch14.art88.p2.ab1", "ok")])
    assert s.recall_unit == 1.0


def test_abstention_and_summary():
    a = score_answer("q1", ["nk1.ch14.art88.p2"], "В корпусе нет достаточных оснований для ответа.",
                     [], expected_abstain=True)
    b = score_answer("q2", ["nk1.ch14.art88.p2"], "**Вывод** …",
                     [_check("п. 2 ст. 88 НК РФ", "nk1.ch14.art88.p2", "ok")], reworked=True, tool_calls=2)
    assert a.abstained and a.abstain_correct is True and a.precision_unit is None
    assert b.abstain_correct is None
    summary = EvalSummary([a, b])
    d = summary.as_dict()
    assert d["questions"] == 2
    assert d["citation_precision_unit"] == 1.0 and d["citation_recall_unit"] == 0.5
    assert d["hallucination_rate"] == 0.0 and d["temporal_correctness"] == 1.0
    assert d["abstention_quality"] == 1.0 and d["reworked_share"] == 0.5
    text = summary.render()
    assert "citation_precision_unit" in text and "q2" in text


def test_article_of_unit_id_handles_chapter_section_and_bare_ids():
    from taxcorpus.resolver import article_of_unit_id
    assert article_of_unit_id("nk1.ch14.art88.p1.ab2") == "nk1.ch14.art88"
    assert article_of_unit_id("nk2.rviii-1.art346-4.p2") == "nk2.rviii-1.art346-4"
    assert article_of_unit_id("nk1.art10.p3") == "nk1.art10"
    assert article_of_unit_id("nk2.ch23.art227-1@2.p1") == "nk2.ch23.art227-1@2"
    assert article_of_unit_id("nk1.ch14") == "nk1.ch14"
