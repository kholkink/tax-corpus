"""Тесты нормализации текста."""

from taxcorpus.normalize import normalize_text, split_paragraphs
from taxcorpus.parser import MARKER_RES


def test_normalize_removes_page_noise():
    raw = "Статья 1. Тест\n---\n\n12\n\nТекст статьи."
    text, removed = normalize_text(raw)
    assert "---" not in text
    assert "\n12\n" not in f"\n{text}\n"
    assert removed == 2


def test_normalize_unifies_whitespace():
    raw = "Статья 1.\u00a0Общие\r\nположения\r\n"
    text, _ = normalize_text(raw)
    assert "\u00a0" not in text
    assert "\r" not in text
    assert "Статья 1. Общие" in text


def test_normalize_keeps_paragraph_breaks():
    raw = "Абзац один.\n\nАбзац два.\n\n\n\nАбзац три."
    text, _ = normalize_text(raw)
    assert text.count("\n\n") == 2


def test_split_paragraphs_joins_wrapped_lines():
    text = "1. Налогоплательщиками признаются\nорганизации и физические лица."
    blocks = split_paragraphs(text)
    assert blocks == ["1. Налогоплательщиками признаются организации и физические лица."]


def test_split_paragraphs_breaks_on_markers():
    text = ("Статья 1. Тест\n1. Первый пункт.\n1) Первый подпункт.\nтекст подпункта\n")
    blocks = split_paragraphs(text, MARKER_RES)
    # источник без пустых строк: маркеры начинают абзац, последующие строки
    # склеиваются в него (переносы), поэтому «текст подпункта» остаётся в пункте
    assert blocks == [
        "Статья 1. Тест",
        "1. Первый пункт.",
        "1) Первый подпункт. текст подпункта",
    ]


def test_noise_counter():
    _, removed = normalize_text("текст\n====\n\n1234\nконец")
    assert removed == 2
