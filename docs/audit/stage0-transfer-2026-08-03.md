# Перенос корпуса — протокол, 2026-08-03

Что утверждается: корпус `research/prizrak_corpus/` перенесён из `C:\Users\Антон\Documents\hunter`
без единого изменения. FOUNDATION.md §6.

## Числа

| Величина | Значение |
|---|---|
| Файлов в корпусе (рекурсивно) | 45 |
| Из них верхним уровнем | 41 файл + каталог `course_notes/` = 42 записи |
| .razbor.md (разборы) | 9 |
| `.txt` (14 транскриптов + _glossary.txt) | 15 |
| `.segments.jsonl` | 14 |
| Прочее | INDEX.md, `README.md`, `manifest.jsonl`, `course_notes/` (4 файла) |
| Файлов с расхождением sha256 | 0 |

Формулировка «42 файла» в FOUNDATION.md §6 считает `course_notes/` одной записью верхнего
уровня. Рекурсивно файлов 45. Расхождение — в способе счёта, не в содержимом.

## Команда воспроизведения

```bash
cd "C:/Users/Антон/Documents/hunter/research/prizrak_corpus" && find . -type f -exec sha256sum {} \; | sort -k2 > /tmp/old.sums
cd "C:/Users/Антон/Documents/hunter-v2/research/prizrak_corpus" && find . -type f -exec sha256sum {} \; | sort -k2 > /tmp/new.sums
diff /tmp/old.sums /tmp/new.sums && echo "идентично"
```

Прогон 2026-08-03: обе описи по 45 строк, `diff` пуст. Проверено дважды — до `git init` и
после первого коммита (второй прогон нужен потому, что в окружении `core.autocrlf=true`
переписал бы переводы строк при checkout; `.gitattributes` это выключает на корпусе).

`docs/FOUNDATION.md` — копия `Downloads/ОСНОВАНИЕ_Hunter-Prizrak_v2.md`,
sha256 `409508fd78b15aa45524688abe955542636054921857a78192363fc15a4b3cd0` у обоих.

## Что осталось непроверенным

- **Содержимое корпуса не верифицировалось.** Проверено равенство копии оригиналу, а не
  правильность самих транскриптов. `README.md` корпуса сам предупреждает, что расшифровка
  и понимание сделаны агентом и могут содержать ошибки.
- **`README.md` и INDEX.md внутри корпуса ссылаются на структуру старого репозитория**
  (`hunt_core/prizrak/`, `research/manipulations_corpus/`, docs/PRIZRAK_METHODOLOGY.md,
  scripts/ingest_manipulation_video.py) — в новом репозитории этих путей нет. Не
  исправлено умышленно: перенос требовался «без изменений».
- **PDF мини-курса в старом репозитории отсутствует** (поиск `find . -iname "*.pdf"` —
  ноль совпадений). В корпусе есть только `course_notes/` — постраничный конспект PDF,
  сделанный ИИ 2026-07-17. Владелец пришлёт PDF отдельно.
