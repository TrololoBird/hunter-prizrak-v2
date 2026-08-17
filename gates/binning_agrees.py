"""ГЕЙТ: обе ветви бинирования цены дают ОДИН И ТОТ ЖЕ бин. FOUNDATION.md §10.3.

⚠ Обоснование переписано 2026-08-17. Прежняя первая строка держала гейт на §5
(«бины профиля привязаны к tickSize») — то есть на параграфе, который FOUNDATION §2.2
сам признаёт замещённым: боевой профиль с 2026-08-09 считает `build_tv` (решение
владельца, TV_ROWS), а с 2026-08-12 — по свечам. Настоящее основание гейта не параграф,
а ДЕТЕРМИНИЗМ (§10.3): tick-бинирование по-прежнему исполняется живым потоком сделок
(`TradeHistogram`, `run._watch_trades_impl`) и архивным кэшем, и две реализации одной
формулы обязаны совпадать, иначе одинаковые данные дают разные гистограммы.

Реализаций было две и они расходились: живой поток считал бин через `Decimal`, архив —
через `float`-деление с `floor`. Реальные цены сделок ВСЕГДА кратны шагу, то есть ложатся
на границу бина, а двоичное представление `k·tick` в `float64` сплошь и рядом чуть
меньше точного значения — и `floor` уводил цену в бин `k−1`.

Замер 2026-08-04 на реальных сутках, 21 символ вселенной, 504 часовых окна: ПОК
расходился на 194 окнах (38%), максимум на 1.687% цены. На BTC, ETH и BCH расхождения не
было вовсе, поэтому проверка на флагманах ничего бы не показала — гейт обязан идти по
ВСЕМ шагам цены, встречающимся у Binance.

Это ПОВЕДЕНЧЕСКИЙ гейт, а не проверка формы: он исполняет расчёт и сравнивает числа.
Разбор класса: docs/audit/critical-review-verified-2026-08-04.md, находка Б-1.
"""

from __future__ import annotations

import sys
from decimal import Decimal

import polars as pl

from hunter.archive import bin_expr
from hunter.models import bin_index, tick_scale

# Шаги цены Binance USD-M. Собраны из PRICE_FILTER вселенной плюс те, что биржа
# использует у более дорогих и более дешёвых инструментов.
TICKS = [
    Decimal("0.000001"), Decimal("0.00001"), Decimal("0.0001"), Decimal("0.001"),
    Decimal("0.005"), Decimal("0.01"), Decimal("0.05"), Decimal("0.1"),
    Decimal("0.5"), Decimal("1"), Decimal("2.5"), Decimal("10"),
]

STEPS = 5000
"""Сколько узлов сетки проверять на каждом шаге. Число задаёт охват, а не порог."""


def grid(tick: Decimal, n: int) -> list[float]:
    """Цены РЕАЛЬНОЙ биржевой сетки: ровно `k·tick`, как их отдаёт биржа."""
    return [float(tick * k) for k in range(1, n + 1)]


def main() -> int:
    checked = 0
    bad: list[str] = []
    for tick in TICKS:
        prices = grid(tick, STEPS)
        scale, step = tick_scale(tick)

        # Ветвь 1 — скалярная, ею считает живой поток (`TradeHistogram`, `BarBinnedTrades`).
        scalar = [bin_index(p, tick) for p in prices]
        # Ветвь 2 — полярисовая, ею считает архив (`binned_day`, `_histogram`).
        frame = pl.DataFrame({"price": prices})
        vector = list(frame.select(bin_expr(pl.col("price"), tick).alias("b"))["b"])
        # Ветвь 3 — ТОЧНЫЙ ответ в десятичной арифметике. Без него гейт проверял бы
        # только то, что две реализации ошибаются одинаково.
        exact = [int(Decimal(str(p)) // tick) for p in prices]

        checked += len(prices)
        for i, (p, a, b, e) in enumerate(zip(prices, scalar, vector, exact, strict=True)):
            if a != b:
                bad.append(f"tick {tick} цена {p!r}: скаляр {a}, полярис {b}")
            if a != e:
                bad.append(f"tick {tick} цена {p!r}: скаляр {a}, точный ответ {e}")
            # На сетке биржи бин обязан совпадать с номером узла: цена k·tick — это
            # ровно k-й бин, и любое иное значение означает потерю различимого уровня.
            if a != i + 1:
                bad.append(f"tick {tick} цена {p!r}: бин {a}, а узел сетки {i + 1}")
            if len(bad) > 20:
                break
        if len(bad) > 20:
            break
        # Шаг обязан выражаться целым: 0.005 → (1000, 5). Иначе бинировать нечем.
        if Decimal(step) / scale != tick:
            bad.append(f"tick {tick}: целое представление {step}/{scale} не равно шагу")

    print(f"гейт согласованности бинирования: шагов цены {len(TICKS)}, "
          f"цен проверено {checked}, ветвей 3, расхождений {len(bad)}")
    for b in bad[:20]:
        print(f"  РАСХОЖДЕНИЕ {b}")
    if not checked:
        print("ПРОВАЛ: не проверено ни одной цены — проверка не состоялась")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
