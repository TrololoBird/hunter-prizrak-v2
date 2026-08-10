"""ГЕЙТ: канонические величины против ОПУБЛИКОВАННЫХ ФОРМУЛ, а не против библиотек.

Замечание владельца 2026-08-03: согласие двух библиотек не доказывает, что обе верны —
они могут разделять общую конвенцию. Внешний референт по §0 — сама формула.

Всё ниже посчитано СКАЛЯРНЫМ ЦИКЛОМ: без polars, без ta-библиотек, по формуле из
указанного источника. Сверяется с тем, что выдаёт проектный код (polars_talib).

ИСТОЧНИКИ ФОРМУЛ (вторичные изложения первоисточников; книг в проекте нет):
  ATR, RSI  — J.W. Wilder, New Concepts in Technical Trading Systems (1978),
              https://en.wikipedia.org/wiki/Average_true_range
  EMA       — s_t = a*x_t + (1-a)*s_{t-1}; a = 2/(k+1),
              https://en.wikipedia.org/wiki/Exponential_smoothing
  MACD      — MACD = EMA12 - EMA26; сигнал = EMA9(MACD),
              https://en.wikipedia.org/wiki/MACD
  Bollinger — середина = SMA(N); границы = SMA +- K*sigma; N=20, K=2;
              sigma ПОПУЛЯЦИОННОЕ (делитель n, не n-1),
              https://en.wikipedia.org/wiki/Bollinger_Bands
  ADX       — UpMove/DownMove, +DM/-DM, +DI/-DI, DX, сглаживание Уайлдера,
              https://en.wikipedia.org/wiki/Average_directional_movement_index

Затравка экспоненциальных величин: источники допускают разные варианты, TA-Lib берёт
простое среднее первых n значений. Здесь взят тот же вариант, и это ПРОВЕРЯЕТСЯ
сравнением, а не принимается на веру: расхождение печатается числом.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import polars as pl
import polars_talib as plta

TOLERANCE = 1e-9
SLICE = Path("docs/audit/reference-slice/BTCUSDT-1h-500.parquet")

sys.path.insert(0, "src")
from hunter import indicators  # noqa: E402
from hunter.admission import CANONICAL_FROM_BARS  # noqa: E402


def ema(x: list[float], n: int) -> list[float | None]:
    """s_t = a*x_t + (1-a)*s_{t-1}, a = 2/(n+1); затравка — ПЕРВЫМ значением (TV).

    ⚠ До 2026-08-09 эталон сеялся SMA первых n (конвенция TA-Lib/StockCharts). Проект
    перевёл боевую EMA на конвенцию TradingView — инструмента курса (стр. 69), — и
    эталон переведён ТОЙ ЖЕ правкой: расхождение конвенций не «прогревом гасится»,
    а именно конвенция. Реализации остаются независимыми: рукописная рекурсия против
    `ewm_mean` polars.
    """
    out: list[float | None] = [None] * len(x)
    if not x:
        return out
    a = 2.0 / (n + 1)
    prev = x[0]
    out[0] = prev
    for t in range(1, len(x)):
        prev = a * x[t] + (1 - a) * prev
        out[t] = prev
    return out


def true_range(high: list[float], low: list[float], close: list[float]) -> list[float]:
    tr = [high[0] - low[0]]
    for t in range(1, len(high)):
        pc = close[t - 1]
        tr.append(max(high[t] - low[t], abs(high[t] - pc), abs(low[t] - pc)))
    return tr


def wilder_smooth(x: list[float], n: int, start: int) -> list[float | None]:
    """v_t = (v_{t-1}*(n-1) + x_t)/n; затравка — среднее x[start .. start+n-1]."""
    out: list[float | None] = [None] * len(x)
    if len(x) < start + n:
        return out
    prev = sum(x[start:start + n]) / n
    out[start + n - 1] = prev
    for t in range(start + n, len(x)):
        prev = (prev * (n - 1) + x[t]) / n
        out[t] = prev
    return out


def atr(high: list[float], low: list[float], close: list[float],
        n: int = 14) -> list[float | None]:
    return wilder_smooth(true_range(high, low, close), n, start=1)


def rsi(close: list[float], n: int = 14) -> list[float | None]:
    gains = [0.0]
    losses = [0.0]
    for t in range(1, len(close)):
        d = close[t] - close[t - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    out: list[float | None] = [None] * len(close)
    if len(close) <= n:
        return out
    ag = sum(gains[1:n + 1]) / n
    al = sum(losses[1:n + 1]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for t in range(n + 1, len(close)):
        ag = (ag * (n - 1) + gains[t]) / n
        al = (al * (n - 1) + losses[t]) / n
        out[t] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd_line(close: list[float], fast: int = 12, slow: int = 26) -> list[float | None]:
    """MACD = EMA(fast) − EMA(slow). Выравнивания по сигнальной линии НЕТ:
    оно было особенностью plta.macd(), а не частью определения."""
    ef, es = ema(close, fast), ema(close, slow)
    raw: list[float | None] = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ef, es, strict=True)
    ]
    return raw


def bbands_upper(close: list[float], n: int = 20, k: float = 2.0) -> list[float | None]:
    """SMA +- K*sigma, sigma ПОПУЛЯЦИОННОЕ (делитель n)."""
    out: list[float | None] = [None] * len(close)
    for t in range(n - 1, len(close)):
        w = close[t - n + 1:t + 1]
        m = sum(w) / n
        var = sum((v - m) ** 2 for v in w) / n
        out[t] = m + k * var ** 0.5
    return out


def bbands_lower(close: list[float], n: int = 20, k: float = 2.0) -> list[float | None]:
    """SMA − K*sigma, sigma ПОПУЛЯЦИОННОЕ (делитель n).

    ⚠ Заведена 2026-08-07. Нижнюю полосу зовёт `card.py:274`, и до этого дня её не
    проверял НИ ОДИН гейт: сверялась только верхняя. Замечание QA, `docs/audit/09-qa.md`.
    Отдельная функция, а не знак у верхней: копия формулы со своим знаком проверяет
    ровно то, что читается в источнике, и не наследует ошибку соседа.
    """
    out: list[float | None] = [None] * len(close)
    for t in range(n - 1, len(close)):
        w = close[t - n + 1:t + 1]
        m = sum(w) / n
        var = sum((v - m) ** 2 for v in w) / n
        out[t] = m - k * var ** 0.5
    return out


def adx(high: list[float], low: list[float], close: list[float],
        n: int = 14) -> list[float | None]:
    plus_dm = [0.0]
    minus_dm = [0.0]
    for t in range(1, len(high)):
        up = high[t] - high[t - 1]
        down = low[t - 1] - low[t]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    s_tr = wilder_smooth(true_range(high, low, close), n, start=1)
    s_p = wilder_smooth(plus_dm, n, start=1)
    s_m = wilder_smooth(minus_dm, n, start=1)

    dx: list[float | None] = [None] * len(high)
    for t in range(len(high)):
        a, b, c = s_p[t], s_m[t], s_tr[t]
        if a is None or b is None or c is None or c == 0:
            continue
        pdi, mdi = 100 * a / c, 100 * b / c
        s = pdi + mdi
        dx[t] = 0.0 if s == 0 else 100 * abs(pdi - mdi) / s

    idx = [i for i, v in enumerate(dx) if v is not None]
    out: list[float | None] = [None] * len(high)
    if len(idx) < n:
        return out
    seed = [dx[i] for i in idx[:n]]
    prev = sum(v for v in seed if v is not None) / n
    out[idx[n - 1]] = prev
    for i in idx[n:]:
        cur = dx[i]
        assert cur is not None
        prev = (prev * (n - 1) + cur) / n
        out[i] = prev
    return out


def compare(a: list[float | None], b: list[float | None],
            skip: int = 0) -> tuple[int, float, int]:
    """Сравнить, пропустив первые `skip` баров — там расходится не формула, а затравка."""
    compared = 0
    worst = 0.0
    over = 0
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if i < skip:
            continue
        # `math.isnan` вместо идиомы `x != x` — см. пояснение в indicator_oracle.py.
        if x is None or y is None or math.isnan(x) or math.isnan(y):
            continue
        compared += 1
        rel = abs(x - y) / max(abs(x), abs(y), 1e-9)
        worst = max(worst, rel)
        if rel > TOLERANCE:
            over += 1
    return compared, worst, over


def main() -> int:
    if not SLICE.exists():
        print(f"ПРОВАЛ: нет среза {SLICE} — проверка не состоялась")
        return 1
    df = pl.read_parquet(SLICE)
    h, lo, c = df["high"].to_list(), df["low"].to_list(), df["close"].to_list()

    cases: list[tuple[str, list[float | None], pl.Expr]] = [
        ("atr14", atr(h, lo, c, 14),
         plta.atr(pl.col("high"), pl.col("low"), pl.col("close"), timeperiod=14)),
        # ⚠ Правка аудита 2026-08-06 (М-06 = Н-5): здесь стояли прямые вызовы `plta.*`,
        # то есть гейт сверял с формулой БИБЛИОТЕКУ, а не обёртку проекта. Подмена
        # периода в `hunter/indicators.py` проезжала мимо (evidence/E-020-gate-probes).
        ("rsi14", rsi(c, 14), indicators.rsi()),
        ("ema200", ema(c, 200), indicators.ema(200)),
        # MACD сверяется с ПРОЕКТНЫМ определением (ema12 - ema26), а не с
        # plta.macd(): тот противоречит своим же EMA, см.
        # docs/audit/macd-talib-inconsistency-2026-08-03.md
        ("macd", macd_line(c), indicators.macd_line()),
        ("bb_upper", bbands_upper(c, 20, 2.0), indicators.bbands_upper()),
        # ⚠ `bb_lower` добавлена 2026-08-07: её зовёт `card.py:274`, и до сих пор
        # её не проверял НИ ОДИН гейт (замечание QA, 09-qa.md раздел «б»).
        ("bb_lower", bbands_lower(c, 20, 2.0), indicators.bbands_lower()),
        ("adx14", adx(h, lo, c, 14),
         plta.adx(pl.col("high"), pl.col("low"), pl.col("close"), timeperiod=14)),
    ]

    print(f"гейт «против опубликованных формул»: срез {SLICE.name}, баров {df.height}, "
          f"величин {len(cases)}, порог {TOLERANCE:g}")
    failed = 0
    for name, mine, expr in cases:
        lib = df.select(expr.alias("v"))["v"].to_list()
        canon = CANONICAL_FROM_BARS.get(name)
        if canon is None:
            print(f"  ПРОВАЛ {name}: точка каноничности не замерена")
            failed += 1
            continue
        skip = canon - 1
        _, worst_all, over_all = compare(mine, lib, skip=0)
        compared, worst, over = compare(mine, lib, skip=skip)
        seed = "затравка подтверждена" if over_all == 0 else (
            f"ЗАТРАВКА РАСХОДИТСЯ до бара {canon} (макс {worst_all:.1e})"
        )
        status = "СОШЛОСЬ" if over == 0 else f"РАЗОШЛОСЬ на {over}"
        print(f"  {name:9} каноничен с {canon:4}  сравнено {compared:4}  "
              f"худшее {worst:.3e}  {status}")
        print(f"  {'':9} {seed}")
        if compared == 0:
            print(f"  ПРОВАЛ {name}: сравнено 0 точек — величина не проверена")
            failed += 1
        if over:
            failed += 1
    if failed:
        print(f"\nПРОВАЛ: величин с расхождением {failed}")
        return 1
    print("\nВсе величины совпали со своей опубликованной формулой. Круговой референт снят.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
