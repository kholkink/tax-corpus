"""Маскировка персональных данных перед отправкой облачному провайдеру (F9 плана ПО).

Детерминированно: ИНН, КПП, ОГРН/ОГРНИП, СНИЛС, паспорт, банковские счета, телефоны,
e-mail, ФИО в формах «Иванов И.И.», «И.И. Иванов» и «Иванов Иван Иванович» (в любом падеже) заменяются
плейсхолдерами вида [ИНН-1]; словарь замен хранится в деле (redaction_map.json), ответ
модели демаскируется. Суммы, даты и названия организаций не трогаются: они нужны для
права и не идентифицируют физлицо сами по себе.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# порядок важен: длинные и специфичные шаблоны раньше коротких
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("СЧЁТ", re.compile(r"(?<!\d)\d{20}(?!\d)")),
    ("ОГРН", re.compile(r"(?<!\d)\d{15}(?!\d)|(?<!\d)\d{13}(?!\d)")),
    ("ИНН", re.compile(r"(?<!\d)\d{12}(?!\d)|(?<!\d)\d{10}(?!\d)")),
    ("СНИЛС", re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[ -]\d{2}(?!\d)")),
    ("КПП", re.compile(r"(?<=КПП[ :])\s*\d{9}(?!\d)")),
    ("ПАСПОРТ", re.compile(r"(?<!\d)\d{2}\s?\d{2}\s?№?\s?\d{6}(?!\d)")),
    ("ТЕЛ", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
    # ФИО: «Иванов И.И.», «И.И. Иванов», «Иванов Иван Иванович» (отчество на -вич/-вна/-ична)
    ("ФИО", re.compile(
        r"\b[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\."
        r"|\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
        r"|\b[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:вич|вн|ичн|ыч)[а-яё]{0,2}\b")),
]
RE_PLACEHOLDER = re.compile(r"\[(EMAIL|СЧЁТ|ОГРН|ИНН|СНИЛС|КПП|ПАСПОРТ|ТЕЛ|ФИО)-(\d+)\]")


class Redactor:
    def __init__(self, map_path: str | Path | None = None):
        self.map_path = Path(map_path) if map_path else None
        self.forward: dict[str, str] = {}   # значение -> плейсхолдер
        self.backward: dict[str, str] = {}  # плейсхолдер -> значение
        if self.map_path and self.map_path.exists():
            data = json.loads(self.map_path.read_text(encoding="utf-8"))
            self.backward = data
            self.forward = {v: k for k, v in data.items()}

    def _save(self) -> None:
        if self.map_path:
            self.map_path.parent.mkdir(parents=True, exist_ok=True)
            self.map_path.write_text(json.dumps(self.backward, ensure_ascii=False, indent=1), encoding="utf-8")

    def _placeholder(self, kind: str, value: str) -> str:
        if value in self.forward:
            return self.forward[value]
        n = 1 + sum(1 for k in self.backward if k.startswith(f"[{kind}-"))
        token = f"[{kind}-{n}]"
        self.forward[value], self.backward[token] = token, value
        return token

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for kind, pattern in PATTERNS:
            out = pattern.sub(lambda m, k=kind: self._placeholder(k, m.group(0)), out)
        self._save()
        return out

    def unredact(self, text: str) -> str:
        if not text:
            return text
        return RE_PLACEHOLDER.sub(lambda m: self.backward.get(m.group(0), m.group(0)), text)

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for token in self.backward:
            kind = token[1:token.index("-")]
            out[kind] = out.get(kind, 0) + 1
        return out
