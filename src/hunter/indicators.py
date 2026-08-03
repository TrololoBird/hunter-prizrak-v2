"""Канонические величины §2.9 как выражения polars. FOUNDATION.md §10.2, §0.2.

§10.2: «Индикаторы: не писать свои» — считает `polars_talib`.

ИСКЛЮЧЕНИЕ — MACD. Он определён как разность EMA12 и EMA26 тремя источниками
(Wikipedia, StockCharts, TradingView), а `polars_talib.macd` этому определению НЕ
удовлетворяет: замер 2026-08-03 показал, что он не равен разности собственных
`ema(12)` и `ema(26)` библиотеки — расхождение 5.874e-02 на баре 33, ноль с бара 300.
Библиотека, противоречащая своим же EMA, неправа по определению, поэтому MACD здесь
СОБИРАЕТСЯ из библиотечных EMA, а не берётся готовым.
Протокол: docs/audit/macd-talib-inconsistency-2026-08-03.md

Собственных реализаций сглаживания тут нет: EMA по-прежнему считает библиотека.
"""

from __future__ import annotations

import polars as pl
import polars_talib as plta


# У polars_talib нет типовых стабов, его выражения приходят как Any. Приведение
# собрано в одном месте, а не рассыпано по функциям: так видно, где кончается
# типизированная часть проекта.
def _expr(x: object) -> pl.Expr:
    assert isinstance(x, pl.Expr)
    return x


MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


def atr(period: int = 14) -> pl.Expr:
    return _expr(plta.atr(pl.col("high"), pl.col("low"), pl.col("close"),
                          timeperiod=period))


def rsi(period: int = 14) -> pl.Expr:
    return _expr(plta.rsi(pl.col("close"), timeperiod=period))


def ema(period: int) -> pl.Expr:
    return _expr(plta.ema(pl.col("close"), timeperiod=period))


def macd_line(fast: int = MACD_FAST, slow: int = MACD_SLOW) -> pl.Expr:
    """MACD = EMA(fast) − EMA(slow). Определение, а не вызов macd() библиотеки."""
    return ema(fast) - ema(slow)


def macd_signal(fast: int = MACD_FAST, slow: int = MACD_SLOW,
                signal: int = MACD_SIGNAL) -> pl.Expr:
    """Сигнальная линия — EMA сигнального периода от линии MACD.

    ⚠ Считается на СЫРОМ ряду MACD, в котором первые бары пусты; библиотечная `ema`
    на ряду с ведущими null может вести себя иначе, чем на плотном ряду. Пока эта
    величина §2.9 не используется (там названа «MACD (дивергенция/конвергенция)»),
    и до её применения поведение надо замерить.
    """
    return _expr(plta.ema(macd_line(fast, slow), timeperiod=signal))


def bbands_upper(period: int = 20, dev: float = 2.0) -> pl.Expr:
    return _expr(plta.bbands(pl.col("close"), timeperiod=period, nbdevup=dev,
                             nbdevdn=dev)).struct.field("upperband")


def bbands_lower(period: int = 20, dev: float = 2.0) -> pl.Expr:
    return _expr(plta.bbands(pl.col("close"), timeperiod=period, nbdevup=dev,
                             nbdevdn=dev)).struct.field("lowerband")
