"""Нормализация сырого текста: страница -> единый текст с абзацами.

Ожидается, что конвертер источника (HTML/DOCX/PDF -> текст) отделяет абзацы
пустой строкой. Здесь: унификация пробелов/переводов строк, удаление шума
(линии-разделители, номера страниц), склейка перенесённых строк в абзацы
с принудительным разрывом на маркерах структуры.
"""

from __future__ import annotations

import re

# линии-разделители и номера страниц
RE_NOISE_LINE = re.compile(r"^(?:[-–—_=*·•.\s]{3,}|\d{1,4})$")


def normalize_text(raw: str) -> tuple[str, int]:
    """Нормализует сырой текст. Возвращает (текст, число удалённых строк-шума)."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u00a0", " ")
    text = text.replace("\t", " ")

    removed = 0
    cleaned: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ ]{2,}", " ", line).strip()
        if line and RE_NOISE_LINE.match(line):
            removed += 1
            continue
        cleaned.append(line)

    out: list[str] = []
    for line in cleaned:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out).strip(), removed


def split_paragraphs(text: str, force_break_re: tuple[re.Pattern, ...] = ()) -> list[str]:
    """Разбивает текст на абзацы.

    Абзац — блок, отделённый пустой строкой; перенесённые строки склеиваются
    пробелом. Строка, начинающаяся с маркера структуры (force_break_re),
    начинает новый абзац принудительно — на случай источников без пустых строк.
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            flush()
            continue
        if current and any(pattern.match(line) for pattern in force_break_re):
            flush()
        current.append(line)
    flush()
    return blocks
