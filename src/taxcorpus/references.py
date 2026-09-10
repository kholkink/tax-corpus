"""Извлечение явных ссылок из текста единицы кодекса (только stdlib re).

Внутренние ссылки — на нормы самого Кодекса (подпункт/пункт/абзац/статья/
глава/раздел в любом падеже, дробные номера, абзацы порядковыми словами,
координатные списки), внешние — на федеральные законы, постановления
Правительства РФ и ведомственные акты (приказы ФНС, письма Минфина).
Структурированная цель (target) резолвится в unit_id отдельным слоем.
"""

from __future__ import annotations

import re

# --- базовые фрагменты шаблонов ---
RE_DATE = r"\d{1,2}\.\d{1,2}\.\d{4}"  # числовая дата ДД.ММ.ГГГГ
RE_NUM = r"\d+(?:\.\d+)?"  # номер с дробной частью: «2», «54.1»
RE_WS = r"[^\S\r\n]+"  # горизонтальный пробел, не перевод строки
RE_NO = r"№[^\S\r\n]*"  # знак номера с необязательным пробелом
RE_AGNUM = r"[0-9а-яёa-z@/\-]+"  # номер ведомственного акта: «ММВ-7-6/398@»
RE_NUM_FIND = re.compile(RE_NUM)

# элемент списка — номер или диапазон: «3», «1 - 3», «2.1 - 2.4»
RE_NUM_ITEM = rf"{RE_NUM}(?:[^\S\r\n]*[-–—][^\S\r\n]*{RE_NUM})?"
# координатный список: «1 и 3», «1, 2 и 4», «1 - 3 и 5»
RE_NUM_LIST = (
    rf"(?:{RE_NUM_ITEM}(?:[^\S\r\n]*,[^\S\r\n]*{RE_NUM_ITEM})*"
    rf"[^\S\r\n]*(?:и|или)[^\S\r\n]*{RE_NUM_ITEM}|{RE_NUM_ITEM})"
)
RE_NUM_ITEM_FIND = re.compile(RE_NUM_ITEM)
RE_ROMAN = r"[ivxlcdm]{1,6}\b"  # номер раздела — римские цифры

# слова-маркеры внутренних ссылок с падежными окончаниями
_W_SUB = r"подпункт(?:ами|ах|ам|ом|ов|ы|а|у|е)?\b"
_W_PAR = r"абзац(?:ем|ами|ах|ам|ов|ы|а|у|е)?\b"
_W_PNT = r"пункт(?:ами|ах|ам|ом|ов|ы|а|у|е)?\b"
_W_ART = r"стат(?:ьями|ьях|ьям|ьей|ьёй|ья|ьи|ье|ью|ей)\b"
_W_CHP = r"глав(?:ами|ах|ам|ой|ов|ы|а|е|у)?\b"
_W_SEC = r"раздел(?:ами|ах|ам|ом|ов|ы|а|у|е)?\b"

# порядковые слова абзацев: «втором», «двадцать первом»
_RE_ORDINAL = (
    r"(?:двадцать[^\S\r\n]+(?:девят|восьм|седьм|шест|пят|четвёрт|четверт|трет|втор|перв)"
    r"|тридцать[^\S\r\n]+(?:девят|восьм|седьм|шест|пят|четвёрт|четверт|трет|втор|перв)"
    r"|одиннадцат|двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат"
    r"|восемнадцат|девятнадцат|тридцат|двадцат|десят|девят|восьм|седьм|шест|пят"
    r"|четвёрт|четверт|трет|втор|перв)[а-яё]+"
)
# список порядковых: «втором и третьем», «втором - четвертом», «первом, третьем и пятом»
_RE_ORDINAL_ITEM = rf"{_RE_ORDINAL}(?:[^\S\r\n]*[-–—][^\S\r\n]*{_RE_ORDINAL})?"
_RE_ORDINAL_LIST = (
    rf"(?:{_RE_ORDINAL_ITEM}(?:[^\S\r\n]*,[^\S\r\n]*{_RE_ORDINAL_ITEM})*"
    rf"[^\S\r\n]*(?:и|или)[^\S\r\n]*{_RE_ORDINAL_ITEM}|{_RE_ORDINAL_ITEM})"
)

