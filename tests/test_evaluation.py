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


def test_documents_excluded_and_precision_undefined_without_expected():
    checks = [_check("п. 2 ст. 88 НК РФ", "nk1.ch14.art88.p2", "ok"),
              {"raw": "письмо ФНС от 01.01.2024 № 1", "unit_id": "fns-1", "status": "ok", "depth": "document"}]
    s = score_answer("q", ["nk1.ch14.art88.p2"], "", checks)
    assert s.cited == ["nk1.ch14.art88.p2"] and s.precision_unit == 1.0
    s = score_answer("q", [], "**Вывод** нормы отменены", checks)
    assert s.precision_unit is None and s.recall_unit is None and not s.abstained
    assert score_answer("q", [], "В корпусе нет документа с такими реквизитами", []).abstained


def test_by_topic_spread_and_accuracy_report(tmp_path):
    from taxcorpus.evaluation import EvalSummary, accuracy_report, score_answer

    def sc(qid, topic, rep, cited_ok, expected):
        checks = [{"raw": c, "unit_id": c, "status": "ok"} for c in cited_ok]
        return score_answer(qid, expected, "ответ", checks, topic=topic, rep=rep)

    scores = [sc("q1", "НДС", 0, ["nk2.ch21.art164.p3"], ["nk2.ch21.art164.p3"]),
              sc("q2", "НДС", 0, ["nk2.ch21.art164.p1", "nk2.ch21.art165"], ["nk2.ch21.art164.p1"]),
              sc("q3", "проверки", 0, ["nk1.ch14.art88.p2"], ["nk1.ch14.art88.p2"]),
              sc("q1", "НДС", 1, ["nk2.ch21.art164.p3", "nk2.ch21.art164"], ["nk2.ch21.art164.p3"])]
    summary = EvalSummary(scores)
    topics = summary.by_topic()
    assert set(topics) == {"НДС", "проверки"} and topics["проверки"]["citation_precision_unit"] == 1.0
    assert topics["НДС"]["questions"] == 3 and 0.5 < topics["НДС"]["citation_precision_unit"] < 1.0
    reps = summary.by_rep()
    assert set(reps) == {0, 1} and reps[1]["citation_precision_unit"] == 1.0   # предок статьи считается попаданием
    spread = summary.spread()
    assert spread["citation_precision_unit"][0] < spread["citation_precision_unit"][1] == 1.0
    rendered = summary.render()
    assert "| тема |" in rendered and "| НДС |" in rendered and "по повторам" in rendered

    golden = {"version": "v1-draft", "questions": [{"id": "q1", "topic": "НДС"}, {"id": "q2", "topic": "НДС"},
                                                   {"id": "q3", "topic": "проверки"}]}
    agent = {"model": "m", "as_of": "2026-09-10", "runs": [{"score": s.__dict__} for s in scores]}
    search = {"variants": {"hybrid": {"unit_hits": 2, "article_hits": 3, "questions": 3, "misses": ["q2"]}},
              "by_topic": {"НДС": {"questions": 2, "hybrid": 1}, "проверки": {"questions": 1, "hybrid": 1}}}
    data, md = accuracy_report(agent, search, golden, snapshot=7, generated_at="2026-09-11T00:00:00+00:00")
    assert data["golden_questions"] == 3 and data["golden_topics"] == {"НДС": 2, "проверки": 1}
    assert data["agent"]["reps"] == 2 and data["agent"]["by_topic"]["проверки"]["questions"] == 1
    assert "# Карта точности" in md and "| hybrid | 2/3 | 3/3 |" in md and "| НДС | 2 | 1 |" in md
    assert "Снимок корпуса: 7" in md and "| вопрос |" not in md            # без таблицы по вопросам
    data2, md2 = accuracy_report(None, None, golden)
    assert data2["agent"] is None and "ещё не выполнялся" in md2 and "ещё не выполнялась" in md2


def test_golden_has_topics():
    import json
    from pathlib import Path
    g = json.loads((Path(__file__).resolve().parents[1] / "tests" / "golden" / "golden_v0.json").read_text(encoding="utf-8"))
    assert all(q.get("topic") for q in g["questions"]) and set(g["topics"]) == {q["topic"] for q in g["questions"]}
    assert len(g["questions"]) >= 70 and len(g["topics"]) >= 12
    ids = [q["id"] for q in g["questions"]]
    assert len(ids) == len(set(ids))
