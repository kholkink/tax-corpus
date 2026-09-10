"""Структурный парсер кодекса: сырой текст -> дерево единиц -> плоские записи.

Правила грамматики НК РФ:
  Часть -> Раздел (римские) -> [Подраздел] -> Глава -> Статья -> Пункт -> Подпункт
Пункты нумеруются «1.», «2.1.» (дробные), подпункты — «1)».
Заголовки разделов/глав/статей могут стоять в том же абзаце, что и маркер,
или в следующем абзаце.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import KIND_RU, Unit, build_label, number_key
from .normalize import normalize_text, split_paragraphs

# --- маркеры структуры (проверяются по началу абзаца) ---
# в официальных консолидированных текстах заголовки набраны капсом:
# «РАЗДЕЛ I. …», «ГЛАВА 2. …», поэтому у контейнеров два регистровых варианта
RE_PART = re.compile(
    r"^(?:Часть\s+(Первая|Вторая|Третья|Четвёртая|Четвертая|Пятая|Шестая|\d+)"
    r"|ЧАСТЬ\s+(ПЕРВАЯ|ВТОРАЯ|ТРЕТЬЯ|ЧЕТВЁРТАЯ|ЧЕТВЕРТАЯ|ПЯТАЯ|ШЕСТАЯ|\d+))\.?\s*(.*)$"
)
# банк ГАС пишет «Раздел V. 1.» с пробелом внутри дробного номера (NBSP),
# а иногда NBSP выпадает вовсе: «Раздел V1.» — поэтому номер очищается позже
RE_SECTION = re.compile(r"^(?:Раздел|РАЗДЕЛ)\s+([IVXLCDM]+(?:\.\s?\d+)?|[IVXLCDM]+\d+)\.?\s+(.*)$")
RE_SUBSECTION = re.compile(r"^(?:Подраздел|ПОДРАЗДЕЛ)\s+(\d+)\.?\s+(.*)$")
RE_CHAPTER = re.compile(r"^(?:Глава|ГЛАВА)\s+(\d+(?:\.\d+)?(?:-\d+)?)\.?\s*(.*)$")
# дефисные номера реальны в НК: «статья 25.12-1», «статья 105.16-6»
RE_ARTICLE = re.compile(r"^(?:Статья|СТАТЬЯ)\s+(\d+(?:\.\d+)?(?:-\d+)?)\.?\s*(.*)$")
# подпункты бывают дробными и дефисными: «3.1)», «2.8-1)» (банк: «31)», «28-1)»);
# пункты — только с точкой: «1.», «2.1.», «8.10.»
RE_SUBPOINT = re.compile(r"^(\d+(?:\.\d+)?(?:-\d+)?)\)\s+(.*)$")
# после точки пробел может отсутствовать («1.Налогоплательщиками…»), но за точкой
# не должна идти цифра — иначе это дата или число, а не маркер
# текст пункта начинается с буквы или пометки «<…>», но не с цифры: «1. 61 53 00; 75 02 00;»
# в ст. 333.45 — строка таблицы координат, а не пункт
RE_POINT = re.compile(r"^(\d+(?:\.\d+)?(?:-\d+)?)\.(?!\d)\s*([^\d\s].*)$")

# служебные абзацы-пометки редакции: «(в ред. Федерального закона от ...)»
# и пометки банка ГАС в угловых скобках: «<В новой ред. ...>», «<Введена ...>»,
# «<Изменения: ...>» (конвертер склеивает многоабзацные пометки в один абзац)
# закрывающая «>» у банка иногда теряется: «13. <Утратил силу с 1 июля 2026 г.: … N 425-ФЗ»
RE_EDITION_NOTE = re.compile(r"^(?:\((?:в ред\.|введен|введена|ред\.)[^)]*\)|<.+>?)$")
# заголовок + пометка в одном абзаце: «ТОРГОВЫЙ СБОР <Глава 33 введена …>»
RE_TITLE_NOTE = re.compile(r"^(.*?)\s*(<.+>?)$")
# самостоятельная единица-«огрызок»: «Статья 20. Утратила силу.»
RE_REPEALED = re.compile(r"(?i)^утратил[аи]?\s+силу\.?\s*$")

# при сплющивании ID дробные номера кодируются с «-», чтобы ID был однозначным:
# статья 54.1 -> art54-1, пункт 2.1 -> p2-1
ID_DOT = "-"

MARKER_RES: tuple[re.Pattern, ...] = (
    RE_PART, RE_SECTION, RE_SUBSECTION, RE_CHAPTER, RE_ARTICLE, RE_SUBPOINT, RE_POINT,
)

_NUMBER_WORDS = {
    "Первая": "1", "Вторая": "2", "Третья": "3", "Четвёртая": "4",
    "Четвертая": "4", "Пятая": "5", "Шестая": "6",
    "ПЕРВАЯ": "1", "ВТОРАЯ": "2", "ТРЕТЬЯ": "3", "ЧЕТВЁРТАЯ": "4",
    "ЧЕТВЕРТАЯ": "4", "ПЯТАЯ": "5", "ШЕСТАЯ": "6",
}

# допустимые родители для каждого вида единицы
ALLOWED_PARENTS: dict[str, set[str]] = {
    "part": set(),
    "section": {"part"},
    "subsection": {"section"},
    "chapter": {"part", "section", "subsection"},
    "article": {"chapter", "section", "subsection", "part"},
    "point": {"article"},
    "subpoint": {"point"},
}

# виды, чьи остатки абзацев превращаются в дочерние единицы-абзацы
PARAGRAPH_HOSTS = ("article", "point", "subpoint")


@dataclass
class ParseStats:
    paragraphs_total: int = 0
    noise_lines_removed: int = 0
    units_by_kind: dict[str, int] = field(default_factory=dict)
    edition_notes: int = 0
    titles_from_next_line: int = 0
    stray_subpoints: int = 0
    detokenized: int = 0  # восстановленных дробных номеров («61» -> 6.1)
    duplicate_suffixes: int = 0  # повторных ID, разрешённых суффиксом «@2»
    title_notes: int = 0  # пометок, отделённых от заголовков глав/статей
    article_level_points: int = 0  # «N)» прямо под статьёй, принятых за пункты
    inferred_points: int = 0  # синтезированных «п. 1» вместо потерянного банком маркера
    pending_title_note: str | None = None  # служебное: пометка из последнего заголовка


def _int_part(number: str) -> int:
    """Целая часть номера: «23-1» -> 23, «54.1» -> 54."""
    head = str(number).split(".")[0].split("-")[0]
    try:
        return int(head)
    except ValueError:
        return 0


def _detokenize(number: str, base: int | None, stats: ParseStats) -> str:
    """Банк ГАС сплющивает дробные номера: «6.1» -> «61», «25.13» -> «2513».

    Восстановление по контексту: номер «X» после единицы с целой частью base
    трактуется как base.rest, если X начинается со строки base, а остаток —
    целое >= 1. Настоящие номера продолжают ряд и не начинаются с префикса
    предыдущей единицы («Статья 7» после 6.1, «Статья 12» после 11.3).
    """
    if base is None:
        return number
    # префикс — целая часть предыдущей единицы либо следующий номер: после «8)»
    # идёт «91)» (подп. 9.1 ст. 309, целого «9)» в подаче нет) — это 9.1, а не 91
    for prefix in (str(base), str(base + 1)):
        if number.startswith(prefix) and len(number) > len(prefix):
            rest = number[len(prefix):]
            # остаток — целое, дефисный или многоточечный номер: «2512-1» -> 25.12-1,
            # «34625.1» -> 346.25.1 (иначе рвётся цепочка восстановления до конца главы)
            if re.fullmatch(r"\d+(?:\.\d+)*(?:-\d+)*", rest) and int(re.match(r"\d+", rest).group(0)) >= 1:
                stats.detokenized += 1
                return f"{prefix}.{rest}"
    return number


def _clean_section_number(raw: str) -> str:
    """«V. 1» / «V1» -> «V.1»; римский номер банка после потери NBSP."""
    number = raw.replace(" ", "")
    if (m := re.fullmatch(r"([IVXLCDM]+)(\d+)", number)):
        return f"{m.group(1)}.{m.group(2)}"
    return number


def match_marker(paragraph: str) -> tuple[str, str, str] | None:
    """Абзац -> (вид единицы, номер, остаток абзаца) либо None."""
    if (m := RE_PART.match(paragraph)):
        return "part", m.group(1) or m.group(2) or "1", m.group(3)
    if (m := RE_SECTION.match(paragraph)):
        return "section", _clean_section_number(m.group(1)), m.group(2)
    if (m := RE_SUBSECTION.match(paragraph)):
        return "subsection", m.group(1), m.group(2)
    if (m := RE_CHAPTER.match(paragraph)):
        return "chapter", m.group(1), m.group(2)
    if (m := RE_ARTICLE.match(paragraph)):
        return "article", m.group(1), m.group(2)
    if (m := RE_SUBPOINT.match(paragraph)):
        return "subpoint", m.group(1), m.group(2)
    if (m := RE_POINT.match(paragraph)):
        return "point", m.group(1), m.group(2)
    return None


def _extract_title(unit_kind: str, remainder: str, blocks: list[str],
                   i: int, stats: ParseStats) -> tuple[str | None, str | None, int]:
    """Заголовок контейнера: остаток абзаца или следующий абзац.

    Возвращает (заголовок | None, текст «Утратила силу.» | None, новый индекс):
    «Утратила силу.» заголовком не считается — это текст единицы.
    """
    remainder = remainder.strip()
    candidate = remainder
    if not candidate:
        if i < len(blocks):
            nxt = blocks[i]
            if nxt and not match_marker(nxt) and not RE_EDITION_NOTE.match(nxt):
                candidate = nxt
                i += 1
                stats.titles_from_next_line += 1
    # пометка банка склеена с заголовком: «АКЦИЗЫ <Глава введена Федеральным законом …>»
    if candidate and (m := RE_TITLE_NOTE.match(candidate)):
        candidate, note = m.group(1).strip(), m.group(2).strip()
        stats.pending_title_note = note
    if candidate and RE_REPEALED.match(candidate):
        return None, candidate, i
    return candidate or None, None, i


def _add_edit_note(unit: Unit, note: str, stats: ParseStats) -> None:
    """Пометки редакции накапливаются (одна единица может иметь несколько),
    разделитель — перевод строки; amendments.py разбирает их по отдельности."""
    unit.edit_note = f"{unit.edit_note}\n{note}" if unit.edit_note else note
    stats.edition_notes += 1


def parse_code(raw_text: str, act_code: str = "nk1") -> tuple[Unit, ParseStats]:
    """Строит дерево единиц из сырого текста кодекса."""
    text, noise = normalize_text(raw_text)
    stats = ParseStats(noise_lines_removed=noise)
    blocks = split_paragraphs(text, MARKER_RES)
    stats.paragraphs_total = len(blocks)

    root = Unit(kind="part", number="1")
    stack: list[Unit] = [root]

    # для восстановления сплющенных дробных номеров: целая часть предыдущей
    # главы/статьи/пункта/подпункта (нумерация статей сквозная, остальных — в родителе)
    chapter_base: int | None = None
    article_base: int | None = None
    point_base: int | None = None
    subpoint_base: int | None = None

    i = 0
    while i < len(blocks):
        block = blocks[i]
        i += 1

        marker = match_marker(block)

        if marker is None:
            if RE_EDITION_NOTE.match(block):
                _add_edit_note(stack[-1], block, stats)
            else:
                stack[-1].paragraphs.append(block)
            continue

        kind, number, remainder = marker

        if kind == "chapter":
            number = _detokenize(number, chapter_base, stats)
            chapter_base = _int_part(number)
        elif kind == "article":
            number = _detokenize(number, article_base, stats)
            article_base = _int_part(number)
            point_base = None
        elif kind == "point":
            number = _detokenize(number, point_base, stats)
            point_base = _int_part(number)
            subpoint_base = None
        elif kind == "subpoint":
            # дробные подпункты реальны: «подпункт 3.1» банка подан как «31)»
            number = _detokenize(number, subpoint_base, stats)
            subpoint_base = _int_part(number)

        if kind == "part":
            # корень дерева уже является частью кодекса: заголовок части
            # («ЧАСТЬ ПЕРВАЯ») наполняет корень, а не создаёт новую единицу
            title, repealed_text, i = _extract_title(kind, remainder, blocks, i, stats)
            if repealed_text:
                root.paragraphs.append(repealed_text)
            if root.title is None:
                root.title = title
            stack = [root]
            continue

        paren_point = False
        if kind == "subpoint":
            enclosing_point = next((u for u in reversed(stack) if u.kind == "point"), None)
            if enclosing_point is None or enclosing_point.paren_point:
                if any(u.kind == "article" for u in stack):
                    # «1) …», «18.1) …» прямо под статьёй (ст. 217, 270 НК): юридически это
                    # пункты — цитируются «п. 18.1 ст. 217»; следующие «N)» — их соседи,
                    # а не подпункты; номер восстанавливается как у пункта
                    kind, paren_point = "point", True
                    number = _detokenize(marker[1], point_base, stats)
                    point_base = _int_part(number)
                    subpoint_base = None
                    stats.article_level_points += 1
                else:
                    # «N)» вне статьи — грамматическая ошибка источника; сохраняем как текст
                    stack[-1].paragraphs.append(block)
                    stats.stray_subpoints += 1
                    continue

        while stack and stack[-1].kind not in ALLOWED_PARENTS[kind]:
            stack.pop()
        if not stack:
            stack = [root]

        unit = Unit(kind=kind, number=number, paren_point=paren_point)
        if kind in ("part", "section", "subsection", "chapter", "article"):
            stats.pending_title_note = None
            unit.title, repealed_text, i = _extract_title(kind, remainder, blocks, i, stats)
            if repealed_text:
                unit.paragraphs.append(repealed_text)
            if stats.pending_title_note:
                _add_edit_note(unit, stats.pending_title_note, stats)
                stats.title_notes += 1
                stats.pending_title_note = None
        elif RE_EDITION_NOTE.match(remainder.strip()):
            # «13. <Утратил силу с 1 января 2023 г.: …>» — пометка в одном абзаце
            # с маркером: это история единицы, а не текст нормы
            _add_edit_note(unit, remainder.strip(), stats)
        elif remainder.strip():
            unit.paragraphs.append(remainder.strip())

        stack[-1].children.append(unit)
        stack.append(unit)
        stats.units_by_kind[kind] = stats.units_by_kind.get(kind, 0) + 1

    return root, stats


def _infer_lost_point_one(root: Unit, stats: ParseStats) -> None:
    """Банк иногда теряет маркер «1.» (ст. 150 НК): статья начинается вводным абзацем,
    затем идут «1) … 23)», затем «2. <Утратил силу…>». Если «N)»-пункты статьи
    предшествуют пункту с точкой и номером > 1, они — подпункты потерянного п. 1:
    синтезируем его (inferred=True) из вводных абзацев статьи."""
    for article in root.walk():
        if article.kind != "article":
            continue
        points = [c for c in article.children if c.kind == "point"]
        paren = [c for c in points if c.paren_point]
        dotted = [c for c in points if not c.paren_point]
        if not paren or not dotted:
            continue
        first_dotted = dotted[0]
        if number_key(first_dotted.number) <= (1,) or \
                article.children.index(paren[0]) > article.children.index(first_dotted):
            continue
        inferred = Unit(kind="point", number="1", inferred=True,
                        paragraphs=list(article.paragraphs))
        article.paragraphs = []
        for item in paren:
            item.kind = "subpoint"
            item.paren_point = False
            inferred.children.append(item)
        article.children = [c for c in article.children if c not in paren]
        article.children.insert(0, inferred)
        stats.inferred_points += 1


def _attach_paragraph_units(root: Unit) -> None:
    """Собственные абзацы статей/пунктов/подпунктов -> дочерние единицы-абзацы.

    Абзацы вставляются перед существующими детьми, чтобы сохранить порядок
    документа (преамбула статьи, затем её пункты).
    """
    for unit in root.walk():
        if unit.kind in PARAGRAPH_HOSTS and unit.paragraphs:
            paragraph_units = [
                Unit(kind="paragraph", number=str(idx + 1), paragraphs=[p])
                for idx, p in enumerate(unit.paragraphs)
            ]
            unit.children = paragraph_units + unit.children


def _fractional(number: str | None) -> str:
    return (number or "").replace(".", ID_DOT)


def build_unit_id(path: list[Unit], act_code: str) -> str:
    """Канонический ID: <акт>.<глава|раздел>.art<p>.p<n>.sp<n>.ab<n>.

    ID не зависит от перенумерации соседей: путь задаётся номерами предков,
    а не порядковыми индексами. Пример: nk1.ch6.art14.p1.sp2.
    """
    leaf = path[-1]
    if leaf.kind == "part":
        return act_code

    chapter = next((u for u in path if u.kind == "chapter"), None)
    section = next((u for u in path if u.kind == "section"), None)
    subsection = next((u for u in path if u.kind == "subsection"), None)

    if chapter is not None:
        head = f"{act_code}.ch{_fractional(chapter.number)}"
    elif section is not None:
        head = f"{act_code}.r{_fractional(section.number.lower())}"
    elif subsection is not None:
        head = f"{act_code}.sub{_fractional(subsection.number)}"
    else:
        head = act_code

    tail: list[str] = []
    for node in path:
        if node.kind == "article":
            tail.append(f"art{_fractional(node.number)}")
        elif node.kind == "point":
            tail.append(f"p{_fractional(node.number)}")
        elif node.kind == "subpoint":
            tail.append(f"sp{_fractional(node.number)}")
        elif node.kind == "paragraph":
            tail.append(f"ab{node.number}")
    return head + ("." + ".".join(tail) if tail else "")


def full_text(node: Unit) -> str:
    """Текст единицы целиком, как её читает юрист: собственные абзацы плюс
    вложенные пункты/подпункты в порядке документа (абзацы-дети не дублируются —
    они и есть собственные абзацы). Для поиска и get_unit; `text` остаётся
    «доказательным» текстом самой единицы."""
    parts = [node.text] if node.paragraphs else []
    for child in node.children:
        if child.kind == "paragraph":
            continue
        child_text = full_text(child)
        if child_text:
            head = ""
            if child.kind == "point" and child.number:
                head = f"{child.number}. "
            elif child.kind == "subpoint" and child.number:
                head = f"{child.number}) "
            parts.append(head + child_text)
    return "\n\n".join(parts)


def build_context(path: list[Unit], act_short: str = "НК РФ") -> str:
    """Контекст заголовков для чанка (слой 4 плана): «НК РФ, часть 1, раздел V
    «Налоговая декларация и налоговый контроль», глава 14 «Налоговый контроль»,
    статья 88 «Камеральная налоговая проверка»»."""
    parts: list[str] = []
    for node in path:
        if node.kind == "part":
            parts.append(f"{act_short}, часть {node.number}")
        elif node.kind in ("section", "subsection", "chapter", "article"):
            head = f"{KIND_RU[node.kind]} {node.number}"
            parts.append(f"{head} «{node.title}»" if node.title else head)
    return ", ".join(parts)


def is_chunk(node: Unit) -> bool:
    """Единица поиска: пункт/подпункт; статья — только если у неё нет пунктов."""
    if node.kind in ("point", "subpoint"):
        return True
    if node.kind == "article":
        return not any(c.kind == "point" for c in node.children)
    return False


def unit_record(node: Unit, act_code: str, path: list[Unit] | None = None) -> dict:
    return {
        "unit_id": node.unit_id,
        "act": act_code,
        "kind": node.kind,
        "parent_unit_id": node.parent_unit_id,
        "number": node.number,
        "title": node.title,
        "label": node.label,
        "context": build_context(path) if path else "",
        "is_chunk": is_chunk(node),
        "text": node.text,
        "text_hash": node.text_hash(),
        "full_text": full_text(node) if node.kind in PARAGRAPH_HOSTS else node.text,
        "paragraphs": list(node.paragraphs),
        "edit_note": node.edit_note,
        "duplicate_of": node.duplicate_of,
        "inferred": node.inferred,
    }


def flatten(root: Unit, act_code: str) -> list[dict]:
    """Дерево -> плоский список записей в порядке документа (родители раньше детей).

    Канонический ID обязан быть уникальным. Если источник (подача банка)
    содержит повторный маркер с тем же путём («Статья 88» с двумя «11.»),
    повторение получает суффикс «@2», «@3» и пометку duplicate_of — данные
    не теряются, конфликт разрешается ручной сверкой.
    """
    records: list[dict] = []
    seen: set[str] = set()

    def rec(node: Unit, path: list[Unit]) -> None:
        path = [*path, node]
        node.unit_id = build_unit_id(path, act_code)
        node.parent_unit_id = path[-2].unit_id if len(path) > 1 else None
        node.label = build_label(path)
        if node.unit_id in seen:
            canonical = node.unit_id
            suffix = 2
            while f"{canonical}@{suffix}" in seen:
                suffix += 1
            node.unit_id = f"{canonical}@{suffix}"
            node.duplicate_of = canonical
        seen.add(node.unit_id)
        records.append(unit_record(node, act_code, path))
        for child in node.children:
            rec(child, path)

    rec(root, [])
    return records


def parse_document(raw_text: str, act_code: str = "nk1") -> tuple[Unit, list[dict], ParseStats]:
    """Полный проход: нормализация -> дерево -> абзацы -> плоские записи."""
    root, stats = parse_code(raw_text, act_code)
    _infer_lost_point_one(root, stats)
    _attach_paragraph_units(root)
    records = flatten(root, act_code)
    stats.duplicate_suffixes = sum(1 for r in records if r["duplicate_of"])
    return root, records, stats