# цепочка внутренней ссылки; в тексте НК компоненты идут от частного к общему:
# «подпунктом 1 пункта 1 статьи 23», «абзаце втором пункта 1 статьи 346.19»
RE_INTERNAL = re.compile(
    r"(?<!\w)(?=подпункт|абзац|пункт|стат|глав|раздел)"
    rf"(?:(?P<sub>{_W_SUB}){RE_WS}(?P<sub_num>{RE_NUM_LIST}))?"
    rf"(?:[^\S\r\n]*(?P<par>{_W_PAR}){RE_WS}(?P<par_ord>{_RE_ORDINAL_LIST}))?"
    rf"(?:[^\S\r\n]*(?P<pnt>{_W_PNT}){RE_WS}(?P<pnt_num>{RE_NUM_LIST}))?"
    rf"(?:[^\S\r\n]*(?P<art>{_W_ART}){RE_WS}(?P<art_num>{RE_NUM_LIST}))?"
    rf"(?:[^\S\r\n]*(?P<chap>{_W_CHP}){RE_WS}(?P<chap_num>{RE_NUM}))?"
    rf"(?:[^\S\r\n]*(?P<sec>{_W_SEC}){RE_WS}(?P<sec_num>{RE_ROMAN}))?"
    # контекст: «абзаце первом настоящего пункта», «пункте 2 настоящей статьи»
    r"(?:[^\S\r\n]+настоящ\w+[^\S\r\n]+(?P<rel>пункта|подпункта|статьи|главы|раздела|Кодекса))?",
    re.IGNORECASE,
)

_REL_KIND = {"пункта": "point", "подпункта": "subpoint", "статьи": "article",
             "главы": "chapter", "раздела": "section", "кодекса": "act"}

# внешние акты: головная конструкция + одна или несколько пар «от ДАТА № НОМЕР»;
# перечисление через «и»/«,» продолжает ту же конструкцию
_PAIR = rf"{RE_WS}от{RE_WS}{RE_DATE}{RE_WS}{RE_NO}"
_AND_PAIR = (
    rf"(?:(?:[^\S\r\n]*,[^\S\r\n]*|[^\S\r\n]+и[^\S\r\n]+)"
    rf"от{RE_WS}{RE_DATE}{RE_WS}{RE_NO}"
)

RE_FEDERAL = re.compile(
    r"(?<!\w)федеральн\w+[^\S\r\n]+закон\w*"
    rf"(?:{_PAIR}\d+-ФЗ{_AND_PAIR}\d+-ФЗ)*)",
    re.IGNORECASE,
)
RE_DECREE = re.compile(
    r"(?<!\w)постановлени\w+[^\S\r\n]+Правительства[^\S\r\n]+"
    r"(?:Российской[^\S\r\n]+Федерации|РФ|России)\b"
    rf"(?:{_PAIR}\d+{_AND_PAIR}\d+)*)",
    re.IGNORECASE,
)
RE_AGENCY = re.compile(
    r"(?<!\w)(?:приказ|письм|распоряжени|указани|информаци|сообщени|положени)\w*"
    rf"(?:[^\S\r\n]+[а-яё-]+){{0,6}}?"
    r"[^\S\r\n]+(?P<agency>Минэкономразвития|Минфина|Минфин|Минюста|Минюст|ФНС|ФТС|ФСТ)\b"
    r"(?:[^\S\r\n]+России\b)?"
    rf"(?:{_PAIR}{RE_AGNUM}{_AND_PAIR}{RE_AGNUM})*)",
    re.IGNORECASE,
)

# сразу после цепочки «статьи N» стоит имя ДРУГОГО акта: это не ссылка на НК.
# «настоящего Кодекса» и «Налогового кодекса» сюда не входят.
RE_FOREIGN_ACT_AFTER = re.compile(
    r"[^\S\r\n]+("
    r"федеральн\w+[^\S\r\n]+закон\w*"
    r"|закон\w*[^\S\r\n]+(?:Российской[^\S\r\n]+Федерации|РФ|СССР|РСФСР)"
    r"|(?:Гражданск|Бюджетн|Уголовн|Трудов|Таможенн|Арбитражн|Земельн|Жилищн|Семейн"
    r"|Градостроительн|Лесн|Водн|Воздушн|Уголовно-процессуальн"
    r"|Гражданск\w+[^\S\r\n]+процессуальн)\w*[^\S\r\n]+кодекс\w*"
    r"|кодекс\w*[^\S\r\n]+Российской[^\S\r\n]+Федерации[^\S\r\n]+об[^\S\r\n]+\w+"
    r"|Договор\w*|Соглашени\w*|Конвенци\w*|Устав\w*|Протокол\w*"
    r")",
    re.IGNORECASE,
)

