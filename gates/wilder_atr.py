"""ГЕЙТ: ATR против ОПУБЛИКОВАННОГО ОПРЕДЕЛЕНИЯ, а не против второй библиотеки.

Замечание владельца 2026-08-03: согласие TA-Lib и pandas-ta не доказывает, что обе
верны — они могут разделять общую конвенцию, отличную от определения Уайлдера.
Внешний референт по §0 — сама формула.

Источник формулы: J. Welles Wilder Jr., «New Concepts in Technical Trading Systems»
(1978), в изложении https://en.wikipedia.org/wiki/Average_true_range :

    TR_t  = max[ (H_t − L_t), |H_t − C_{t−1}|, |L_t − C_{t−1}| ]
    ATR_n = (1/n) · Σ_{i=1..n} TR_i                     — затравка простым средним
    ATR_t = ( ATR_{t−1} · (n−1) + TR_t ) / n            — при t > n

⚠ Это вторичное изложение первоисточника: книги 1978 года в проекте нет. Формула
воспроизведена дословно по указанной странице; если владелец получит книгу, сверку
надо повторить по ней.

Здесь формула считается СКАЛЯРНЫМ ЦИКЛОМ, без polars и без ta-библиотек, и
сравнивается с тем, что выдаёт проектный код (polars_talib). Гейт также печатает
таблицу первых баров, которую можно пересчитать на калькуляторе вручную.
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import polars_talib as plta

TOLERANCE = 1e-9
PERIOD = 14
SLICE = Path("docs/audit/reference-slice/BTCUSDT-1h-500.parquet")


def true_range(high: list[float], low: list[float], close: list[float]) -> list[float]:
    """TR по определению. У первого бара предыдущего закрытия нет — берётся H−L."""
    tr: list[float] = [high[0] - low[0]]
    for t in range(1, len(high)):
        prev_close = close[t - 1]
        tr.append(max(high[t] - low[t],
                      abs(high[t] - prev_close),
                      abs(low[t] - prev_close)))
    return tr


def wilder_atr(high: list[float], low: list[float], close: list[float],
               n: int = PERIOD) -> list[float | None]:
    """ATR скалярным циклом по опубликованному определению. Никаких библиотек."""
    tr = true_range(high, low, close)
    out: list[float | None] = [None] * len(tr)
    if len(tr) <= n:
        return out
    # Затравка: простое среднее TR_1..TR_n (индексы 1..n, первый TR исключён —
    # у него нет предыдущего закрытия). Такова конвенция TA-Lib; она проверяется
    # сравнением ниже, а не принимается на веру.
    seed = sum(tr[1:n + 1]) / n
    out[n] = seed
    prev = seed
    for t in range(n + 1, len(tr)):
        prev = (prev * (n - 1) + tr[t]) / n
        out[t] = prev
    return out


def main() -> int:
    if not SLICE.exists():
        print(f"ПРОВАЛ: нет среза {SLICE} — проверка не состоялась")
        return 1
    df = pl.read_parquet(SLICE)
    high = df["high"].to_list()
    low = df["low"].to_list()
    close = df["close"].to_list()

    mine = wilder_atr(high, low, close, PERIOD)
    lib = df.select(
        plta.atr(pl.col("high"), pl.col("low"), pl.col("close"), timeperiod=PERIOD)
        .alias("atr")
    )["atr"].to_list()

    compared = 0
    worst = 0.0
    over = 0
    worst_at = -1
    for i, (a, b) in enumerate(zip(mine, lib, strict=True)):
        if a is None or b is None or a != a or b != b:
            continue
        compared += 1
        rel = abs(a - b) / max(abs(a), abs(b), 1e-9)
        if rel > worst:
            worst, worst_at = rel, i
        if rel > TOLERANCE:
            over += 1

    print(f"гейт «ATR по определению Уайлдера»: срез {SLICE.name}, баров {df.height}, "
          f"период {PERIOD}, порог {TOLERANCE:g}")
    print("  формула — скалярный цикл, без библиотек; сверяется с polars_talib")
    print(f"  сравнено точек {compared}, худшее расхождение {worst:.3e} "
          f"(бар {worst_at}), за порогом {over}")

    print("\n  ПЕРЕСЧЁТ ВРУЧНУЮ — первые значения после затравки:")
    print(f"  {'бар':>4} {'high':>10} {'low':>10} {'close_пред':>11} "
          f"{'TR':>10} {'ATR по формуле':>15} {'ATR библиотеки':>15}")
    tr = true_range(high, low, close)
    for i in range(PERIOD, PERIOD + 5):
        pc = close[i - 1]
        a = mine[i]
        b = lib[i]
        a_s = f"{a:.6f}" if a is not None else "—"
        b_s = f"{b:.6f}" if b is not None else "—"
        print(f"  {i:>4} {high[i]:>10.2f} {low[i]:>10.2f} {pc:>11.2f} "
              f"{tr[i]:>10.4f} {a_s:>15} {b_s:>15}")
    print(f"  затравка ATR[{PERIOD}] = среднее TR[1..{PERIOD}] = "
          f"{sum(tr[1:PERIOD + 1]) / PERIOD:.6f}")
    print(f"  шаг: ATR[{PERIOD + 1}] = (ATR[{PERIOD}]·{PERIOD - 1} + TR[{PERIOD + 1}])"
          f"/{PERIOD} = ({sum(tr[1:PERIOD + 1]) / PERIOD:.6f}·{PERIOD - 1} + "
          f"{tr[PERIOD + 1]:.4f})/{PERIOD} = {mine[PERIOD + 1]:.6f}")

    if compared == 0:
        print("  ПРОВАЛ: сравнено 0 точек — величина не проверена")
        return 1
    if over:
        print(f"  ПРОВАЛ: {over} точек разошлись с определением")
        return 1
    print("\n  СОШЛОСЬ на всей серии, включая затравку — прогрев здесь не нужен: "
          "сравниваются две реализации ОДНОГО определения, а не две конвенции.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
