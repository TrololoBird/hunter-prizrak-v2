"""ЗОНД: воспроизводит ли разметку автора профиль ПО СВЕЧАМ так же, как по тикам.

⚠ ПОВОД — ВОПРОС ВЛАДЕЛЬЦА 2026-08-12: «вызывает сомнения идея строить и рассчитывать всё
через aggtrades, а не через свечи; кажется, ИИ изобрёл велосипед вместо повсеместной
практики». Обзор двадцати чужих проектов сомнение подтвердил: сигнальные боты берут свечи,
сделок не берут вовсе. TradingView, инструмент автора, строит профиль по свечам МЛАДШЕГО
разрешения, а не по тикам («Volume profile indicators are calculated using data from lower
timeframes for the same symbol»).

⚠ ЧТО ЗДЕСЬ ПОДМЕНЯЕТСЯ, А ЧТО НЕТ. Подменяется РОВНО ОДНО — источник профиля. Отсечка,
допуск, разбор разметки, критерий совпадения, контроли и вся цепочка построения уровней
берутся ИМПОРТОМ из зонда R-03 (`probe_author_conformance_2026-08-07.py`), а не копией.
Иначе сравнение поехало бы на различии рамок, а не приборов.

Источники профиля:

    ТИК          суточный кэш aggTrade — нынешний боевой
    СВЕЧИ-РАЗМАЗ объём каждой свечи размазан РАВНОМЕРНО по её диапазону high-low
                 (семейство TradingView), разрешения 1м / 5м / 15м
    СВЕЧИ-ЗАКР   весь объём свечи в бин ЦЕНЫ ЗАКРЫТИЯ — так делает
                 Haehnchen/crypto-trading-bot: «Volume Profile - requires candles»

Бинирование у всех источников ОДНО И ТО ЖЕ (тот же `tick_size`), иначе сравнивались бы
сетки, а не профили.

⚠ Контроли те же, что в R-03, и они здесь главное: решётка равноотстоящих цен и СДВИГ всей
разметки на ±1% и ±2%. Прибор, который не бьёт собственные сдвиги, не показал ничего.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_author_candles_2026-08-12.py
"""

from __future__ import annotations

import asyncio
import bisect
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.engine import decide  # noqa: E402
from hunter.exchange import Exchange  # noqa: E402
from hunter.models import Bar, NotReady, TradeHistogram, bin_index  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "r03", Path(__file__).resolve().parent / "probe_author_conformance_2026-08-07.py")
assert _spec and _spec.loader
r03 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r03)

RESOLUTIONS = ("1m", "5m", "15m")
DAYS_BACK = 120
PAGE = 499
STORE = Path(__file__).resolve().parent / "_candles_author"
MAX_BINS_PER_BAR = 20_000
"""Предел размазывания одной свечи. Бар шире — это не рынок, а битые данные; считается
отдельно и печатается, потому что молчаливый пропуск читался бы как «размазали всё»."""


class CandleWindows:
    """Профиль по окну ИЗ СВЕЧЕЙ. Тот же протокол, что у тикового источника.

    `mode='размаз'` — объём бара поровну по всем бинам его диапазона (так строит профиль
    TradingView по данным младшего разрешения). `mode='закр'` — весь объём в бин цены
    закрытия (так делает `volume_by_price` у Haehnchen/crypto-trading-bot).
    """

    def __init__(self, symbol: str, tick: Decimal, bars: list[Bar], mode: str):
        self.symbol, self.tick, self.mode = symbol, tick, mode
        self.bars = sorted(bars, key=lambda b: b.open_ms)
        self.keys = [b.open_ms for b in self.bars]
        self.too_wide = 0

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady:
        i = bisect.bisect_left(self.keys, from_ms)
        j = bisect.bisect_left(self.keys, to_ms)
        chunk = self.bars[i:j]
        if not chunk:
            return NotReady(reason=f"{self.symbol}: окно не покрыто свечами")
        h = TradeHistogram(symbol=self.symbol, tick_size=self.tick)
        if self.mode == "закр":
            for b in chunk:
                k = bin_index(b.close, self.tick)
                h.qty_by_bin[k] = h.qty_by_bin.get(k, 0.0) + b.volume
                h.trades_seen += 1
                h.qty_seen += b.volume
        else:
            # ⚠ РАЗНОСТНЫЙ МАССИВ, А НЕ ЦИКЛ ПО БИНАМ. Первая редакция добавляла долю
            # объёма в каждый бин диапазона бара — O(баров × бинов). На минутных свечах
            # BTC это порядка 13 млн операций НА ОКНО при сотне окон, то есть зонд не
            # досчитывался вовсе (остановлен вручную). Здесь то же самое считается за
            # O(баров + ширина диапазона): каждому бару правится только начало и конец
            # его отрезка, а один проход префиксной суммы разворачивает это в профиль.
            # Результат обязан совпасть с прежним — это проверяет контроль в `main`.
            spans: list[tuple[int, int, float]] = []
            k_lo = k_hi = None
            for b in chunk:
                k0 = bin_index(b.low, self.tick)
                k1 = bin_index(b.high, self.tick)
                if k1 - k0 + 1 > MAX_BINS_PER_BAR:
                    self.too_wide += 1
                    continue
                spans.append((k0, k1, b.volume / (k1 - k0 + 1)))
                k_lo = k0 if k_lo is None else min(k_lo, k0)
                k_hi = k1 if k_hi is None else max(k_hi, k1)
                h.trades_seen += 1
                h.qty_seen += b.volume
            if k_lo is not None and k_hi is not None:
                width = k_hi - k_lo + 2
                diff = [0.0] * width
                for k0, k1, share in spans:
                    diff[k0 - k_lo] += share
                    diff[k1 - k_lo + 1] -= share
                run = 0.0
                for i in range(width - 1):
                    run += diff[i]
                    if run > 0.0:
                        h.qty_by_bin[k_lo + i] = run
        if not h.qty_by_bin:
            return NotReady(reason=f"{self.symbol}: в окне нет объёма")
        return h


