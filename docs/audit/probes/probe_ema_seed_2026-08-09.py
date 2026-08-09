"""ЗОНД: чем на НАШИХ данных отличается EMA200 с затравкой TA-Lib от затравки TradingView.

ВОПРОС (indicators-conformance-2026-08-09, строка EMA): формула одна, конвенции затравки
разные. TA-Lib/StockCharts сеют SMA первых N и начинают с бара N−1; TV сеет ПЕРВЫМ close
и рисует с бара 0. Вес чужой затравки затухает как (1−α)^t, α = 2/(N+1). Вопрос «чей
референт» стоит только если разница видна на наших рядах — это и меряется.

ЗАМЕР: по всем сериям баров в сохранённых кадрах — относительная разница двух EMA200 на
ПОСЛЕДНЕМ баре, в процентах от цены; распределение по длине ряда.

КОНТРОЛЬ: прибор обязан уметь дать ИНОЙ ответ — на коротком ряду (чуть больше 200 баров)
разница обязана быть заметной, на длинном — исчезать. Печатаются обе группы; если бы
разница была нулевой везде, это был бы дефект зонда (две ветки сеются по-разному).

Команда воспроизведения:
    uv run python docs/audit/probes/probe_ema_seed_2026-08-09.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS  # noqa: E402

PERIOD = 200
ALPHA = 2.0 / (PERIOD + 1)


def ema_talib(closes: list[float]) -> float | None:
    """Затравка SMA первых N (TA-Lib `ta_EMA.c`), значение с бара N−1."""
    if len(closes) < PERIOD:
        return None
    val = sum(closes[:PERIOD]) / PERIOD
    for c in closes[PERIOD:]:
        val = ALPHA * c + (1 - ALPHA) * val
    return val


def ema_tv(closes: list[float]) -> float:
    """Затравка первым close (эталонный код `ta.ema` TradingView), с бара 0."""
    val = closes[0]
    for c in closes[1:]:
        val = ALPHA * c + (1 - ALPHA) * val
    return val


def main() -> int:
    frames = ROOT / "data" / "frames"
    diffs: list[tuple[int, float]] = []  # (длина ряда, |разница| в % от цены)
    skipped_short = 0
    runs = 0

    for run_dir in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run_dir.iterdir()):
            if not (sym_dir / "meta.json").exists():
                continue
            json.loads((sym_dir / "meta.json").read_text(encoding="utf-8"))
            saw = False
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                saw = True
                closes = pl.read_parquet(f)["close"].to_list()
                a = ema_talib(closes)
                if a is None:
                    skipped_short += 1
                    continue
                b = ema_tv(closes)
                price = closes[-1]
                diffs.append((len(closes), abs(a - b) / price * 100))
            if saw:
                runs += 1

    if not diffs:
        print("рядов длиной ≥ 200 баров нет — замер не состоялся")
        return 1

    diffs.sort()
    vals = [d for _, d in diffs]
    short = [d for n, d in diffs if n < 300]
    long_ = [d for n, d in diffs if n >= 500]
    print("=" * 78)
    print("EMA200: затравка TA-Lib (наша) против TradingView — разница на последнем баре")
    print(f"отпечаток данных: серий {len(diffs)} (из {runs} прогонов), "
          f"короче 200 баров пропущено {skipped_short}")
    print(f"разница, % от цены: медиана {statistics.median(vals):.4g}, "
          f"максимум {max(vals):.4g}")
    print(f"ряды < 300 баров ({len(short)} шт.): медиана "
          f"{statistics.median(short):.4g}%" if short else "рядов < 300 баров нет")
    print(f"ряды ≥ 500 баров ({len(long_)} шт.): медиана "
          f"{statistics.median(long_):.4g}%, максимум {max(long_):.4g}%"
          if long_ else "рядов ≥ 500 баров нет")
    worst = max(diffs, key=lambda t: t[1])
    print(f"худший случай: ряд {worst[0]} баров, разница {worst[1]:.4g}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
