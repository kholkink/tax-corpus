"""Верификационный проход эталона: прогоняет каждый вопрос через инструменты
корпуса и печатает доказательства, прежде чем множество будет заморожено.

Запуск: python scripts/build_golden.py
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from taxcorpus.db import get_unit, search_units
from taxcorpus.parser import parse_document
from taxcorpus.resolver import UnitIndex, article_of_unit_id, resolve_citation

DB = "postgresql://postgres@127.0.0.1:5432/taxcorpus"
TODAY = "2026-09-09"

QUESTIONS = [
    # --- поиск: ч.1, налоговый контроль (числа — цифрами, как в тексте кодекса) ---
    ("q01", "search", "В течение какого срока проводится камеральная налоговая проверка?",
     "камеральная налоговая проверка проводится в течение трех месяцев", ["nk1.ch14.art88.p2"]),
    ("q02", "search", "Какой предельный срок выездной налоговой проверки?",
     "выездная налоговая проверка не может продолжаться более двух месяцев", ["nk1.ch14.art89.p1"]),
    ("q03", "search", "На какой срок может быть продлена выездная проверка?",
     "решение о продлении выездной налоговой проверки четыре месяца", ["nk1.ch14.art89.p4"]),
    ("q04", "search", "В какой срок нужно исполнить требование об уплате налога?",
     "исполнение требования об уплате налога в течение восьми дней с даты получения", ["nk1.ch10.art69.p4"]),
    ("q05", "search", "Какой штраф за непредставление налоговой декларации?",
     "непредставление налоговой декларации влекут взыскание штрафа 5 процентов", ["nk1.ch16.art119.p1"]),
    ("q06", "search", "Какой штраф за неуплату налога при умысле?",
     "деяния совершенные умышленно влекут взыскание штрафа 40 процентов", ["nk1.ch16.art122.p3"]),
    ("q07", "search", "В какой срок можно привлечь к налоговой ответственности?",
     "срок давности привлечения к ответственности по истечении трех лет", ["nk1.ch16.art113.p1"]),
    ("q08", "search", "Кто признаётся взаимозависимыми лицами?",
     "взаимозависимыми лицами признаются доля участия организации более 25", ["nk1.ch14-1.art105-1.p2"]),
    ("q09", "search", "Что такое необоснованная налоговая выгода?",
     "необоснованная налоговая выгода искажение сведений о фактах хозяйственной жизни", ["nk1.ch8.art54-1.p1"]),
    ("q10", "search", "Когда декларацию нужно подавать в электронном виде?",
     "декларация в электронном виде по телекоммуникационным каналам связи 100 человек", ["nk1.ch13.art80.p3"]),
    # --- поиск: ч.2, НДС ---
    ("q11", "search", "Какая общая ставка НДС?",
     "налоговая ставка 20 процентов", ["nk2.ch21.art164.p3"]),
    ("q12", "search", "Когда применяется ставка НДС 0 процентов?",
     "ставка 0 процентов реализация товаров вывезенных в таможенной процедуре экспорта", ["nk2.ch21.art164.p1"]),
    ("q13", "search", "Когда применяется расчётная ставка 10/110?",
     "расчетная ставка сумма авансовых платежей", ["nk2.ch21.art164.p4"]),
    ("q14", "search", "Какие суммы НДС принимаются к вычету?",
     "вычетам подлежат суммы налога предъявленные налогоплательщику при приобретении товаров", ["nk2.ch21.art171.p2"]),
    ("q15", "search", "В какой срок выставляется счёт-фактура?",
     "счет-фактура не позднее пяти календарных дней со дня отгрузки", ["nk2.ch21.art169.p3"]),
    ("q16", "search", "Что является моментом определения налоговой базы по НДС?",
     "момент определения налоговой базы наиболее ранняя из следующих дат день отгрузки", ["nk2.ch21.art167.p1"]),
    ("q17", "search", "Кто является плательщиками НДС?",
     "налогоплательщиками налога на добавленную стоимость признаются организации", ["nk2.ch21.art143.p1"]),
    # --- поиск: ч.2, НДФЛ и взносы ---
    ("q18", "search", "Какая основная ставка НДФЛ для резидентов?",
     "налоговая ставка 13 процентов налоговая база налоговые резиденты", ["nk2.ch23.art224.p1"]),
    ("q19", "search", "Какой стандартный вычет на первого ребёнка?",
     "стандартный налоговый вычет на первого и второго ребенка", ["nk2.ch23.art218.p1.sp4"]),
    ("q20", "search", "Какой общий тариф страховых взносов?",
     "единый тариф страховых взносов 30 процентов", ["nk2.ch34.art425.p1"]),
    ("q21", "search", "Какая ставка налога на прибыль?",
     "налоговая ставка 25 процентов налог на прибыль", ["nk2.ch25.art284.p1"]),
    ("q22", "search", "Какая ставка УСН для объекта «доходы»?",
     "ставка налога 6 процентов объект налогообложения доходы", ["nk2.ch26-2.art346-20.p1"]),
    ("q23", "search", "Какая ставка при патентной системе налогообложения?",
     "ставка налога 6 процентов патентная система налогообложения", ["nk2.ch26-5.art346-50.p1"]),
    # --- resolve_citation ---
    ("q24", "resolve", "п. 2 ст. 88 НК РФ", ["nk1.ch14.art88.p2"]),
    ("q25", "resolve", "подп. 4 п. 1 ст. 218", ["nk2.ch23.art218.p1.sp4"]),
    ("q26", "resolve", "ст. 6.1 НК РФ", ["nk1.ch1.art6-1"]),
    ("q27", "resolve", "п. 1 ст. 105.3", ["nk1.ch14-2.art105-3.p1"]),
    # --- as_of: фильтр редакций (сейчас в корпусе одна редакция) ---
    ("q28", "as_of_absent", "Действовала ли п. 2 ст. 88 на 01.01.2000?",
     ("nk1.ch14.art88.p2", "2000-01-01")),
    ("q29", "as_of_present", "Действует ли ст. 164 НК на сегодня?",
     ("nk2.ch21.art164", TODAY)),
    ("q30", "as_of_absent", "Действовала ли ст. 143 на 01.01.2000?",
     ("nk2.ch21.art143", "2000-01-01")),
]


def main() -> int:
    conn = psycopg.connect(DB, row_factory=dict_row)
    _, records, _ = parse_document(
        "", "nk1") if False else (None, [], None)  # records не нужны: индекс из БД
    rows = conn.execute("SELECT unit_id, kind, number, label, title, parent_unit_id "
                        "FROM unit").fetchall()
    index = UnitIndex(rows)

    ok = 0
    for qid, kind, question, *payload in QUESTIONS:
        print(f"\n=== {qid} [{kind}] {question}")
        if kind == "search":
            query, expected = payload
            hits = search_units(conn, query, TODAY, limit=5)
            exp_articles = {article_of_unit_id(e) for e in expected}
            found = [h for h in hits if article_of_unit_id(h["unit_id"]) in exp_articles]
            status = "OK" if found else "MISS"
            ok += bool(found)
            print(f"  {status} expected={expected}")
            for i, h in enumerate(hits[:5], 1):
                mark = " <<" if article_of_unit_id(h["unit_id"]) in exp_articles else ""
                print(f"  {i}. {h['unit_id']} [{h['rank']:.2f}] {h['label']}{mark}")
                print(f"     {h['snippet'][:130]}")
        elif kind == "resolve":
            expected = payload[0] if isinstance(payload[0], list) else payload
            res = resolve_citation(question, index)
            status = "OK" if res.unit_id in expected else "MISS"
            ok += status == "OK"
            print(f"  {status} -> {res.unit_id} ({res.status}, {res.depth}); expected={expected}")
        elif kind in ("as_of_present", "as_of_absent"):
            unit_id, as_of = payload[0]
            rec = get_unit(conn, unit_id, as_of)
            present = rec is not None
            want = kind == "as_of_present"
            status = "OK" if present == want else "MISS"
            ok += status == "OK"
            print(f"  {status}: на {as_of} единица {'есть' if present else 'отсутствует'} "
                  f"(ожидалось: {'есть' if want else 'отсутствует'})")
    print(f"\n=== верификация: {ok}/{len(QUESTIONS)} ===")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