def slow_profile(bars: list[Bar], tick: Decimal) -> dict[int, float]:
    """ПРЯМОЙ, заведомо верный способ: доля объёма в каждый бин диапазона бара.

    Существует только ради контроля быстрого пути. Медленный настолько, что в замере
    неприменим, — но на десятке баров он и есть определение того, что должно получиться.
    """
    out: dict[int, float] = {}
    for b in bars:
        k0 = bin_index(b.low, tick)
        k1 = bin_index(b.high, tick)
        share = b.volume / (k1 - k0 + 1)
        for k in range(k0, k1 + 1):
            out[k] = out.get(k, 0.0) + share
    return out


def check_fast_equals_slow(bars: list[Bar], tick: Decimal) -> str:
    """Контроль: разностный массив против прямого цикла. Расхождение — стоп замеру."""
    sample = bars[:200]
    if not sample:
        return "контроль НЕ проведён: баров нет"
    src = CandleWindows("контроль", tick, sample, "размаз")
    fast = src.window(sample[0].open_ms, sample[-1].open_ms + 1)
    if isinstance(fast, NotReady):
        return f"контроль НЕ проведён: {fast.reason}"
    slow = slow_profile(sample, tick)
    keys = set(fast.qty_by_bin) | set(slow)
    worst = max((abs(fast.qty_by_bin.get(k, 0.0) - slow.get(k, 0.0)) for k in keys),
                default=0.0)
    scale = max(slow.values(), default=1.0)
    return (f"контроль быстрого пути: бинов {len(keys)}, наибольшее расхождение "
            f"{worst:.3e} при масштабе {scale:.3e} -> "
            f"{'СОВПАЛО' if worst <= scale * 1e-9 else 'РАСХОЖДЕНИЕ'}")


async def fetch(ex: Exchange, symbol: str, res: str, lo: int, hi: int,
                step: int) -> list[Bar]:
    STORE.mkdir(exist_ok=True)
    f = STORE / f"{symbol.replace('/', '_').replace(':', '_')}-{res}.parquet"
    if f.exists():
        df = pl.read_parquet(f)
        return [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                    high=float(r["high"]), low=float(r["low"]),
                    close=float(r["close"]), volume=float(r["volume"]))
                for r in df.iter_rows(named=True)]
    out: dict[int, Bar] = {}
    cursor = lo
    while cursor <= hi:
        raw: Any = await ex._rest(
            f"свечи {res}", f"{symbol} {res}",
            lambda c=cursor: ex._ex.fetch_ohlcv(symbol, res, since=c, limit=PAGE))
        if isinstance(raw, NotReady) or not raw:
            break
        for r in raw:
            t = int(r[0])
            if t > hi:
                continue
            try:
                out[t] = Bar(open_ms=t, open=float(r[1]), high=float(r[2]),
                             low=float(r[3]), close=float(r[4]), volume=float(r[5]))
            except ValueError:
                pass
        last = int(raw[-1][0])
        if last < cursor:
            break
        cursor = last + step
    bars = [out[k] for k in sorted(out)]
    pl.DataFrame({
        "open_ms": [b.open_ms for b in bars], "open": [b.open for b in bars],
        "high": [b.high for b in bars], "low": [b.low for b in bars],
        "close": [b.close for b in bars], "volume": [b.volume for b in bars],
    }).write_parquet(f)
    return bars


