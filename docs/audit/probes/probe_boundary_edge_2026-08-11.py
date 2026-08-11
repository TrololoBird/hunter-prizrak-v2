"""ЗОНД: какой край пары первых точек — граница базы, и где тогда оказывается ПОК.

Повод — вопрос владельца 2026-08-11: как ПОК может лежать ВНЕ базы, если базу мы
определяем первой. Замер по карте показал, что так у 21.6% уровней. Это разбор ПРИЧИНЫ,
а не симптома.

Курс, стр. 18, дословно: «Границы базы (хай и лой) чаще всего определяются первыми
2-мя точками. Но если на 3++ точках были проколы за границы - стоп всегда ставится за
этот прокол, т.к. это может быть расширением базы и в будущем цена может ходить уже в
этом расширенном диапазоне».

Двумя точками — но КАКОЙ их ценой? Курс не уточняет словами, зато рисует: на стр. 13
верхняя линия коробки проходит ПО ВЕРШИНАМ повторяющихся точек 1, 3, 5, 9, а точки 7 и
11 стоят заметно выше линии и в коробку не входят — это проколы.

Наш детектор берёт ВНУТРЕННИЙ край пары (`up_edge = min(hi_px[:2])`, решение
2026-08-08). Тогда вторая точка пары автоматически оказывается «проколом» — и база
занижается на разницу между точками ещё до всякого рынка.

Зонд сравнивает ТРИ конвенции границы на одних и тех же структурах:
  * ВНУТРЕННИЙ край пары — как сейчас;
  * ВНЕШНИЙ край пары — как нарисовано на стр. 13;
  * крайние цены баров (ХАЙ/ЛОЙ структуры) — самая широкая.

Мера одна и та же: доля структур, где ПОК профиля лежит ВНУТРИ границы. Курс требует,
чтобы ПОК был уровнем базы (стр. 21), значит верная конвенция обязана давать долю,
близкую к единице.

⚠ КОНТРОЛЬ. Прибор обязан отвечать по-разному на три конвенции — иначе он их не
различает и число ничего не значит. Плюс контроль на заведомо неверной конвенции:
граница, СУЖЕННАЯ вдвое от внутреннего края, обязана дать долю ХУЖЕ всех.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_boundary_edge_2026-08-11.py
"""

from __future__ import annotations

import re
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
from hunter.volume_profile import TV_ROWS, build_tv  # noqa: E402

CACHE = ROOT / "data" / "aggcache"
NAME_RE = re.compile(r"^([A-Z0-9]+)-(\d{4}-\d{2}-\d{2})-300000-t([0-9p]+)-b2\.parquet$")
BUCKET_MS = 300_000
TF = "1h"
MIN_POINTS = 3


def cached() -> dict[tuple[str, Decimal], list[Path]]:
    out: dict[tuple[str, Decimal], list[Path]] = defaultdict(list)
    for p in sorted(CACHE.glob("*.parquet")):
        m = NAME_RE.match(p.name)
        if m:
            out[(m[1], Decimal(m[3].replace("p", ".")))].append(p)
    return out


def bars_from(frames: list[pl.DataFrame], tick: Decimal, tf: str) -> list[Bar]:
    step = TIMEFRAME_MS[tf]
    agg: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for f in frames:
        for row in f.iter_rows(named=True):
            price = float(int(row["bin"]) * float(tick))
            agg[int(row["bucket"]) // step * step].append(
                (int(row["bucket"]), price, float(row["qty"])))
    out: list[Bar] = []
    for open_ms in sorted(agg):
        items = sorted(agg[open_ms])
        prices = [p for _, p, _ in items]
        out.append(Bar(open_ms=open_ms, open=prices[0], high=max(prices),
                       low=min(prices), close=prices[-1],
                       volume=sum(q for _, _, q in items)))
    return out


