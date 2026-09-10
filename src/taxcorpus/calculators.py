"""Калькуляторы с цитатами (F5 плана ПО): пени, штрафы, сроки обжалования, срок давности.

Как compute_deadline: правило берётся из текста нормы в корпусе (якоря проверяются
тестами), каждый шаг расчёта возвращает unit_id применённого пункта. Модель не считает —
считает код (принцип 3 плана).

Пени (ст. 75 НК): п. 3 — за каждый календарный день со дня возникновения недоимки по день
уплаты включительно; п. 4 — физлица 1/300 ключевой ставки, организации 1/300 первые
30 дней и 1/150 далее; п. 5 — 09.03.2022–31.12.2024 организации 1/300; п. 5.1 —
01.01.2025–31.12.2026 организации 1/300 (1–30 день), 1/150 (31–90), 1/300 (с 91-го).
Ключевая ставка — data/parameters/key_rate.json (cbr.ru, задача crawl_key_rate).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .deadlines import ProductionCalendar, compute_deadline

KEY_RATE_PATH = Path(__file__).resolve().parents[2] / "data" / "parameters" / "key_rate.json"

ART75 = "nk1.ch11.art75"
UNITS = {
    "penalty_days": f"{ART75}.p3", "penalty_rate": f"{ART75}.p4", "penalty_2022": f"{ART75}.p5",
    "penalty_2025": f"{ART75}.p5-1",
    "mitigating": "nk1.ch15.art112.p1", "aggravating": "nk1.ch15.art112.p2",
    "reduce": "nk1.ch15.art114.p3", "increase": "nk1.ch15.art114.p4",
    "fine_119": "nk1.ch16.art119.p1", "fine_122": "nk1.ch16.art122.p1",
    "fine_122_willful": "nk1.ch16.art122.p3", "fine_126": "nk1.ch16.art126.p1",
    "objections": "nk1.ch14.art100.p6", "decision_force": "nk1.ch14.art101.p9",
    "appeal": "nk1.ch19.art139-1.p2", "complaint": "nk1.ch19.art139.p2",
    "limitation": "nk1.ch15.art113.p1",
}


@dataclass
class CalcResult:
    name: str
    value: float | dict
    steps: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "steps": self.steps,
                "applied_units": self.applied, "warnings": self.warnings}


# --- ключевая ставка -----------------------------------------------------------------------

class KeyRates:
    def __init__(self, intervals: list[dict], source: str | None = None, last_day: str | None = None):
        self.intervals = sorted(intervals, key=lambda i: i["valid_from"])
        self.source = source
        self.last_day = last_day

    @classmethod
    def load(cls, path: str | Path = KEY_RATE_PATH) -> "KeyRates":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["intervals"], data.get("source_url"), data.get("last_day"))

    def rate_on(self, d: date) -> float | None:
        s = d.isoformat()
        for i in self.intervals:
            if i["valid_from"] <= s and (i["valid_to"] is None or s < i["valid_to"]):
                return float(i["rate"])
        return None


def _fraction_for_org(d: date, delay_day: int) -> tuple[int, str]:
    """(знаменатель доли ключевой ставки, unit_id правила) для организации на день d."""
    if date(2022, 3, 9) <= d <= date(2024, 12, 31):
        return 300, UNITS["penalty_2022"]
    if date(2025, 1, 1) <= d <= date(2026, 12, 31):
        if delay_day <= 30:
            return 300, UNITS["penalty_2025"]
        if delay_day <= 90:
            return 150, UNITS["penalty_2025"]
        return 300, UNITS["penalty_2025"]
    return (300 if delay_day <= 30 else 150), UNITS["penalty_rate"]


def compute_penalty(amount: float, due_date: date, paid_date: date, taxpayer: str = "organization",
                    key_rates: KeyRates | None = None) -> CalcResult:
    """Пени на недоимку amount за просрочку с due_date (срок уплаты) по paid_date включительно."""
    if taxpayer not in ("organization", "individual"):
        raise ValueError("taxpayer: organization | individual")
    if amount <= 0:
        raise ValueError("amount должен быть положительным")
    if paid_date <= due_date:
        return CalcResult("penalty", 0.0, ["просрочки нет: дата уплаты не позже срока"], [UNITS["penalty_days"]])
    rates = key_rates or KeyRates.load()
    res = CalcResult("penalty", 0.0)
    res.applied.append(UNITS["penalty_days"])
    res.steps.append(f"период начисления: с {(due_date + timedelta(days=1)).isoformat()} (день возникновения "
                     f"недоимки) по {paid_date.isoformat()} включительно (п. 3 ст. 75)")
    total = 0.0
    segments: list[dict] = []
    d, day_no = due_date + timedelta(days=1), 1
    rules_used: set[str] = set()
    while d <= paid_date:
        rate = rates.rate_on(d)
        if rate is None:
            res.warnings.append(f"нет ключевой ставки на {d.isoformat()} — обновите key_rate.json")
            break
        if taxpayer == "individual":
            denom, unit = 300, UNITS["penalty_rate"]
        else:
            denom, unit = _fraction_for_org(d, day_no)
        rules_used.add(unit)
        daily = amount * rate / 100 / denom
        total += daily
        if segments and segments[-1]["rate"] == rate and segments[-1]["denom"] == denom:
            segments[-1]["to"], segments[-1]["days"] = d.isoformat(), segments[-1]["days"] + 1
        else:
            segments.append({"from": d.isoformat(), "to": d.isoformat(), "days": 1, "rate": rate, "denom": denom})
        d, day_no = d + timedelta(days=1), day_no + 1
    for seg in segments:
        res.steps.append(f"{seg['from']}–{seg['to']}: {seg['days']} дн. × {amount:,.2f} × {seg['rate']}% / {seg['denom']}"
                         f" = {amount * seg['rate'] / 100 / seg['denom'] * seg['days']:,.2f}".replace(",", " "))
    res.applied.extend(sorted(rules_used))
    res.value = round(total, 2)
    res.steps.append(f"итого пени: {res.value:,.2f} руб.".replace(",", " "))
    if rates.last_day and paid_date.isoformat() > rates.last_day:
        res.warnings.append(f"ключевая ставка известна по {rates.last_day}; далее взята последняя")
    res.warnings.append("не учтены: положительное сальдо ЕНС, арест имущества и приостановление операций "
                        "(абз. 2–3 п. 3 ст. 75), периоды по п. 7 ст. 75")
    return res


# --- штрафы ---------------------------------------------------------------------------------

FINES = {
    "119": ("непредставление декларации: 5 % неуплаченной суммы за каждый полный/неполный месяц, "
            "не более 30 % и не менее 1000 руб.", UNITS["fine_119"]),
    "122": ("неуплата налога: 20 % от неуплаченной суммы", UNITS["fine_122"]),
    "122_willful": ("умышленная неуплата: 40 % от неуплаченной суммы", UNITS["fine_122_willful"]),
    "126": ("непредставление документов: 200 руб. за каждый документ", UNITS["fine_126"]),
}


def _months_late(due: date, actual: date) -> int:
    """Полные и неполные месяцы просрочки (каждый начатый месяц считается)."""
    if actual <= due:
        return 0
    months = (actual.year - due.year) * 12 + (actual.month - due.month)
    if actual.day > due.day:
        months += 1
    return max(months, 1)


def compute_fine(article: str, base: float = 0.0, due_date: date | None = None,
                 actual_date: date | None = None, documents: int = 0,
                 mitigating: list[str] | None = None, aggravating: bool = False,
                 reduction_factor: float = 2.0) -> CalcResult:
    if article not in FINES:
        raise ValueError(f"article: {', '.join(FINES)}")
    desc, unit = FINES[article]
    res = CalcResult("fine", 0.0, applied=[unit])
    if article == "119":
        if due_date is None or actual_date is None:
            raise ValueError("для ст. 119 нужны due_date (срок подачи) и actual_date (дата подачи)")
        months = _months_late(due_date, actual_date)
        raw = base * 0.05 * months
        capped = min(raw, base * 0.30)
        fine = max(capped, 1000.0)
        res.steps += [f"месяцев просрочки (полных и неполных): {months}",
                      f"5 % × {base:,.2f} × {months} = {raw:,.2f}; не более 30 % ({base * 0.30:,.2f}) и не менее 1000 руб. "
                      f"→ {fine:,.2f} (п. 1 ст. 119)".replace(",", " ")]
    elif article == "122":
        fine = base * 0.20
        res.steps.append(f"20 % × {base:,.2f} = {fine:,.2f} (п. 1 ст. 122)".replace(",", " "))
    elif article == "122_willful":
        fine = base * 0.40
        res.steps.append(f"40 % × {base:,.2f} = {fine:,.2f} (п. 3 ст. 122)".replace(",", " "))
    else:
        fine = 200.0 * documents
        res.steps.append(f"200 руб. × {documents} документов = {fine:,.2f} (п. 1 ст. 126)".replace(",", " "))
    if aggravating:
        fine *= 2
        res.applied += [UNITS["aggravating"], UNITS["increase"]]
        res.steps.append(f"повторное аналогичное правонарушение: +100 % → {fine:,.2f} (п. 2 ст. 112, п. 4 ст. 114)".replace(",", " "))
    if mitigating:
        if not 2.0 <= reduction_factor <= 10.0:
            raise ValueError("reduction_factor: от 2 до 10 (п. 3 ст. 114)")
        fine /= reduction_factor
        res.applied += [UNITS["mitigating"], UNITS["reduce"]]
        res.steps.append(f"смягчающие обстоятельства ({'; '.join(mitigating)}): уменьшение в {reduction_factor:g} раза "
                         f"(не менее чем в два и не более чем в десять) → {fine:,.2f} (п. 1 ст. 112, п. 3 ст. 114)".replace(",", " "))
    res.value = round(fine, 2)
    res.steps.insert(0, desc)
    return res


# --- сроки обжалования -----------------------------------------------------------------------

def appeal_deadlines(act_received: date | None = None, decision_received: date | None = None,
                     decision_date: date | None = None, complaint_decision_date: date | None = None,
                     cal: ProductionCalendar | None = None) -> CalcResult:
    """Сроки процедуры по проверке: возражения на акт, вступление решения в силу и апелляция,
    жалоба на вступившее решение, жалоба в ФНС."""
    cal = cal or ProductionCalendar.load()
    res = CalcResult("appeal_deadlines", {})
    out: dict = {}
    if act_received:
        d = compute_deadline(act_received, 1, "months", cal)
        out["objections_until"] = d.end.isoformat()
        res.steps.append(f"возражения на акт: месяц со дня получения акта {act_received.isoformat()} → "
                         f"{d.end.isoformat()} (п. 6 ст. 100; расчёт по ст. 6.1)")
        res.applied += [UNITS["objections"], *d.applied]
    if decision_received:
        d = compute_deadline(decision_received, 1, "months", cal)
        out["decision_in_force"] = d.end.isoformat()
        out["appeal_until"] = (d.nominal_end - timedelta(days=1)).isoformat()
        res.steps.append(f"решение вступает в силу по истечении месяца со дня вручения {decision_received.isoformat()} → "
                         f"{d.end.isoformat()} (п. 9 ст. 101)")
        res.steps.append(f"апелляционная жалоба — до дня вступления решения в силу: не позднее "
                         f"{out['appeal_until']} (п. 2 ст. 139.1)")
        res.applied += [UNITS["decision_force"], UNITS["appeal"], *d.applied]
    if decision_date:
        d = compute_deadline(decision_date, 1, "years", cal)
        out["complaint_until"] = d.end.isoformat()
        res.steps.append(f"жалоба на вступившее в силу решение (без апелляции): год со дня вынесения "
                         f"{decision_date.isoformat()} → {d.end.isoformat()} (абз. 2 п. 2 ст. 139)")
        res.applied += [UNITS["complaint"], *d.applied]
    if complaint_decision_date:
        d = compute_deadline(complaint_decision_date, 3, "months", cal)
        out["fns_complaint_until"] = d.end.isoformat()
        res.steps.append(f"жалоба в ФНС России: три месяца со дня принятия решения по жалобе "
                         f"{complaint_decision_date.isoformat()} → {d.end.isoformat()} (абз. 3 п. 2 ст. 139)")
        res.applied += [UNITS["complaint"], *d.applied]
    if not out:
        raise ValueError("укажите хотя бы одну дату: act_received / decision_received / decision_date / complaint_decision_date")
    res.value = out
    res.applied = sorted(set(res.applied))
    res.warnings.append("переносы выходных Правительства учитываются только при наличии data/calendar/<год>.json")
    return res


# --- срок давности ----------------------------------------------------------------------------

PERIOD_BASED = ("120", "122", "129.3", "129.5")


def limitation_status(offense_date: date | None, decision_date: date, article: str | None = None,
                      period_end: date | None = None, cal: ProductionCalendar | None = None) -> CalcResult:
    """Истёк ли срок давности привлечения к ответственности (3 года, п. 1 ст. 113) к дате решения."""
    cal = cal or ProductionCalendar.load()
    res = CalcResult("limitation", {}, applied=[UNITS["limitation"]])
    if article in PERIOD_BASED:
        if period_end is None:
            raise ValueError(f"для ст. {article} нужен period_end — последний день налогового периода")
        start = period_end  # срок течёт со следующего дня после окончания периода
        res.steps.append(f"ст. {article}: срок исчисляется со следующего дня после окончания налогового периода "
                         f"({period_end.isoformat()}) (абз. 3 п. 1 ст. 113)")
    else:
        if offense_date is None:
            raise ValueError("нужна offense_date — день совершения правонарушения")
        start = offense_date
        res.steps.append(f"срок исчисляется со дня совершения правонарушения {offense_date.isoformat()} (абз. 2 п. 1 ст. 113)")
    d = compute_deadline(start, 3, "years", cal)
    expired = decision_date > d.nominal_end
    res.value = {"limitation_ends": d.nominal_end.isoformat(), "decision_date": decision_date.isoformat(),
                 "expired": expired}
    res.steps.append(f"три года истекают {d.nominal_end.isoformat()}; решение {decision_date.isoformat()} — "
                     f"{'за пределами срока: привлечение невозможно' if expired else 'в пределах срока'} (п. 1 ст. 113)")
    res.applied += d.applied[:2]
    res.warnings.append("приостановление срока (п. 1.1 ст. 113) не учитывается")
    return res
