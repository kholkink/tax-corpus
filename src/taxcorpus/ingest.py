"""Конвертер сырых источников в нормализованный текст.

Первичный формат — HTML документов из банка ГАС «Законодательство России»
(pravo-search.minjust.ru): абзацы в <p>, заголовки глав/разделов разорваны
на несколько центрированных <p> (склеиваются, но не через маркер структуры),
служебные пометки банка обёрнуты в <...> и могут занимать несколько абзацев
(склеиваются в один). Сущности HTML декодируются.
Выход — текст, где абзацы разделены пустой строкой (формат парсера).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# новый центрированный блок, начинающийся с маркера, не приклеивается к предыдущему
RE_MARKER_START = re.compile(r"^(?:Часть|ЧАСТЬ|Раздел|РАЗДЕЛ|Подраздел|ПОДРАЗДЕЛ|"
                             r"Глава|ГЛАВА|Статья|СТАТЬЯ)\s")


class _ParagraphExtractor(HTMLParser):
    """Собирает абзацы <p> вместе с признаком центрирования."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, bool]] = []
        self._buf: list[str] = []
        self._centered = False
        self._in_p = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self._flush()
            self._in_p = True
            self._buf = []
            style = (dict(attrs).get("style") or "")
            self._centered = "center" in style
        elif tag == "br" and self._in_p:
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_p:
            self._in_p = False
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._buf.append(data)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.blocks.append((text, self._centered))


def html_to_paragraph_text(html: str) -> str:
    """HTML банка ГАС -> текст с абзацами, разделёнными пустой строкой."""
    parser = _ParagraphExtractor()
    parser.feed(html)

    # 1) заголовки глав/разделов разорваны между центрированными абзацами —
    #    склеиваем, но не через маркер структуры («Раздел VI» и «Глава 15» рядом)
    centered_merged: list[tuple[str, bool]] = []
    for text, centered in parser.blocks:
        if (centered and centered_merged and centered_merged[-1][1]
                and not RE_MARKER_START.match(text)):
            centered_merged[-1] = (f"{centered_merged[-1][0]} {text}", True)
        else:
            centered_merged.append((text, centered))

    # 2) пометки банка «<В ред. …>» могут занимать несколько абзацев — склеиваем
    merged: list[str] = []
    pending: list[str] = []
    for text, _centered in centered_merged:
        if pending:
            pending.append(text)
            if text.endswith(">"):
                merged.append(" ".join(pending))
                pending = []
            continue
        if text.startswith("<") and not text.endswith(">"):
            pending = [text]
            continue
        merged.append(text)
    if pending:
        merged.append(" ".join(pending))

    return "\n\n".join(merged)


def convert(raw: str, source_format: str) -> str:
    if source_format in ("html", "htm"):
        return html_to_paragraph_text(raw)
    return raw