def hist_for(frames: list[pl.DataFrame], tick: Decimal, sym: str,
             from_ms: int, to_ms: int) -> TradeHistogram | None:
    h = TradeHistogram(symbol=sym, tick_size=tick)
    for f in frames:
        part = f.filter((pl.col("bucket") >= from_ms) & (pl.col("bucket") < to_ms))
        for row in part.iter_rows(named=True):
            idx = int(row["bin"])
            h.qty_by_bin[idx] = h.qty_by_bin.get(idx, 0.0) + float(row["qty"])
            h.count_by_bin[idx] = h.count_by_bin.get(idx, 0) + int(row["n"])
    if not h.qty_by_bin:
        return None
    h.qty_seen = sum(h.qty_by_bin.values())
    h.trades_seen = sum(h.count_by_bin.values())
    return h


def main() -> int:
    files = cached()
    if not files:
        print("ОТКАЗ: кэш сделок пуст — замерять нечего")
        return 2
    print(f"ОТПЕЧАТОК ДАННЫХ: символов {len(files)}, "
          f"файлов суток {sum(len(v) for v in files.values())}, ТФ {TF}")

    names = ("внутренний край пары (как сейчас)", "ВНЕШНИЙ край пары (стр. 13)",
             "ХАЙ/ЛОЙ структуры (все свечи)", "контроль: вдвое уже внутреннего")
    inside = [0, 0, 0, 0]
    total = 0

    for (market, tick), paths in sorted(files.items()):
        frames = [pl.read_parquet(p) for p in paths]
        bars = bars_from(frames, tick, TF)
        if len(bars) < 40:
            continue
        sw = detect_swings(bars)
        if isinstance(sw, NotReady):
            continue
        scan = detect_accumulations(bars, sw, TF, min_points=MIN_POINTS)
        if isinstance(scan, NotReady):
            continue
        for acc in scan.closed:
            span = bars[acc.first_index: acc.last_index + 1]
            if not span:
                continue
            from_ms, to_ms = structure_window_ms(acc, bars, TIMEFRAME_MS[TF])
            h = hist_for(frames, tick, market, from_ms, to_ms)
            if h is None:
                continue
            lo_bar = Decimal(str(min(b.low for b in span)))
            hi_bar = Decimal(str(max(b.high for b in span)))
            prof = build_tv(h, bottom=lo_bar, top=hi_bar, rows=TV_ROWS)
            if isinstance(prof, NotReady):
                continue
            poc = prof.poc_price
            total += 1

            # Четыре конвенции границы. `edge` — как сейчас (внутренний край пары,
            # возможно сдвинутый внутрь сужением); `lo`/`hi` — сама пара точек.
            up_in, lo_in = Decimal(str(acc.upper.edge)), Decimal(str(acc.lower.edge))
            up_out = Decimal(str(acc.upper.hi))
            lo_out = Decimal(str(acc.lower.lo))
            mid = (up_in + lo_in) / 2
            up_half = mid + (up_in - mid) / 2
            lo_half = mid - (mid - lo_in) / 2

            for i, (lo, hi) in enumerate((
                (lo_in, up_in), (lo_out, up_out), (lo_bar, hi_bar),
                (lo_half, up_half),
            )):
                if lo <= poc <= hi:
                    inside[i] += 1

    if not total:
        print("ОТКАЗ: ни одной структуры с покрытым окном — замер не состоялся")
        return 2

    print(f"\nструктур замерено: {total} (ТФ {TF}, порог точек {MIN_POINTS})")
    print("\nДОЛЯ СТРУКТУР, ГДЕ ПОК ЛЕЖИТ ВНУТРИ ГРАНИЦЫ:")
    for name, n in zip(names, inside, strict=True):
        print(f"   {name:38} {n:5} из {total} = {n / total * 100:5.1f}%")

    print("\nКОНТРОЛЬ 1 (прибор различает конвенции): доли "
          f"{[f'{n / total * 100:.1f}%' for n in inside]}")
    if len(set(inside)) == 1:
        print("  ⚠ ПРОВАЛ: все конвенции дают одно — прибор их не различает")
    print("КОНТРОЛЬ 2 (заведомо неверная конвенция хуже всех): суженная вдвое даёт "
          f"{inside[3] / total * 100:.1f}%")
    if inside[3] >= max(inside[:3]):
        print("  ⚠ ПРОВАЛ: заведомо неверная граница не хуже правильных — мера слепа")
    return 0


if __name__ == "__main__":
    sys.exit(main())
