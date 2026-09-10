# Оценка агента на эталоне (deepseek-chat, as_of 2026-09-09)

| метрика | значение |
|---|---|
| citation_precision_unit | 0.45 |
| citation_recall_unit | 1.00 |
| citation_precision_article | 0.51 |
| citation_recall_article | 1.00 |
| hallucination_rate | 0.00 |
| temporal_correctness | 1.00 |
| abstention_quality | 0.67 |
| reworked_share | 0.08 |
| avg_tool_calls | 6.82 |

| тема | вопросов | P unit | R unit | галлюц. | temporal | отказы |
|---|---|---|---|---|---|---|
| НДС | 12 | 0.35 | 1.00 | 0.00 | 1.00 | — |
| НДФЛ | 5 | 0.53 | 1.00 | 0.00 | 1.00 | — |
| ТЦО и взаимозависимость | 3 | 0.27 | 1.00 | 0.00 | 1.00 | — |
| взносы | 2 | 0.73 | 1.00 | 0.00 | 1.00 | — |
| декларации и учёт | 2 | 0.53 | 1.00 | 0.00 | 1.00 | — |
| ловушки | 4 | — | — | 0.12 | 1.00 | 0.67 |
| необоснованная выгода | 2 | 0.31 | 1.00 | 0.00 | 1.00 | — |
| ответственность | 9 | 0.38 | 1.00 | 0.00 | 1.00 | — |
| прибыль | 4 | 0.50 | 1.00 | 0.00 | 1.00 | — |
| проверки | 11 | 0.50 | 1.00 | 0.00 | 1.00 | — |
| спецрежимы | 5 | 0.61 | 1.00 | 0.00 | 1.00 | — |
| сроки и требования | 6 | 0.49 | 1.00 | 0.00 | 1.00 | — |

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
| q38 | nk1.ch14.art100.p1 | nk1.ch1.art6-1, nk1.ch14.art100, nk1.ch14.art100.p1, nk1.ch14.art89.p15 | 0.50/1.00 | 0 | нет |
| q39 | nk1.ch14.art100.p6 | nk1.ch1.art6-1, nk1.ch14.art100.p6, nk1.ch14.art101.p4, nk1.ch14.art101.p6-2 | 0.25/1.00 | 0 | нет |
| q40 | nk1.ch14.art101.p9 | nk1.ch14.art101-2, nk1.ch14.art101-2.p1, nk1.ch14.art101-2.p2, nk1.ch14.art101.p9 | 0.25/1.00 | 0 | нет |
| q41 | nk1.ch14.art93.p3 | nk1.ch1.art6-1, nk1.ch14.art83.p4-6, nk1.ch14.art93.p1, nk1.ch14.art93.p3, nk1.ch16.art126.p1, nk1.ch5.art31.p4 | 0.17/1.00 | 0 | нет |
| q42 | nk1.ch14.art89.p9 | nk1.ch14.art89.p6, nk1.ch14.art89.p9, nk1.ch14.art89.p9.sp2, nk1.ch14.art93-1.p1 | 0.50/1.00 | 0 | нет |
| q43 | nk1.ch14.art101.p1 | nk1.ch1.art6-1, nk1.ch14.art100, nk1.ch14.art100.p1.ab3, nk1.ch14.art100.p6, nk1.ch14.art101, nk1.ch14.art101.p1, nk1.ch14.art101.p1-1, nk1.ch14.art101.p6, nk1.ch14.art83.p4-6 | 0.22/1.00 | 0 | нет |
| q44 | nk1.ch1.art6-1.p6 | nk1.ch1.art6-1, nk1.ch1.art6-1.p6 | 1.00/1.00 | 0 | нет |
| q45 | nk1.ch10.art70.p1 | nk1.ch10.art69, nk1.ch10.art70, nk1.ch10.art70.p1, nk1.ch10.art70.p2 | 0.50/1.00 | 0 | нет |
| q46 | nk1.ch11.art75.p4 | nk1.ch11.art75, nk1.ch11.art75.p3, nk1.ch11.art75.p4, nk1.ch11.art75.p5, nk1.ch11.art75.p5-1 | 0.40/1.00 | 0 | нет |
| q47 | nk1.ch8.art45.p7 | nk1.ch8.art45.p13, nk1.ch8.art45.p7, nk1.ch8.art45.p7.sp1, nk1.ch8.art45.p7.sp2, nk1.ch8.art45.p8 | 0.60/1.00 | 0 | нет |
| q48 | nk1.ch16.art120.p1 | nk1.ch15.art108.p2, nk1.ch15.art114, nk1.ch16.art119, nk1.ch16.art120, nk1.ch16.art120.p1, nk1.ch16.art120.p2, nk1.ch16.art120.p3, nk1.ch16.art122 | 0.25/1.00 | 0 | нет |
| q49 | nk1.ch16.art126.p1 | nk1.ch14.art93-1, nk1.ch15.art113, nk1.ch15.art114.p4, nk1.ch16.art119, nk1.ch16.art119.p1, nk1.ch16.art126, nk1.ch16.art126.p1, nk1.ch16.art126.p1-2, nk1.ch16.art126.p2, nk1.ch16.art129-1, nk1.ch3-4.art25-14-1.p1 | 0.18/1.00 | 0 | нет |
| q50 | nk1.ch15.art112.p1 | nk1.ch14.art101-4.p7.sp4, nk1.ch14.art101.p5.sp4, nk1.ch15.art111, nk1.ch15.art112.p1, nk1.ch15.art112.p1.sp3, nk1.ch15.art114.p3 | 0.33/1.00 | 0 | нет |
| q51 | nk1.ch16.art123.p1 | nk1.ch11.art75, nk1.ch11.art75.p1, nk1.ch15.art113, nk1.ch15.art114.p3, nk1.ch15.art114.p4, nk1.ch16.art123, nk1.ch16.art123.p1, nk1.ch16.art123.p2, nk1.ch16.art126.p1-2 | 0.22/1.00 | 0 | нет |
| q52 | nk1.ch15.art111.p1 | nk1.ch15.art111, nk1.ch15.art111.p1, nk1.ch15.art111.p1.sp1, nk1.ch15.art111.p1.sp2, nk1.ch15.art111.p1.sp3, nk1.ch15.art111.p1.sp4, nk1.ch15.art111.p2 | 0.86/1.00 | 0 | нет |
| q53 | nk1.ch13.art81.p4 | nk1.ch13.art81, nk1.ch13.art81.p3, nk1.ch13.art81.p4, nk1.ch13.art81.p4.sp1, nk1.ch13.art81.p4.sp2, nk1.ch13.art81.p5 | 0.67/1.00 | 0 | нет |
| q54 | nk1.ch14-4.art105-14.p1 | nk1.ch14-1.art105-1, nk1.ch14-4.art105-14, nk1.ch14-4.art105-14.p1, nk1.ch14-4.art105-14.p1.sp1, nk1.ch14-4.art105-14.p1.sp2, nk1.ch14-4.art105-14.p1.sp3, nk1.ch14-4.art105-14.p10, nk1.ch14-4.art105-14.p2, nk1.ch14-4.art105-14.p3, nk1.ch14-4.art105-14.p4, nk1.ch14-4.art105-14.p5, nk1.ch14-4.art105-14.p9, nk2.ch21.art145-1, nk2.ch25.art275-2, nk2.ch25.art286-1 | 0.33/1.00 | 0 | нет |
| q55 | nk1.ch14-4.art105-14.p3 | nk1.ch14-4.art105-14.p1, nk1.ch14-4.art105-14.p2, nk1.ch14-4.art105-14.p3, nk1.ch14-4.art105-14.p4, nk1.ch14-4.art105-14.p5, nk1.ch14-4.art105-14.p9 | 0.17/1.00 | 0 | нет |
| q56 | nk1.ch8.art54-1.p3 | nk1.ch8.art54-1, nk1.ch8.art54-1.p1, nk2.ch21.art169, nk2.ch21.art169.p2, nk2.ch21.art169.p2.ab2, nk2.ch21.art169.p2.ab3, nk2.ch21.art169.p6, nk2.ch21.art172.p1 | 0.12/1.00 | 0 | нет |
| q57 | nk2.ch21.art170.p3 | nk2.ch21.art146.p2, nk2.ch21.art162-2, nk2.ch21.art170.p2, nk2.ch21.art170.p3, nk2.ch21.art170.p3-1, nk2.ch21.art170.p3.sp1, nk2.ch21.art170.p3.sp2, nk2.ch21.art170.p3.sp3, nk2.ch21.art170.p3.sp4, nk2.ch21.art170.p3.sp6, nk2.ch21.art170.p3.sp7, nk2.ch21.art171-1, nk2.ch21.art171-1.p3, nk2.ch21.art171-1.p4, nk2.ch21.art171-1.p5, nk2.ch25.art264 | 0.44/1.00 | 0 | нет |
| q58 | nk2.ch21.art146.p2 | nk1.ch7.art39.p3, nk1.ch7.art39.p3.sp1, nk1.ch7.art39.p3.sp2, nk1.ch7.art39.p3.sp3, nk1.ch7.art39.p3.sp4, nk1.ch7.art39.p3.sp4-1, nk1.ch7.art39.p3.sp7, nk1.ch7.art39.p3.sp8, nk2.ch21.art146, nk2.ch21.art146.p1, nk2.ch21.art146.p2, nk2.ch21.art146.p2.sp1, nk2.ch21.art146.p2.sp15, nk2.ch21.art146.p2.sp2, nk2.ch21.art146.p2.sp3, nk2.ch21.art146.p2.sp4, nk2.ch21.art146.p2.sp4-1, nk2.ch21.art146.p2.sp5, nk2.ch21.art146.p2.sp6, nk2.ch21.art146.p2.sp7, nk2.ch21.art149, nk2.ch21.art149.p6 | 0.50/1.00 | 0 | нет |
| q59 | nk2.ch21.art145.p1 | nk2.ch21.art145, nk2.ch21.art145.p1, nk2.ch21.art145.p1.ab1, nk2.ch21.art145.p1.ab2, nk2.ch21.art145.p1.ab3, nk2.ch21.art145.p1.ab6, nk2.ch21.art145.p1.ab7, nk2.ch21.art145.p1.ab9, nk2.ch21.art145.p5, nk2.ch21.art145.p5.ab1, nk2.ch21.art145.p5.ab3, nk2.ch21.art145.p5.ab4, nk2.ch25.art250.p11, nk2.ch25.art271.p4-1 | 0.57/1.00 | 0 | нет |
| q60 | nk2.ch21.art174.p5 | nk1.ch1.art6-1, nk2.ch21.art145, nk2.ch21.art161.p8, nk2.ch21.art163, nk2.ch21.art173.p5, nk2.ch21.art174.p5, nk2.ch21.art174.p5-4, nk2.ch21.art174.p5.ab1, nk2.ch21.art174.p5.ab2, nk2.ch21.art174.p5.ab4 | 0.40/1.00 | 0 | нет |
| q61 | nk2.ch23.art217-1.p4 | nk2.ch23.art217-1, nk2.ch23.art217-1.p2, nk2.ch23.art217-1.p3, nk2.ch23.art217-1.p3.sp1-1, nk2.ch23.art217-1.p3.sp4, nk2.ch23.art217-1.p4, nk2.ch23.art217-1.p6, nk2.ch23.art220.p2 | 0.25/1.00 | 0 | нет |
| q62 | nk2.ch23.art219.p1.sp2 | nk2.ch23.art219.p1, nk2.ch23.art219.p1.sp2, nk2.ch23.art219.p2, nk2.ch23.art224.p1 | 0.50/1.00 | 0 | нет |
| q63 | nk2.ch23.art220.p3.sp1 | nk2.ch23.art220, nk2.ch23.art220.p1.sp3, nk2.ch23.art220.p1.sp4, nk2.ch23.art220.p3.sp1, nk2.ch23.art220.p4, nk2.ch23.art220.p8-1, nk2.ch23.art221-1, nk2.ch23.art224.p1 | 0.25/1.00 | 0 | нет |
| q64 | nk2.ch34.art422.p1 | nk2.ch23.art217.p1, nk2.ch23.art217.p70, nk2.ch34.art419.p1.sp1, nk2.ch34.art420.p4, nk2.ch34.art422, nk2.ch34.art422.p1, nk2.ch34.art422.p1.sp1, nk2.ch34.art422.p1.sp2, nk2.ch34.art422.p1.sp3, nk2.ch34.art422.p2, nk2.ch34.art422.p3 | 0.45/1.00 | 0 | нет |
| q65 | nk2.ch25.art252.p1 | nk2.ch25.art252.p1, nk2.ch25.art252.p1.ab1, nk2.ch25.art252.p1.ab2, nk2.ch25.art252.p1.ab3, nk2.ch25.art252.p1.ab4, nk2.ch25.art252.p2, nk2.ch25.art265, nk2.ch25.art265.p1, nk2.ch25.art270 | 0.56/1.00 | 0 | нет |
| q66 | nk2.ch25.art283.p2-1 | nk2.ch23.art214-1, nk2.ch25-4.art333-45, nk2.ch25.art283.p1, nk2.ch25.art283.p2, nk2.ch25.art283.p2-1, nk2.ch25.art283.p3, nk2.ch25.art284.p1 | 0.14/1.00 | 0 | нет |
| q67 | nk2.ch25.art265.p1 | nk2.ch25.art249, nk2.ch25.art252, nk2.ch25.art265, nk2.ch25.art265.p1, nk2.ch25.art265.p1.sp20, nk2.ch25.art265.p1.sp5, nk2.ch25.art265.p2, nk2.ch25.art266, nk2.ch25.art267-3, nk2.ch25.art267-4, nk2.ch25.art269, nk2.ch25.art279, nk2.ch25.art318, nk2.ch25.art318.p2 | 0.29/1.00 | 0 | нет |
| q68 | nk2.ch26-2.art346-13.p4 | nk2.ch26-2.art346-12.p2, nk2.ch26-2.art346-12.p2.ab2, nk2.ch26-2.art346-12.p2.ab3, nk2.ch26-2.art346-12.p3, nk2.ch26-2.art346-13, nk2.ch26-2.art346-13.p4, nk2.ch26-2.art346-13.p4-1, nk2.ch26-2.art346-14.p3 | 0.25/1.00 | 0 | нет |
| q69 | nk2.ch26-2.art346-20.p2 | nk2.ch26-2.art346-20, nk2.ch26-2.art346-20.p2 | 1.00/1.00 | 0 | нет |
| q70 | nk1.ch15.art114.p3 | nk1.ch15.art112, nk1.ch15.art114.p3 | 0.50/1.00 | 0 | нет |
| q71 | nk2.ch21.art172.p1-1 | nk2.ch21.art171.p2, nk2.ch21.art172.p1-1, nk2.ch21.art172.p1-1.ab2, nk2.ch21.art174 | 0.50/1.00 | 0 | нет |
| q72 | nk2.ch26-5.art346-45.p6.sp1 | nk2.ch25.art249, nk2.ch26-5.art346-43, nk2.ch26-5.art346-45.p6, nk2.ch26-5.art346-45.p6.sp1, nk2.ch26-5.art346-45.p6.sp2 | 0.40/1.00 | 0 | нет |
| q73 | nk1.ch1.art6-1, nk1.ch14.art100.p6 | nk1.ch1.art6-1, nk1.ch1.art6-1.p2, nk1.ch1.art6-1.p5, nk1.ch1.art6-1.p8, nk1.ch14.art100.p6 | 1.00/1.00 | 0 | нет |
| q74 |  | nk1.ch1.art11-3.p5, nk1.ch13.art81.p6, nk1.ch16.art123, nk1.ch16.art123.p1, nk1.ch16.art123.p2, nk2.ch25.art251 | —/— | 0 | да |
| q75 | nk1.ch11.art75.p4 | nk1.ch11.art75.p3, nk1.ch11.art75.p4.sp2, nk1.ch11.art75.p5, nk1.ch11.art75.p5-1, nk1.ch11.art75.p7 | 0.20/1.00 | 0 | нет |
