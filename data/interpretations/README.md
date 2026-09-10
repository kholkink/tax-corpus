# Разъяснения и практика

Файлы `*.jsonl`, по документу на строку (см. `interpretations.py`): письма Минфина/ФНС,
решения по жалобам, постановления Пленума, обзоры и определения ВС, акты КС.
Каждый документ — с `source_url`, `retrieved_at`, `sha256`. Только первоисточники
(nalog.gov.ru, minfin.gov.ru, vsrf.ru, ksrf.ru, pravo.gov.ru).

Загрузка: `python -m taxcorpus load-docs --input data/interpretations`
Проверка привязки: `python -m taxcorpus interpretations --id nk1.ch14.art88.p2`
