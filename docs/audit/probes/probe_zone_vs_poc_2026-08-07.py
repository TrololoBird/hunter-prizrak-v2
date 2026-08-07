"""ЗОНД аудита М-08: отработка уровня считается по ПОК, а курс считает по ЗОНЕ.

ВОПРОС. Стр. 31 дословно:

    «ВАЖНО: если цена ранее забирала зону и уже получила от нее хорошую лонг реакцию,
     уровень лимитными ордерами больше не торгуем - т.к. уровень стал слабее, и в след
     раз может не отработать.»

То есть снимает лимитки заход в ЗОНУ, а не касание ПОК. Стр. 30 подтверждает, что заход в
зону — самостоятельный вариант отработки: «1 - цена забирает объемную зону, (выделено
желтым цветом) и идет в нужном направлении».

Проект решил иначе: `levels.first_test_index` требует, чтобы цена достала САМ ПОК
(`low <= ПОК <= high`), и это решение записано в его докстроке явно. Следствие, если
решение неверно: уровень, у которого цена забрала зону и развернулась, НЕ ДОЙДЯ до ПОК,
остаётся в состоянии «лимитки разрешены», хотя курс велит их снять.

ЗАМЕР. На сохранённых кадрах: сколько уровней меняют разрешение, если считать касанием
заход в зону [VAL, VAH] вместо касания ПОК.

КОНТРОЛЬ. Число обязано быть способно оказаться нулём: если у всех уровней первый заход в
зону совпадает с касанием ПОК, разницы нет и находка снимается. Печатается и распределение
задержки в барах между двумя событиями — если она нулевая у всех, замер это покажет.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_zone_vs_poc_2026-08-07.py
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.engine import decide  # noqa: E402
from hunter.levels import Level  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.store import read_binned_trades  # noqa: E402


def first_touch_poc(level: Level, bars: list[Bar]) -> int | None:
    """Как считает проект: цена достала САМ ПОК."""
    poc = float(level.price)
    for i in range(level.created_at_index + 1, len(bars)):
        if bars[i].low <= poc <= bars[i].high:
            return i
    return None


def first_touch_zone(level: Level, bars: list[Bar]) -> int | None:
    """Как велит стр. 31: цена ЗАБРАЛА ЗОНУ — вошла в [VAL, VAH] с нужной стороны."""
    lo, hi = float(level.zone_lo), float(level.zone_hi)
    for i in range(level.created_at_index + 1, len(bars)):
        if bars[i].low <= hi and bars[i].high >= lo:
            return i
    return None


def main() -> int:
    frames = ROOT / "data" / "frames"
    changed = 0
    same = 0
    only_zone = 0
    delays: list[int] = []
    by_tf: dict[str, int] = defaultdict(int)
    by_tf_total: dict[str, int] = defaultdict(int)
    examples: list[str] = []
    runs = 0

    for run_dir in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run_dir.iterdir()):
            meta_p = sym_dir / "meta.json"
            if not meta_p.exists():
                continue
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            symbol = meta["symbol"]
            from decimal import Decimal
            trades = read_binned_trades(run_dir.name, sym_dir.name,
                                        Decimal(meta["tick_size"]), int(meta["bucket_ms"]),
                                        symbol=symbol)
            series: dict[str, list[Bar]] = {}
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                df = pl.read_parquet(f)
                series[tf] = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                                  high=float(r["high"]), low=float(r["low"]),
                                  close=float(r["close"]), volume=float(r["volume"]))
                              for r in df.iter_rows(named=True)]
            if not series:
                continue
            runs += 1
            from hunter.models import NotReady
            d = decide(symbol, series, None if isinstance(trades, NotReady) else trades,
                       tuple(series))
            for m in d.mapped:
                bars = series.get(m.level.timeframe)
                if not bars:
                    continue
                by_tf_total[m.level.timeframe] += 1
                p = first_touch_poc(m.level, bars)
                z = first_touch_zone(m.level, bars)
                if z is None:
                    same += 1
                    continue
                if p is None:
                    only_zone += 1
                    changed += 1
                    by_tf[m.level.timeframe] += 1
                    if len(examples) < 6:
                        examples.append(
                            f"{symbol} {m.level.timeframe} ПОК {m.level.price} "
                            f"зона {m.level.zone_lo}…{m.level.zone_hi}: "
                            f"зону забрали на баре {z}, ПОК не касались ВОВСЕ"
                        )
                    continue
                if z < p:
                    changed += 1
                    delays.append(p - z)
                    by_tf[m.level.timeframe] += 1
                    if len(examples) < 6:
                        examples.append(
                            f"{symbol} {m.level.timeframe} ПОК {m.level.price} "
                            f"зона {m.level.zone_lo}…{m.level.zone_hi}: "
                            f"зона на баре {z}, ПОК на {p} — позже на {p - z} баров"
                        )
                else:
                    same += 1

    total = changed + same + 0
    print("=" * 78)
    print("М-08  Отработка уровня: касание ПОК (проект) против захода в зону (стр. 31)")
    print("=" * 78)
    print(f"кадров разобрано: {runs}")
    print(f"уровней всего:    {sum(by_tf_total.values())}")
    print(f"из них с заходом в зону: {total}")
    print()
    print(f"РАЗРЕШЕНИЕ МЕНЯЕТСЯ у {changed} уровней")
    print(f"  из них ПОК не касались вовсе: {only_zone}")
    print(f"  совпадает (зона и ПОК на одном баре либо ПОК раньше): {same}")
    if delays:
        d = sorted(delays)
        print(f"  задержка ПОК против зоны, баров: медиана {statistics.median(d):.0f}, "
              f"мин {d[0]}, макс {d[-1]}")
    print()
    print("ПО ТАЙМФРЕЙМУ")
    for tf in TIMEFRAME_MS:
        t = by_tf_total.get(tf, 0)
        if t:
            print(f"  {tf:>4}  меняется {by_tf.get(tf, 0):>4} из {t:>4}")
    if examples:
        print()
        print("ПРИМЕРЫ")
        for e in examples:
            print("  " + e)
    print()
    print("КОНТРОЛЬ: способен ли замер дать ноль?")
    if changed == 0:
        print("   да, и он его дал: разницы между двумя правилами на этих кадрах нет —")
        print("   находка СНИМАЕТСЯ.")
    else:
        print(f"   способен: {same} уровней попали в 'совпадает', то есть правило")
        print("   различает случаи, а не помечает всё подряд.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
