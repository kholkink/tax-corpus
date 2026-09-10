# Оценка агента на эталоне (deepseek-chat, as_of 2026-09-09)

| метрика | значение |
|---|---|
| citation_precision_unit | 0.50 |
| citation_recall_unit | 1.00 |
| citation_precision_article | 0.56 |
| citation_recall_article | 1.00 |
| hallucination_rate | 0.01 |
| temporal_correctness | 1.00 |
| abstention_quality | 0.50 |
| reworked_share | 0.07 |
| avg_tool_calls | 6.41 |

| вопрос | ожидалось | процитировано | P/R (unit) | галлюц. | отказ |
|---|---|---|---|---|---|
| q01 | nk1.ch14.art88.p2 | nk1.ch14.art83.p4-6, nk1.ch14.art88, nk1.ch14.art88.p2, nk1.ch14.art88.p9-1, nk1.ch14.art89.p9, nk1.ch14.art93, nk2.ch23.art221-1.p2 | 0.29/1.00 | 0 | нет |
| q02 | nk1.ch14.art89.p6 | nk1.ch14.art89.p6, nk1.ch14.art89.p6.ab2, nk1.ch14.art89.p9, nk1.ch14.art93-1.p1 | 0.50/1.00 | 0 | нет |
| q03 | nk1.ch14.art89.p6 | nk1.ch14.art89.p6 | 1.00/1.00 | 0 | нет |
| q04 | nk1.ch10.art69.p3 | nk1.ch1.art6-1.p2, nk1.ch1.art6-1.p6, nk1.ch1.art6-1.p8, nk1.ch10.art69.p3 | 0.25/1.00 | 0 | нет |
| q05 | nk1.ch16.art119.p1 | nk1.ch16.art119, nk1.ch16.art119.p1, nk1.ch16.art119.p2, nk1.ch16.art126 | 0.50/1.00 | 0 | нет |
| q06 | nk1.ch16.art122.p3 | nk1.ch15.art110.p2, nk1.ch16.art122, nk1.ch16.art122.p1, nk1.ch16.art122.p3, nk1.ch16.art129-3, nk1.ch16.art129-5 | 0.33/1.00 | 0 | нет |
| q07 | nk1.ch15.art113.p1 | nk1.ch14.art91.p3, nk1.ch15.art109.p1.sp4, nk1.ch15.art113, nk1.ch15.art113.p1, nk1.ch15.art113.p1-1, nk1.ch16.art120, nk1.ch16.art123 | 0.29/1.00 | 0 | нет |
| q08 | nk1.ch14-1.art105-1.p2 | nk1.ch14-1.art105-1, nk1.ch14-1.art105-1.p1, nk1.ch14-1.art105-1.p2, nk1.ch14-1.art105-1.p2.sp1, nk1.ch14-1.art105-1.p3, nk1.ch14-1.art105-1.p4, nk1.ch14-1.art105-1.p5, nk1.ch14-1.art105-1.p6, nk1.ch14-1.art105-1.p7, nk1.ch14-1.art105-2 | 0.30/1.00 | 0 | нет |
| q09 | nk1.ch8.art54-1.p1 | nk1.ch8.art54-1, nk1.ch8.art54-1.p1, nk1.ch8.art54-1.p2, nk1.ch8.art54-1.p3 | 0.50/1.00 | 0 | нет |
| q10 | nk1.ch13.art80.p3 | nk1.ch13.art80.p3, nk1.ch13.art80.p3.ab5, nk1.ch14.art83, nk1.ch16.art119-1, nk2.ch21.art174.p5 | 0.40/1.00 | 0 | нет |
| q11 | nk2.ch21.art164.p3 | nk2.ch21.art164, nk2.ch21.art164.p1, nk2.ch21.art164.p2, nk2.ch21.art164.p3, nk2.ch21.art164.p4 | 0.40/1.00 | 0 | нет |
| q12 | nk2.ch21.art164.p1 | nk2.ch21.art164.p1, nk2.ch21.art164.p1.sp1, nk2.ch21.art164.p3, nk2.ch21.art164.p4, nk2.ch21.art165 | 0.40/1.00 | 0 | нет |
| q13 | nk2.ch21.art164.p4 | nk2.ch21.art154, nk2.ch21.art154.p1, nk2.ch21.art154.p1.ab2, nk2.ch21.art154.p1.ab5, nk2.ch21.art164.p1, nk2.ch21.art164.p2, nk2.ch21.art164.p3, nk2.ch21.art164.p4, nk2.ch21.art167.p1.sp2, nk2.ch21.art167.p13, nk2.ch21.art172 | 0.09/1.00 | 0 | нет |
| q14 | nk2.ch21.art171.p2 | nk2.ch21.art161, nk2.ch21.art166, nk2.ch21.art170.p2, nk2.ch21.art170.p3, nk2.ch21.art170.p4, nk2.ch21.art171, nk2.ch21.art171.p1, nk2.ch21.art171.p11, nk2.ch21.art171.p2, nk2.ch21.art171.p2.sp1, nk2.ch21.art171.p3, nk2.ch21.art171.p5, nk2.ch21.art171.p7, nk2.ch21.art172, nk2.ch21.art172.p1 | 0.20/1.00 | 0 | нет |
| q15 | nk2.ch21.art168.p3 | nk1.ch1.art6-1, nk2.ch21.art166.p3-2, nk2.ch21.art168.p3, nk2.ch21.art172.p10, nk2.ch21.art174-2.p1 | 0.20/1.00 | 0 | нет |
| q16 | nk2.ch21.art167.p1 | nk2.ch21.art161, nk2.ch21.art164.p1, nk2.ch21.art165, nk2.ch21.art167, nk2.ch21.art167.p1, nk2.ch21.art167.p1.sp1, nk2.ch21.art167.p10, nk2.ch21.art167.p11, nk2.ch21.art167.p14, nk2.ch21.art167.p15, nk2.ch21.art167.p16, nk2.ch21.art167.p3, nk2.ch21.art167.p9, nk2.ch21.art167.p9-1 | 0.21/1.00 | 0 | нет |
| q17 | nk2.ch21.art143.p1 | nk2.ch21.art143.p1, nk2.ch21.art143.p2, nk2.ch21.art143.p3, nk2.ch26-5.art346-43.p11 | 0.25/1.00 | 0 | нет |
| q18 | nk2.ch23.art224.p1 | nk2.ch23.art210, nk2.ch23.art224, nk2.ch23.art224.p1 | 0.67/1.00 | 0 | нет |
| q19 | nk2.ch23.art218.p1.sp4 | nk2.ch23.art218.p1.sp4 | 1.00/1.00 | 0 | нет |
| q20 | nk2.ch34.art425.p3 | nk2.ch34.art425.p3, nk2.ch34.art425.p3.sp1, nk2.ch34.art425.p3.sp2 | 1.00/1.00 | 0 | нет |
| q21 | nk2.ch25.art284.p1 | nk2.ch25.art284.p1 | 1.00/1.00 | 0 | нет |
| q22 | nk2.ch26-2.art346-20.p1 | nk2.ch26-2.art346-20.p1 | 1.00/1.00 | 0 | нет |
| q23 | nk2.ch26-5.art346-50.p1 | nk2.ch26-5.art346-50, nk2.ch26-5.art346-50.p1, nk2.ch26-5.art346-50.p2, nk2.ch26-5.art346-50.p3, nk2.ch26-5.art346-51.p1 | 0.40/1.00 | 0 | нет |
| q34 |  | nk2.ch21.art164.p3 | —/— | 0 | нет |
| q35 |  | — | —/— | 2 | да |
| q36 |  | nk2.ch34.art425, nk2.ch34.art425.p3, nk2.ch34.art427, nk2.ch34.art427.p1.sp23, nk2.ch34.art427.p2-2, nk2.ch34.art427.p2-6, nk2.ch34.art431 | —/— | 0 | нет |
| q37 | nk1.ch1.art6-1, nk1.ch14.art88.p2 | nk1.ch1.art6-1.p2, nk1.ch1.art6-1.p5, nk1.ch1.art6-1.p7, nk1.ch14.art88.p2, nk2.ch23.art221.p2 | 0.80/1.00 | 0 | нет |
