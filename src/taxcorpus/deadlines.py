"""Расчёт сроков по ст. 6.1 НК РФ (инструмент compute_deadline слоя 5).

Детерминизм важнее генерации: срок считает код, а каждая применённая норма
возвращается ссылкой на единицу корпуса (nk1.ch1.art6-1.pN), чтобы агент
цитировал, а не «вспоминал». Правила — по тексту редакции в корпусе:

  п. 2  течение срока начинается на следующий день после даты/события;
  п. 3  годами — истекает в соответствующие месяц и число последнего года;
  п. 4  кварталами — в последний день последнего месяца срока (квартал = 3 мес.);
  п. 5  месяцами — в соответствующее число последнего месяца; нет такого числа —
        в последний день месяца;
  п. 6  днями — в рабочих днях, если не сказано «календарных»;
  п. 7  последний день выпал на выходной/праздничный — ближайший рабочий день;
  п. 8  действие может быть совершено до 24 часов последнего дня.

Календарь v0: суббота/воскресенье + нерабочие праздничные дни ст. 112 ТК РФ.
Переносы выходных постановлениями Правительства на конкретный год подключаются
файлом data/calendar/<год>.json ({"holidays": [...], "workdays": [...]});
без него результат помечается calendar_note.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ART = "nk1.ch1.art6-1"
RULES = {
    "start": f"{ART}.p2",
    "years": f"{ART}.p3",
    "quarters": f"{ART}.p4",
    "months": f"{ART}.p5",
    "days": f"{ART}.p6",
    "shift": f"{ART}.p7",
    "until_midnight": f"{ART}.p8",
}

# нерабочие праздничные дни (ст. 112 ТК РФ): (месяц, день)
FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
    (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4),
)

UNITS = ("days", "calendar_days", "months", "quarters", "years")


@dataclass
class ProductionCalendar:
    """Рабочие/нерабочие дни. extra_holidays/extra_workdays — переносы конкретного года."""

    extra_holidays: set[date] = field(default_factory=set)
    extra_workdays: set[date] = field(default_factory=set)
    years_loaded: set[int] = field(default_factory=set)

    @classmethod
    def load(cls, calendar_dir: str | Path | None = None) -> "ProductionCalendar":
        cal = cls()
        root = Path(calendar_dir) if calendar_dir else \
            Path(__file__).resolve().parents[2] / "data" / "calendar"
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    year = int(path.stem)
                except (ValueError, json.JSONDecodeError):
                    continue
                cal.extra_holidays |= {date.fromisoformat(d) for d in data.get("holidays", [])}
                cal.extra_workdays |= {date.fromisoformat(d) for d in data.get("workdays", [])}
                cal.years_loaded.add(year)
        return cal

    def is_holiday(self, d: date) -> bool:
        return (d.month, d.day) in FIXED_HOLIDAYS or d in self.extra_holidays

    def is_working_day(self, d: date) -> bool:
        if d in self.extra_workdays:
            return True
        if d in self.extra_holidays or self.is_holiday(d):
            return False
        return d.weekday() < 5


@dataclass
class DeadlineResult:
    start_event: date
    amount: int
    unit: str
    counting_from: date        # первый день течения срока (п. 2)
    nominal_end: date          # окончание до переноса по п. 7
    end: date                  # окончание с учётом п. 7
    shifted: bool
    applied: list[str]         # unit_id применённых норм в порядке применения
    steps: list[str]           # человекочитаемый след расчёта
    calendar_note: str | None  # предупреждение о неполном календаре


def _add_months(d: date, months: int) -> tuple[date, bool]:
    """Дата через months месяцев с тем же числом; (дата, было ли усечение до конца месяца)."""
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    if d.day > last:
        return date(year, month, last), True
    return date(year, month, d.day), False


def compute_deadline(start_event: date, amount: int, unit: str,
                     cal: ProductionCalendar | None = None) -> DeadlineResult:
    """Окончание срока, начавшегося с события start_event, длиной amount единиц unit.

    unit: days (рабочие, п. 6) | calendar_days | months | quarters | years.
    """
    if unit not in UNITS:
        raise ValueError(f"unit должен быть одним из {UNITS}")
    if amount <= 0:
        raise ValueError("amount должен быть положительным")
    cal = cal or ProductionCalendar.load()

    applied: list[str] = [RULES["start"]]
    counting_from = start_event + timedelta(days=1)
    steps = [f"течение срока начинается {counting_from.isoformat()} — на следующий день "
             f"после {start_event.isoformat()} (п. 2 ст. 6.1)"]

    if unit == "days":
        applied.append(RULES["days"])
        d, counted = counting_from - timedelta(days=1), 0
        while counted < amount:
            d += timedelta(days=1)
            if cal.is_working_day(d):
                counted += 1
        nominal = d
        steps.append(f"{amount} рабочих дней (срок в днях — в рабочих, п. 6 ст. 6.1): "
                     f"{nominal.isoformat()}")
    elif unit == "calendar_days":
        applied.append(RULES["days"])
        nominal = counting_from + timedelta(days=amount - 1)
        steps.append(f"{amount} календарных дней (п. 6 ст. 6.1): {nominal.isoformat()}")
    elif unit == "months":
        applied.append(RULES["months"])
        nominal, clipped = _add_months(start_event, amount)
        steps.append(f"{amount} мес.: соответствующее число последнего месяца срока "
                     f"(п. 5 ст. 6.1): {nominal.isoformat()}"
                     + (" — такого числа нет, взят последний день месяца" if clipped else ""))
    elif unit == "quarters":
        applied.append(RULES["quarters"])
        via_months, _ = _add_months(start_event, amount * 3)
        last = calendar.monthrange(via_months.year, via_months.month)[1]
        nominal = date(via_months.year, via_months.month, last)
        steps.append(f"{amount} кв. = {amount * 3} мес.; последний день последнего месяца "
                     f"срока (п. 4 ст. 6.1): {nominal.isoformat()}")
    else:  # years
        applied.append(RULES["years"])
        nominal, clipped = _add_months(start_event, amount * 12)
        steps.append(f"{amount} г.: соответствующие месяц и число последнего года "
                     f"(п. 3 ст. 6.1): {nominal.isoformat()}"
                     + (" — 29 февраля отсутствует, взят последний день месяца" if clipped else ""))

    end, shifted = nominal, False
    if not cal.is_working_day(end):
        shifted = True
        applied.append(RULES["shift"])
        while not cal.is_working_day(end):
            end += timedelta(days=1)
        steps.append(f"{nominal.isoformat()} — выходной/праздничный день, окончание "
                     f"переносится на ближайший рабочий день {end.isoformat()} (п. 7 ст. 6.1)")
    applied.append(RULES["until_midnight"])
    steps.append(f"действие может быть совершено до 24 часов {end.isoformat()} (п. 8 ст. 6.1)")

    years = {counting_from.year, end.year}
    missing = sorted(y for y in years if y not in cal.years_loaded)
    note = None
    if missing:
        note = ("производственный календарь без переносов Правительства за "
                + ", ".join(map(str, missing))
                + " (учтены только выходные и праздники ст. 112 ТК РФ); при попадании "
                  "окончания на перенесённый выходной результат может отличаться на 1–3 дня")
    return DeadlineResult(start_event, amount, unit, counting_from, nominal, end, shifted,
                          applied, steps, note)