# пары «дата–номер» внутри уже найденной конструкции
RE_PAIR_FEDERAL = re.compile(rf"от{RE_WS}({RE_DATE}){RE_WS}{RE_NO}(\d+-ФЗ)", re.IGNORECASE)
RE_PAIR_DECREE = re.compile(rf"от{RE_WS}({RE_DATE}){RE_WS}{RE_NO}(\d+)", re.IGNORECASE)
RE_PAIR_AGENCY = re.compile(rf"от{RE_WS}({RE_DATE}){RE_WS}{RE_NO}({RE_AGNUM})", re.IGNORECASE)

_ORDINAL_STEMS: tuple[tuple[str, int], ...] = (
    ("девятнадцат", 19), ("восемнадцат", 18), ("семнадцат", 17),
    ("шестнадцат", 16), ("пятнадцат", 15), ("четырнадцат", 14),
    ("тринадцат", 13), ("двенадцат", 12), ("одиннадцат", 11),
    ("тридцат", 30), ("двадцат", 20), ("десят", 10), ("девят", 9),
    ("восьм", 8), ("седьм", 7), ("шест", 6), ("пят", 5),
    ("четвёрт", 4), ("четверт", 4), ("трет", 3), ("втор", 2), ("перв", 1),
)

_AGENCY_CANON = {
    "минфина": "Минфин", "минфин": "Минфин",
    "минюста": "Минюст", "минюст": "Минюст",
    "минэкономразвития": "Минэкономразвития",
    "фнс": "ФНС", "фтс": "ФТС", "фст": "ФСТ",
}

# порядок ключей target по вложенности единицы
_UNIT_ORDER = ("section", "chapter", "article", "point", "subpoint")


def _reference(unit_id: str, kind: str, raw_citation: str, target: dict) -> dict:
    return {
        "from_unit_id": unit_id,
        "kind": kind,
        "raw_citation": raw_citation,
        "target": target,
        "extracted_by": "regex",
        "confidence": 1.0,
    }


def _expand_range(start: str, end: str, limit: int = 30) -> list[str]:
    """«1 - 3» -> [1, 2, 3]; дробные или слишком длинные диапазоны — только концы."""
    if start.isdigit() and end.isdigit() and 0 < int(end) - int(start) <= limit:
        return [str(n) for n in range(int(start), int(end) + 1)]
    return [start, end]


def _numbers(fragment: str | None) -> list[str]:
    """Список/диапазон номеров -> плоский список: «1, 2 - 4 и 7» -> [1, 2, 3, 4, 7]."""
    if not fragment:
        return []
    out: list[str] = []
    for item in RE_NUM_ITEM_FIND.findall(fragment):
        nums = RE_NUM_FIND.findall(item)
        out.extend(_expand_range(nums[0], nums[1]) if len(nums) == 2 else nums)
    return out


def _stem_value(word: str) -> int | None:
    for stem, value in _ORDINAL_STEMS:
        if word.startswith(stem):
            return value
    return None


def _ordinal_value(word: str | None) -> int | None:
    """Порядковое слово («втором», «двадцать первом») -> число."""
    if not word:
        return None
    w = word.lower()
    tens = re.match(r"(двадцать|тридцать)[^\S\r\n]+(\S+)", w)
    if tens:
        rest = _stem_value(tens.group(2))
        if rest is None:
            return None
        return (20 if tens.group(1) == "двадцать" else 30) + rest
    return _stem_value(w)


_RE_ORDINAL_FIND = re.compile(_RE_ORDINAL, re.IGNORECASE)


