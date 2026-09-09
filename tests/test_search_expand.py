"""Тесты расширения поискового запроса синонимами-аббревиатурами."""

from taxcorpus.db import ABBREVIATIONS, expand_query


def test_nds_expands_to_full_form():
    assert expand_query("ставка НДС") == [ABBREVIATIONS["ндс"]]


def test_case_insensitive_and_multiple():
    out = expand_query("НДФЛ и енс")
    assert ABBREVIATIONS["ндфл"] in out
    assert ABBREVIATIONS["енс"] in out


def test_no_expansion_without_abbreviations():
    assert expand_query("камеральная проверка срок") == []