def report(tag: str, sym: str, prices: list[float], levels: list[float],
           tol: float) -> dict[str, Any]:
    hits, dists = r03.matched(prices, levels, tol)
    fin = [x for x in dists if x != float("inf")]
    lo, hi = min(prices), max(prices)
    grid = [lo + (hi - lo) * i / (len(prices) - 1) for i in range(len(prices))]
    g_hits, _ = r03.matched(grid, levels, tol)
    beats = 0
    shifts: list[int] = []
    for s in r03.SHIFTS:
        s_hits, _ = r03.matched([p * (1 + s) for p in prices], levels, tol)
        shifts.append(s_hits)
        if hits > s_hits:
            beats += 1
    med_atr = statistics.median(fin) / tol * r03.TOLERANCE_ATR
    print(f"  {tag:<20} уровней {len(levels):>4}  "
          f"воспроизведено {hits:>2}/{len(prices)} ({100 * hits / len(prices):>5.1f}%)  "
          f"медиана {med_atr:>5.2f}·ATR  решётка {g_hits:>2}  сдвиги {shifts}  "
          f"бьёт {beats}/{len(r03.SHIFTS)}")
    return {"hits": hits, "grid": g_hits, "shifts": shifts, "beats": beats,
            "levels": len(levels)}


async def main() -> int:
    print(f"отсечка {r03.CUTOFF_MS}; допуск {r03.TOLERANCE_ATR}·ATR14(4ч) — оба из R-03")
    author = r03.parse_markup()
    print(f"разметка: {', '.join(f'{k} — {len(v)} цен' for k, v in author.items())}")

    frames = ROOT / "data" / "frames"
    series: dict[str, dict[str, list[Bar]]] = defaultdict(dict)
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            if sym not in author:
                continue
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                df = pl.read_parquet(f)
                bb = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                          high=float(r["high"]), low=float(r["low"]),
                          close=float(r["close"]), volume=float(r["volume"]))
                      for r in df.iter_rows(named=True)]
                if len(bb) > len(series[sym].get(tf, [])):
                    series[sym][tf] = bb

    ex = Exchange("binanceusdm")
    await ex.open()
    lo_ms = r03.CUTOFF_MS - DAYS_BACK * 86_400_000
    summary: dict[str, dict[str, Any]] = {}

    for sym, prices in sorted(author.items()):
        tfs = series.get(sym)
        if not tfs:
            print(f"{sym}: кадров нет — НЕ СМОГ ПРОВЕРИТЬ")
            continue
        cut = {tf: [b for b in bars if b.open_ms + TIMEFRAME_MS[tf] <= r03.CUTOFF_MS]
               for tf, bars in tfs.items()}
        cut = {tf: b for tf, b in cut.items() if len(b) >= 20}
        a = r03.atr14(cut.get("4h", []))
        if a is None or a <= 0:
            print(f"{sym}: ATR14(4ч) не посчитан — НЕ СМОГ ПРОВЕРИТЬ")
            continue
        tol = r03.TOLERANCE_ATR * a
        print(f"\n--- {sym} --- ATR14(4ч) {a:.2f}, допуск {tol:.2f}")

        loaded = r03.load_cache(sym.split("/")[0] + "USDT")
        if loaded is None:
            print("  тикового кэша нет — эталон недоступен")
            continue
        tick, cells = loaded

        d = decide(sym, cut, r03.CacheWindows(sym, tick, cells), tuple(cut))
        summary[f"{sym}|ТИК"] = report(
            "ТИК (боевой)", sym, prices, [float(m.level.price) for m in d.mapped], tol)

        for res in RESOLUTIONS:
            step = {"1m": 60_000, "5m": 300_000, "15m": 900_000}[res]
            bars = await fetch(ex, sym, res, lo_ms, r03.CUTOFF_MS, step)
            if not bars:
                print(f"  свечи {res}: не скачались")
                continue
            print(f"  {res}: свечей {len(bars)}; {check_fast_equals_slow(bars, tick)}")
            for mode in ("размаз", "закр"):
                src = CandleWindows(sym, tick, bars, mode)
                dd = decide(sym, cut, src, tuple(cut))
                summary[f"{sym}|{res}-{mode}"] = report(
                    f"СВЕЧИ {res} {mode}", sym, prices,
                    [float(m.level.price) for m in dd.mapped], tol)
                if src.too_wide:
                    print(f"      ⚠ баров шире {MAX_BINS_PER_BAR} бинов пропущено: "
                          f"{src.too_wide}")
    await ex.close()

    print("\n" + "=" * 78)
    print("ИТОГ: бьёт ли источник СОБСТВЕННЫЕ сдвиговые контроли (из 4)")
    print("=" * 78)
    for k, v in sorted(summary.items()):
        print(f"  {k:<28} воспроизведено {v['hits']:>2}, решётка {v['grid']:>2}, "
              f"бьёт сдвигов {v['beats']}/4, уровней {v['levels']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
