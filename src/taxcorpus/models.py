"""Базовые модели данных корпуса (срез фазы 1).

Соответствует модели из архитектурного плана: Act / Edition / Unit / UnitText /
Reference. Парсер оперирует деревом Unit, затем «сплющивает» его в плоские
записи для JSONL и загрузки в PostgreSQL.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Виды структурных единиц в порядке вложенности
KINDS = ("part", "section", "subsection", "chapter", "article", "point", "subpoint", "paragraph")

KIND_RU = {
    "part": "часть",
    "section": "раздел",
    "subsection": "подраздел",
    "chapter": "глава",
    "article": "статья",
    "point": "пункт",
    "subpoint": "подпункт",
    "paragraph": "абзац",
}

_ORDINALS_RU = (
    "первый", "второй", "третий", "четвёртый", "пятый", "шестой", "седьмой",
    "восьмой", "девятый", "десятый", "одиннадцатый", "двенадцатый", "тринадцатый",
    "четырнадцатый", "пятнадцатый", "шестнадцатый", "семнадцатый", "восемнадцатый",
    "девятнадцатый", "двадцатый", "двадцать первый", "двадцать второй",
    "двадцать третий", "двадцать четвёртый", "двадцать пятый", "двадцать шестой",
    "двадцать седьмой", "двадцать восьмой", "двадцать девятый", "тридцатый",
)


def ordinal_ru(n: int) -> str:
    if 1 <= n <= len(_ORDINALS_RU):
        return _ORDINALS_RU[n - 1]
    return f"{n}-й"


def number_key(number: str | None) -> tuple[int, ...]:
    """Числовой ключ для сравнения номеров с дробями и дефис-суффиксами.

    '54.1' -> (54, 1); '105.16-6' -> (105, 16, 6) — порядок корректен:
    (105, 16) < (105, 16, 6) < (105, 17).
    """
    if number is None:
        return ()
    key: list[int] = []
    for part in str(number).split("."):
        for sub in part.split("-"):
            try:
                key.append(int(sub))
            except ValueError:
                return ()
    return tuple(key)


@dataclass
class Unit:
    """Структурная единица кодекса в конкретном дереве разбора."""

    kind: str
    number: str | None = None  # "164", "164.1", "3"; у абзаца — порядковый номер
    title: str | None = None   # заголовок (раздел / глава / статья)
    paragraphs: list[str] = field(default_factory=list)  # собственный текст по абзацам

    # заполняются при сплющивании дерева
    unit_id: str = ""
    parent_unit_id: str | None = None
    label: str = ""
    edit_note: str | None = None  # «(в ред. Федерального закона от ...)»; несколько — через \n
    notes: list[tuple[int, str]] = field(default_factory=list)  # (после какого абзаца, пометка)
    duplicate_of: str | None = None  # канонический ID, если ID совпал с ранее встреченным
    paren_point: bool = False  # пункт, поданный маркером «N)» прямо под статьёй (ст. 217 НК)
    inferred: bool = False  # единица синтезирована парсером (потерянный банком маркер «1.»)
    children: list["Unit"] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    def text_hash(self) -> str:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def walk(self):
        """Обход в глубину: сама единица, затем потомки."""
        yield self
        for child in self.children:
            yield from child.walk()


def build_label(path: list[Unit], act_short: str = "НК РФ") -> str:
    """Человекочитаемое обозначение по цепочке родителей, как в юридической нотации.

    «подпункт 1 пункта 3 статьи 164 НК РФ», «абзац второй пункта 1 статьи 21 НК РФ».
    path — от корня (часть кодекса) к самой единице; предки — в родительном падеже.
    """
    leaf = path[-1]
    if leaf.kind == "part":
        return f"{act_short}, часть {leaf.number}"
    if leaf.kind in ("section", "chapter", "article"):
        return f"{KIND_RU[leaf.kind]} {leaf.number} {act_short}"

    if leaf.kind == "paragraph":
        try:
            head = "абзац " + ordinal_ru(int(leaf.number))
        except (TypeError, ValueError):
            head = "абзац " + str(leaf.number)
    else:
        head = f"{KIND_RU[leaf.kind]} {leaf.number}"

    words = [head]
    for node in reversed(path[:-1]):
        if node.kind == "part":
            break
        if node.kind == "article":
            words.append(f"статьи {node.number}")
            break
        if node.kind == "point":
            words.append(f"пункта {node.number}")
        elif node.kind == "subpoint":
            words.append(f"подпункта {node.number}")
        elif node.kind == "paragraph":
            try:
                words.append("абзаца " + ordinal_ru(int(node.number)))
            except (TypeError, ValueError):
                words.append("абзаца " + str(node.number))
    words.append(act_short)
    return " ".join(words)
