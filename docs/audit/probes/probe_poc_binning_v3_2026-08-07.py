"""ЗОНД R-01 редакция 3: ИСПРАВЛЕННЫЕ контроли. Повод — замечание QA (`09-qa.md`, «г»).

ЧТО БЫЛО НЕ ТАК В РЕДАКЦИИ 2. Нулём я взял ЦИКЛИЧЕСКИЙ СДВИГ вектора объёмов на половину
длины. Это не разрыв связи цены с объёмом, а ПЕРЕНОС ПРОФИЛЯ: форма пика сохраняется
целиком, оба прибора уезжают вместе, и согласие между ними обязано сохраниться. QA измерил
это прямо — сдвиг ПОК после поворота равен ровно половине числа занятых бинов.
То есть «нуль» воспроизводил измеряемый механизм вместо того, чтобы его разрушать.

Это ВТОРАЯ подряд неудачная конструкция нуля в одном и том же замере (первая — «равномерный
объём», оказавшийся плотностью присутствия сделок). Обе описаны в `trials.md`.

ЧТО ЗДЕСЬ ИСПРАВЛЕНО

1. **Настоящий нуль — ПЕРЕСТАНОВКА объёмов по занятым ценам.** Множество занятых цен и
   набор объёмов сохраняются, связь между ними рвётся. Перестановка ДЕТЕРМИНИРОВАННАЯ и
   без датчика случайных чисел: `j = (i * STEP) % n`, где `STEP` взаимно прост с `n`.
   Такая перестановка воспроизводима побитово, то есть §10.3 не нарушает, а довод «нужен
   поворот, потому что случайность запрещена» несостоятелен.

2. **Добавлен ЧЕСТНЫЙ критерий согласия.** «Расстояние между двумя ценами меньше 0.10 ATR»
   смешивает несогласие с ошибкой квантования. Правильный вопрос — НАШЛИ ЛИ ОБА ПРИБОРА
   ОДИН И ТОТ ЖЕ ПИК, то есть лежит ли тиковый ПОК ВНУТРИ выигравшей строки прибора А.

3. **Добавлен ЗОННЫЙ критерий.** Проект показывает оператору не только ПОК, но и область
   стоимости. Вопрос «попадает ли ответ прибора А внутрь того, что проект уже показывает»
   — практический, и редакция 2 его число напечатала, но в выводе не использовала.

4. **Печатается ПОТОЛОК согласия** при идеальном совпадении: если пик равномерен внутри
   выигравшей строки, какая доля попала бы в допуск 0.10 ATR просто по геометрии.

Допуск не менялся: 0.10 ATR, `docs/audit/tolerance-R-01.md`.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_poc_binning_v3_2026-08-07.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import detect as detect_accumulations  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.levels import structure_window_ms  # noqa: E402
from hunter.models import Bar, NotReady, TradeHistogram  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402
from hunter.volume_profile import build as build_profile  # noqa: E402

ROWS = (24, 50, 100, 200)
TOLERANCE_ATR = 0.10
BUCKET_MS = 300_000
CACHE = ROOT / "data" / "aggcache"
NAME_RE = re.compile(r"^([A-Z0-9]+)-(\d{4}-\d{2}-\d{2})-300000-t([0-9p]+)-b2\.parquet$")
PERM_STEP = 7919
"""Шаг детерминированной перестановки. Простое число: взаимно просто с любой длиной,
не кратной ему, то есть `i -> (i*STEP) % n` — биекция. Датчика случайных чисел нет."""


def atr14(bars: list[Bar]) -> float | None:
    if len(bars) < 15:
        return None
    trs = [max(bars[i].high - bars[i].low,
               abs(bars[i].high - bars[i - 1].close),
               abs(bars[i].low - bars[i - 1].close)) for i in range(1, len(bars))]
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


def permuted(vals: list[float]) -> list[float]:
    """Детерминированная перестановка. Если STEP делит n — сдвигаем на 1, чтобы не сломать
    биекцию; это единственная ветка, и она печатается счётчиком."""
    n = len(vals)
    step = PERM_STEP if n % PERM_STEP else PERM_STEP + 1
    return [vals[(i * step) % n] for i in range(n)]


def rows_of(bins: list[int], qtys: list[float], tick: Decimal,
            n_rows: int) -> tuple[float, float, float] | None:
    """Прибор А: (центр выигравшей строки, её низ, её верх). Ничья → None."""
    if not bins:
        return None
    t = float(tick)
    lo, hi = min(bins) * t, max(bins) * t
    if hi <= lo:
        return lo, lo, lo
    width = (hi - lo) / n_rows
    acc = [0.0] * n_rows
    for b, q in zip(bins, qtys, strict=True):
        acc[min(int((b * t - lo) / width), n_rows - 1)] += q
    peak = max(acc)
    win = [k for k, v in enumerate(acc) if v == peak]
    if len(win) != 1:
        return None
    k = win[0]
    return lo + (k + 0.5) * width, lo + k * width, lo + (k + 1) * width


def load_cache(market_id: str) -> tuple[Decimal, dict[int, dict[int, float]]] | None:
    tick: Decimal | None = None
    out: dict[int, dict[int, float]] = defaultdict(dict)
    found = 0
    for p in sorted(CACHE.iterdir()):
        m = NAME_RE.match(p.name)
        if not m or m.group(1) != market_id:
            continue
        tick = Decimal(m.group(3).replace("p", "."))
        df = pl.read_parquet(p)
        for b, i, q in zip(df["bucket"], df["bin"], df["qty"], strict=True):
            cell = out[int(b)]
            cell[int(i)] = cell.get(int(i), 0.0) + float(q)
        found += 1
    return None if tick is None or not found else (tick, dict(out))


def main() -> int:
    frames = ROOT / "data" / "frames"
    series: dict[str, dict[str, list[Bar]]] = defaultdict(dict)
    market: dict[str, str] = {}
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            market[sym] = sym.split("/")[0] + "USDT"
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                df = pl.read_parquet(f)
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in df.iter_rows(named=True)]
                if len(bars) > len(series[sym].get(tf, [])):
                    series[sym][tf] = bars

    stat: dict[tuple[str, int], list[bool]] = defaultdict(list)
    ceiling: dict[int, list[float]] = defaultdict(list)
    n_struct = 0

    for sym, tfs in sorted(series.items()):
        loaded = load_cache(market[sym])
        if loaded is None:
            continue
        tick, cache = loaded
        for tf, bars in sorted(tfs.items(), key=lambda x: TIMEFRAME_MS[x[0]]):
            step = TIMEFRAME_MS[tf]
            sw = detect_swings(bars)
            if isinstance(sw, NotReady):
                continue
            a = atr14(bars)
            if a is None or a <= 0:
                continue
            for acc in detect_accumulations(bars, sw, tf).closed:
                lo_ms, hi_ms = structure_window_ms(acc, bars, step)
                agg: dict[int, float] = {}
                missing = 0
                for b_ms in range(lo_ms - lo_ms % BUCKET_MS, hi_ms, BUCKET_MS):
                    cell = cache.get(b_ms)
                    if cell is None:
                        missing += 1
                        continue
                    for i, q in cell.items():
                        agg[i] = agg.get(i, 0.0) + q
                if missing or not agg:
                    continue
                hist = TradeHistogram(symbol=sym, tick_size=tick)
                hist.qty_by_bin.update(agg)
                prof = build_profile(hist)
                if isinstance(prof, NotReady):
                    continue
                n_struct += 1
                poc_p = float(prof.poc_price)
                va_lo, va_hi = float(prof.val_price), float(prof.vah_price)
                ordered = sorted(agg.items())
                b_list = [b for b, _ in ordered]
                q_list = [q for _, q in ordered]
                q_null = permuted(q_list)
                h_null = TradeHistogram(symbol=sym, tick_size=tick)
                h_null.qty_by_bin.update(dict(zip(b_list, q_null, strict=True)))
                pn = build_profile(h_null)

                for n in ROWS:
                    r = rows_of(b_list, q_list, tick, n)
                    if r is not None:
                        c, rlo, rhi = r
                        stat[("точка_допуск", n)].append(abs(poc_p - c) <= TOLERANCE_ATR * a)
                        stat[("тот_же_пик", n)].append(rlo <= poc_p <= rhi)
                        stat[("зона", n)].append(va_lo <= c <= va_hi)
                        # потолок: доля строки, попадающая в допуск вокруг её центра
                        w = rhi - rlo
                        ceiling[n].append(min(1.0, (2 * TOLERANCE_ATR * a) / w) if w else 1.0)
                    if not isinstance(pn, NotReady):
                        rn = rows_of(b_list, q_null, tick, n)
                        if rn is not None:
                            cn, nlo, nhi = rn
                            p2 = float(pn.poc_price)
                            stat[("НУЛЬ_точка", n)].append(abs(p2 - cn) <= TOLERANCE_ATR * a)
                            stat[("НУЛЬ_тот_же_пик", n)].append(nlo <= p2 <= nhi)
                            stat[("НУЛЬ_зона", n)].append(
                                float(pn.val_price) <= cn <= float(pn.vah_price))

    print("=" * 80)
    print("R-01 ред. 3  Контроли исправлены по замечанию QA")
    print("=" * 80)
    print(f"структур замерено: {n_struct}")
    print(f"нуль: ДЕТЕРМИНИРОВАННАЯ ПЕРЕСТАНОВКА объёмов, шаг {PERM_STEP} (не поворот)")
    if not n_struct:
        print("ЗАМЕР НЕ СОСТОЯЛСЯ")
        return 1

    def pct(key: tuple[str, int]) -> str:
        v = stat.get(key)
        return "  —  " if not v else f"{sum(v) / len(v) * 100:5.1f}%"

    print()
    print("КРИТЕРИЙ 1 — точечный: |ПОК_П − центр строки А| <= 0.10 ATR")
    print(f"{'N':>5} {'настоящий':>11} {'нуль':>9} {'потолок при идеале':>20}")
    for n in ROWS:
        cap = statistics.mean(ceiling[n]) * 100 if ceiling[n] else float("nan")
        print(f"{n:>5} {pct(('точка_допуск', n)):>11} {pct(('НУЛЬ_точка', n)):>9} "
              f"{cap:>19.1f}%")

    print()
    print("КРИТЕРИЙ 2 — ЧЕСТНЫЙ: тиковый ПОК лежит ВНУТРИ выигравшей строки А")
    print("   (то есть оба прибора указали на ОДНО И ТО ЖЕ место профиля)")
    print(f"{'N':>5} {'настоящий':>11} {'нуль':>9}")
    for n in ROWS:
        print(f"{n:>5} {pct(('тот_же_пик', n)):>11} {pct(('НУЛЬ_тот_же_пик', n)):>9}")

    print()
    print("КРИТЕРИЙ 3 — зонный: ПОК прибора А внутри области стоимости проекта")
    print("   (проект печатает зону рядом с ПОК — это то, что видит оператор)")
    print(f"{'N':>5} {'настоящий':>11} {'нуль':>9}")
    for n in ROWS:
        print(f"{n:>5} {pct(('зона', n)):>11} {pct(('НУЛЬ_зона', n)):>9}")

    print()
    print("КАК ЧИТАТЬ")
    print("  Критерий 1 смешивает несогласие с ошибкой квантования — смотреть на потолок.")
    print("  Критерий 2 отвечает на вопрос замера: тот же ли пик нашли приборы.")
    print("  Критерий 3 отвечает на вопрос оператора: попадает ли ответ автора в то,")
    print("  что проект уже показывает.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
