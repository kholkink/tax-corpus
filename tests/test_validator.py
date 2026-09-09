"""Тесты валидатора на синтетических наборах записей."""

from taxcorpus.validator import validate


def rec(uid, kind, parent=None, number=None, text="текст нормы"):
    return {
        "unit_id": uid,
        "act": "nk1",
        "kind": kind,
        "parent_unit_id": parent,
        "number": number,
        "title": None,
        "label": uid,
        "text": text,
        "text_hash": "sha256:0000",
        "paragraphs": [text],
        "edit_note": None,
    }


def base_tree():
    part = rec("nk1", "part", None, "1")
    art1 = rec("nk1.art1", "article", "nk1", "1")
    p1 = rec("nk1.art1.p1", "point", "nk1.art1", "1")
    sp1 = rec("nk1.art1.p1.sp1", "subpoint", "nk1.art1.p1", "1")
    sp2 = rec("nk1.art1.p1.sp2", "subpoint", "nk1.art1.p1", "2")
    return [part, art1, p1, sp1, sp2]


def test_valid_tree_has_no_errors():
    report = validate(base_tree())
    assert not report.has_errors


def test_duplicate_unit_id():
    records = base_tree()
    records.append(rec("nk1.art1", "article", "nk1", "1", "дубликат"))
    report = validate(records)
    assert any(i.rule == "duplicate_unit_id" for i in report.errors)


def test_article_order_regression():
    records = [rec("nk1", "part", None, "1"),
               rec("nk1.art10", "article", "nk1", "10"),
               rec("nk1.art5", "article", "nk1", "5")]
    report = validate(records)
    assert any(i.rule == "article_order" for i in report.errors)


def test_article_gap_is_info_only():
    records = [rec("nk1", "part", None, "1"),
               rec("nk1.art1", "article", "nk1", "1"),
               rec("nk1.art3", "article", "nk1", "3")]
    report = validate(records)
    assert any(i.rule == "article_gap" for i in report.infos)
    assert not report.has_errors


def test_subpoint_sequence_restart():
    records = base_tree()
    records.append(rec("nk1.art1.p1.sp3", "subpoint", "nk1.art1.p1", "1"))
    report = validate(records)
    assert any(i.rule == "subpoint_order" for i in report.errors)


def test_point_order_regression():
    records = [rec("nk1", "part", None, "1"),
               rec("nk1.art1", "article", "nk1", "1"),
               rec("nk1.art1.p2", "point", "nk1.art1", "2"),
               rec("nk1.art1.p1", "point", "nk1.art1", "1")]
    report = validate(records)
    assert any(i.rule == "point_order" for i in report.errors)


def test_empty_text_is_warning():
    records = base_tree()
    records.append(rec("nk1.art2", "article", "nk1", "2", ""))
    report = validate(records)
    assert any(i.rule == "empty_text" for i in report.warnings)
    assert not report.has_errors


def test_bad_parent_kind():
    records = [rec("nk1", "part", None, "1"),
               rec("nk1.art1.p1", "point", "nk1", "1")]
    report = validate(records)
    assert any(i.rule in ("bad_parent_kind", "orphan_unit", "missing_parent")
               for i in report.errors)


def test_markdown_report_renders():
    report = validate(base_tree())
    markdown = report.render_markdown("Тест")
    assert "# Тест" in markdown
    assert "0 ошибок" in markdown