def _ordinal_values(fragment: str | None) -> list[int]:
    """Список/диапазон порядковых -> числа: «втором - четвертом и шестом» -> [2, 3, 4, 6]."""
    if not fragment:
        return []
    out: list[int] = []
    for item in re.split(r"[^\S\r\n]*,[^\S\r\n]*|[^\S\r\n]+(?:и|или)[^\S\r\n]+", fragment):
        words = _RE_ORDINAL_FIND.findall(item)
        values = [v for v in (_ordinal_value(w) for w in words) if v is not None]
        if len(values) == 2 and re.search(r"[-–—]", item) and 0 < values[1] - values[0] <= 30:
            out.extend(range(values[0], values[1] + 1))
        else:
            out.extend(values)
    return out


def _internal_records(unit_id: str, match: re.Match) -> list[dict]:
    numbers = {
        "section": [match.group("sec_num").upper()] if match.group("sec_num") else [],
        "chapter": _numbers(match.group("chap_num")),
        "article": _numbers(match.group("art_num")),
        "point": _numbers(match.group("pnt_num")),
        "subpoint": _numbers(match.group("sub_num")),
    }
    ordinals = _ordinal_values(match.group("par_ord"))
    present = [key for key in _UNIT_ORDER if numbers[key]]
    if not present and not ordinals:
        return []
    combos: list[dict] = [{}]
    for key in present:
        combos = [{**combo, key: value} for combo in combos for value in numbers[key]]
    if ordinals:
        combos = [{**combo, "paragraph_ordinal": o} for combo in combos for o in ordinals]
    rel = _REL_KIND.get((match.group("rel") or "").lower())
    records = []
    for combo in combos:
        target = {"type": "unit", **combo}
        if rel and rel != "act":
            target["relative_to"] = rel  # база контекстной ссылки — предок источника этого вида
        records.append(_reference(unit_id, "internal_citation", match.group(0), target))
    return records


def _act_records(unit_id: str, kind: str, raw_citation: str,
                 pairs: list[tuple[str, str]], agency: str | None = None) -> list[dict]:
    records = []
    for law_date, law_number in pairs:
        target: dict = {"type": "act", "law_date": law_date, "law_number": law_number}
        if agency:
            target["agency"] = agency
        records.append(_reference(unit_id, kind, raw_citation, target))
    return records


def _agency_records(unit_id: str, match: re.Match) -> list[dict]:
    raw = match.group("agency")
    agency = _AGENCY_CANON.get(raw.lower(), raw.upper())
    pairs = [(law_date, law_number)
             for law_date, law_number in RE_PAIR_AGENCY.findall(match.group(0))
             if re.search(r"\d", law_number)]
    return _act_records(unit_id, "external_agency_act", match.group(0), pairs, agency)


def extract_references(unit_id: str, text: str) -> list[dict]:
    """Текст единицы -> список ссылок в порядке вхождения.

    Координатный список («пунктами 1 и 3 статьи 45») и перечисление актов
    («...от ... № 210-ФЗ и от ... № 325-ФЗ») дают по записи на каждый номер
    с одинаковым raw_citation.
    """
    found: list[tuple[int, list[dict]]] = []
    for match in RE_INTERNAL.finditer(text):
        if match.start() >= match.end():
            continue
        foreign = RE_FOREIGN_ACT_AFTER.match(text, match.end())
        if foreign:
            # «статьи 10 Федерального закона "О защите…"» — норма другого акта,
            # в НК не резолвится; хранится как внешняя ссылка с низкой уверенностью
            raw = text[match.start():foreign.end()]
            target = {"type": "act", "act_name": foreign.group(1).strip(),
                      "cited_unit": match.group(0)}
            record = _reference(unit_id, "external_act_unit", raw, target)
            record["confidence"] = 0.6
            found.append((match.start(), [record]))
            continue
        found.append((match.start(), _internal_records(unit_id, match)))
    for match in RE_FEDERAL.finditer(text):
        found.append((match.start(), _act_records(
            unit_id, "external_federal_law", match.group(0),
            RE_PAIR_FEDERAL.findall(match.group(0)))))
    for match in RE_DECREE.finditer(text):
        found.append((match.start(), _act_records(
            unit_id, "external_gov_decree", match.group(0),
            RE_PAIR_DECREE.findall(match.group(0)))))
    for match in RE_AGENCY.finditer(text):
        found.append((match.start(), _agency_records(unit_id, match)))
    found.sort(key=lambda item: item[0])
    return [record for _, records in found for record in records]
