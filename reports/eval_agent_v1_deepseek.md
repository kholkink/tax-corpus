# Оценка агента на эталоне (deepseek-chat, as_of 2026-09-09)

| метрика | значение |
|---|---|
| citation_precision_unit | 0.47 |
| citation_recall_unit | 1.00 |
| citation_precision_article | 0.48 |
| citation_recall_article | 1.00 |
| hallucination_rate | 0.01 |
| temporal_correctness | 1.00 |
| abstention_quality | 1.00 |
| reworked_share | 0.28 |
| avg_tool_calls | 7.80 |

| вопрос | ожидалось | процитировано | P/R (unit) | галлюц. | отказ |
|---|---|---|---|---|---|
| q01 | nk1.ch14.art88.p2 | nk1.ch1.art6-1, nk1.ch14.art83.p4-6, nk1.ch14.art88.p2, nk2.ch23.art221-1.p2 | 0.25/1.00 | 0 | нет |
| q03 | nk1.ch14.art89.p6 | nk1.ch14.art89.p6 | 1.00/1.00 | 0 | нет |
| q04 | nk1.ch10.art69.p3 | nk1.ch1.art6-1.p6, nk1.ch10.art69, nk1.ch10.art69.p2, nk1.ch10.art69.p3, nk1.ch10.art69.p3.ab2, nk1.ch10.art69.p4, nk1.ch10.art69.p5 | 0.43/1.00 | 0 | нет |
| q05 | nk1.ch16.art119.p1 | nk1.ch15.art112, nk1.ch15.art113, nk1.ch16.art119, nk1.ch16.art119-1, nk1.ch16.art119.p1, nk1.ch16.art119.p2, nk1.ch16.art126 | 0.29/1.00 | 0 | нет |
| q07 | nk1.ch15.art113.p1 | nk1.ch1.art6-1, nk1.ch14.art91.p3, nk1.ch15.art109.p1.sp4, nk1.ch15.art113, nk1.ch15.art113.p1, nk1.ch15.art113.p1-1, nk1.ch16.art120 | 0.29/1.00 | 0 | нет |
| q08 | nk1.ch14-1.art105-1.p2 | nk1.ch14-1.art105-1, nk1.ch14-1.art105-1.p1, nk1.ch14-1.art105-1.p2, nk1.ch14-1.art105-1.p3, nk1.ch14-1.art105-1.p4, nk1.ch14-1.art105-1.p6, nk1.ch14-1.art105-1.p7, nk1.ch14-4.art105-14, nk1.ch14-4.art105-14.p1.sp1, nk1.ch3-4.art25-13, nk2.ch25.art269.p2 | 0.18/1.00 | 0 | нет |
| q09 | nk1.ch8.art54-1.p1 | nk1.ch8.art54-1, nk1.ch8.art54-1.p1, nk1.ch8.art54-1.p2, nk1.ch8.art54-1.p3, nk1.ch8.art54-1.p4 | 0.40/1.00 | 0 | нет |
| q10 | nk1.ch13.art80.p3 | nk1.ch13.art80, nk1.ch13.art80.p3, nk1.ch13.art80.p3.ab1, nk1.ch13.art80.p3.ab4, nk1.ch13.art80.p3.ab5, nk1.ch13.art80.p3.ab6, nk1.ch13.art80.p3.ab7, nk1.ch14.art83, nk1.ch16.art119, nk1.ch16.art119-1, nk1.ch3.art23.p5-1, nk2.ch21.art174.p5 | 0.58/1.00 | 0 | нет |
| q11 | nk2.ch21.art164.p3 | nk2.ch21.art164, nk2.ch21.art164.p2, nk2.ch21.art164.p3, nk2.ch21.art164.p4 | 0.50/1.00 | 0 | нет |
| q12 | nk2.ch21.art164.p1 | nk2.ch21.art164, nk2.ch21.art164.p1, nk2.ch21.art164.p1.sp1, nk2.ch21.art164.p1.sp1-1, nk2.ch21.art164.p1.sp1-2, nk2.ch21.art164.p1.sp2-11, nk2.ch21.art164.p2, nk2.ch21.art164.p3, nk2.ch21.art165, nk2.ch21.art165.p9, nk2.ch21.art171 | 0.55/1.00 | 0 | нет |
| q13 | nk2.ch21.art164.p4 | nk2.ch21.art164, nk2.ch21.art164.p2, nk2.ch21.art164.p3, nk2.ch21.art164.p4, nk2.ch21.art164.p8, nk2.ch21.art167.p1, nk2.ch21.art168.p3, nk2.ch21.art171.p5, nk2.ch21.art171.p8, nk2.ch21.art172.p6 | 0.20/1.00 | 0 | нет |
| q14 | nk2.ch21.art171.p2 | nk2.ch21.art149.p2.sp26, nk2.ch21.art155, nk2.ch21.art161, nk2.ch21.art164.p1, nk2.ch21.art164.p8, nk2.ch21.art165, nk2.ch21.art166, nk2.ch21.art170.p2, nk2.ch21.art171, nk2.ch21.art171-1, nk2.ch21.art171.p1, nk2.ch21.art171.p10, nk2.ch21.art171.p11, nk2.ch21.art171.p12, nk2.ch21.art171.p13, nk2.ch21.art171.p14, nk2.ch21.art171.p2, nk2.ch21.art171.p2.sp1, nk2.ch21.art171.p3, nk2.ch21.art171.p4, nk2.ch21.art171.p4-1, nk2.ch21.art171.p5, nk2.ch21.art171.p6, nk2.ch21.art171.p7, nk2.ch21.art171.p8, nk2.ch21.art172, nk2.ch21.art172.p1, nk2.ch25.art250 | 0.11/1.00 | 0 | нет |
| q15 | nk2.ch21.art168.p3 | nk1.ch1.art6-1, nk2.ch21.art161, nk2.ch21.art166.p3-2, nk2.ch21.art168.p3, nk2.ch21.art169, nk2.ch21.art172.p10, nk2.ch21.art174-2.p10 | 0.14/1.00 | 0 | нет |
| q16 | nk2.ch21.art167.p1 | nk2.ch21.art155, nk2.ch21.art161, nk2.ch21.art164.p1, nk2.ch21.art164.p1.sp9-2, nk2.ch21.art165, nk2.ch21.art167, nk2.ch21.art167.p1, nk2.ch21.art167.p1.sp1, nk2.ch21.art167.p10, nk2.ch21.art167.p11, nk2.ch21.art167.p14, nk2.ch21.art167.p15, nk2.ch21.art167.p16, nk2.ch21.art167.p3, nk2.ch21.art167.p7, nk2.ch21.art167.p8, nk2.ch21.art167.p9, nk2.ch21.art167.p9-1, nk2.ch21.art167.p9-2, nk2.ch21.art167.p9-3, nk2.ch21.art171 | 0.14/1.00 | 0 | нет |
| q17 | nk2.ch21.art143.p1 | nk2.ch21.art143, nk2.ch21.art143.p1, nk2.ch21.art143.p3, nk2.ch21.art145, nk2.ch21.art145.p1, nk2.ch21.art146, nk2.rviii-1.art346-1.p3 | 0.29/1.00 | 0 | нет |
| q18 | nk2.ch23.art224.p1 | nk2.ch23.art210.p2-1, nk2.ch23.art224, nk2.ch23.art224.p1 | 0.67/1.00 | 0 | нет |
| q19 | nk2.ch23.art218.p1.sp4 | nk2.ch23.art218, nk2.ch23.art218.p1.sp4, nk2.ch23.art224.p1 | 0.67/1.00 | 1 | нет |
| q20 | nk2.ch34.art425.p3 | nk2.ch34.art425.p3, nk2.ch34.art425.p3.sp1, nk2.ch34.art425.p3.sp2, nk2.ch34.art427, nk2.ch34.art427.p10-1, nk2.ch34.art427.p2-2 | 0.50/1.00 | 0 | нет |
| q21 | nk2.ch25.art284.p1 | nk2.ch25.art284, nk2.ch25.art284.p1 | 1.00/1.00 | 0 | нет |
| q22 | nk2.ch26-2.art346-20.p1 | nk2.ch26-2.art346-20, nk2.ch26-2.art346-20.p1 | 1.00/1.00 | 0 | нет |
| q23 | nk2.ch26-5.art346-50.p1 | nk2.ch26-5.art346-50, nk2.ch26-5.art346-50.p1, nk2.ch26-5.art346-50.p2, nk2.ch26-5.art346-50.p3 | 0.50/1.00 | 0 | нет |
| q34 |  | nk2.ch21.art164, nk2.ch21.art164.p3 | —/— | 0 | да |
| q35 |  | nk2.ch23.art219, nk2.ch23.art219.p1.sp1 | —/— | 1 | да |
| q36 |  | nk1.ch1.art11-3, nk1.ch3.art23, nk2.ch34.art425.p3, nk2.ch34.art427, nk2.ch34.art427.p2-2, nk2.ch34.art431.p6-2 | —/— | 0 | нет |
| q37 | nk1.ch1.art6-1, nk1.ch14.art88.p2 | nk1.ch1.art6-1, nk1.ch1.art6-1.p2, nk1.ch1.art6-1.p5, nk1.ch1.art6-1.p7, nk1.ch1.art6-1.p8, nk1.ch13.art80.p2, nk1.ch14.art83.p4-6, nk1.ch14.art88.p2, nk1.ch15.art112, nk2.ch23.art221.p2 | 0.60/1.00 | 0 | нет |
