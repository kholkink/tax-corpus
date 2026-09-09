"""Смоук-тесты загруженного корпуса. Запуск: python scripts/smoke_test.py"""
import sys

import psycopg
from psycopg.rows import dict_row

DB = sys.argv[1] if len(sys.argv) > 1 else "postgresql://postgres@localhost:5432/taxcorpus"

conn = psycopg.connect(DB, row_factory=dict_row)


def q(sql: str, *params):
    return conn.execute(sql, params).fetchall()


print("== состав корпуса ==")
print("units по видам:", {r["kind"]: r["n"] for r in
      q("SELECT kind, count(*) n FROM unit GROUP BY kind ORDER BY 2 DESC")})

print("\n== п. 1 ст. 88 (камеральная проверка) ==")
r = q("SELECT u.unit_id, u.label, left(t.text, 110) AS text FROM unit u "
      "JOIN unit_text t USING (unit_id) WHERE u.unit_id = %s", "nk1.ch14.art88.p1")[0]
print(r["unit_id"], "|", r["label"])
print(" ", r["text"])

print("\n== статья 6.1 (дробный номер, пометка редакции) ==")
r = q("SELECT u.label, left(t.text, 90) AS text, t.edit_note FROM unit u "
      "JOIN unit_text t USING (unit_id) WHERE u.unit_id = %s", "nk1.ch1.art6-1")[0]
print(r["label"], "| note:", (r["edit_note"] or "")[:60])
print(" ", r["text"])

print("\n== подпункт 2 пункта 1 статьи 21 (обязанности, глава 3) ==")
r = q("SELECT u.label, left(t.text, 70) AS text FROM unit u "
      "JOIN unit_text t USING (unit_id) WHERE u.unit_id = %s", "nk1.ch3.art21.p1.sp2")
print(r[0]["label"] if r else "НЕТ", "->", r[0]["text"] if r else "")

print("\n== дубликаты источника (суффиксы @) ==")
r = q("SELECT unit_id, duplicate_of IS NOT NULL AS marked FROM unit WHERE unit_id LIKE %s LIMIT 4",
      "%@%")
for row in r:
    print(" ", row["unit_id"])

print("\n== журнал прогонов ==")
r = q("SELECT run_id, act_code, stats->>'detokenized' AS detok, "
      "stats->>'duplicate_suffixes' AS dupes FROM parse_run ORDER BY run_id DESC LIMIT 1")[0]
print(f" run {r['run_id']} {r['act_code']}: детокенизировано {r['detok']}, "
      f"дублей разрешено {r['dupes']}")

print("\n== ссылки ==")
print(" reference rows:", q("SELECT count(*) n FROM reference")[0]["n"])

conn.close()
print("\nOK")
