# Hunter-Prizrak v2

[![CI](https://github.com/TrololoBird/hunter-prizrak-v2/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TrololoBird/hunter-prizrak-v2/actions/workflows/ci.yml)

Сигнально-аналитическая система по методологии PrizrakTrade. Находит уровни, сообщает
оператору в Telegram. Ордера не исполняет, ключей бирж не хранит.

⚠ Репозиторий ПРИВАТНЫЙ. Не потому, что код секретный, а потому, что в дереве лежит
мини-курс PrizrakTrade целиком (`docs/course/`) и расшифровки разборов автора
(`research/prizrak_corpus/`). Это чужой материал: он нужен гейтам `course_citations` и
`course_rules`, которые сверяют цитаты дословно, — и он не наш, чтобы его публиковать.

**Единственный источник требований — [`docs/FOUNDATION.md`](docs/FOUNDATION.md).**
Любой элемент системы проверяется против него. Чего нет в документе — того нет в системе.

Правило, ради которого проект переписан заново: ни один элемент не попадает в код без
внешнего референта (`docs/FOUNDATION.md` §0).
