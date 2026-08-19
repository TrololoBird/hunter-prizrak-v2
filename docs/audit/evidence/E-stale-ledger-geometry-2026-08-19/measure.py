"""Дрейф геометрии леджера: какие поля расходятся с расчётом и почему.

Воспроизведение:
    uv run python docs/audit/evidence/E-stale-ledger-geometry-2026-08-19/measure.py

ОТПЕЧАТОК ДАННЫХ на момент замера 2026-08-19 (леджер растёт — при повторе сверяйте):
    строк в levels: 49735; прогон кадров: ondo-deep; общих строк с расчётом ONDO: 1797.

Контроль встроен и обязателен для чтения результата: поля, которые `sync_map` уже
переписывает при подтверждении (`price`, `zone_lo`, `zone_hi`), обязаны дать РОВНО
НОЛЬ расхождений. Если они дадут не ноль — расходится не леджер, а прибор замера,
и остальные числа читать нельзя.
"""
import sqlite3
from collections import Counter

from hunter import engine, store
from hunter.profile_source import TVWindows

RUN, DIR = "ondo-deep", "ONDO_USDT_USDT"
FIELDS = ("side", "price", "zone_lo", "zone_hi", "boundary_lo", "boundary_hi", "volume")
REWRITTEN_BEFORE_FIX = {"price", "zone_lo", "zone_hi"}


def main() -> int:
    db = sqlite3.connect("data/ledger.sqlite3")
    total = db.execute("select count(*) from levels").fetchone()[0]
    bad = db.execute(
        "select count(*) from levels where zone_lo < boundary_lo - 1e-12"
        " or zone_hi > boundary_hi + 1e-12").fetchone()[0]
    print(f"ЛЕДЖЕР ЦЕЛИКОМ: строк {total}; зона ВНЕ структуры у {bad} ({bad / total * 100:.1f}%)")
    print("  (дефект №1 владельца — зона выходит за структуру; в карточке он закрыт 2026-08-18)")

    symbol, tick, _ = store.read_meta(RUN, DIR)
    tfs = store.saved_timeframes(RUN, DIR)
    series = {tf: store.read_bars(RUN, DIR, tf) for tf in tfs}
    profile = dict(series)
    profile.update(store.read_profile_bars(RUN, DIR))
    decision = engine.decide(symbol, series, TVWindows(symbol, tick, profile), tfs)

    rows = {(r[0], r[1], r[2]): r[3:] for r in db.execute(
        "select timeframe, from_ms, to_ms, side, price, zone_lo, zone_hi,"
        " boundary_lo, boundary_hi, volume from levels where symbol=?", (symbol,))}

    drift: Counter[str] = Counter()
    seen = 0
    for m in decision.mapped:
        lv = m.level
        row = rows.get((lv.timeframe, lv.structure_from_ms, lv.structure_to_ms))
        if row is None:
            continue
        seen += 1
        live = (lv.side.value, float(lv.price), float(lv.zone_lo), float(lv.zone_hi),
                float(lv.boundary_lo), float(lv.boundary_hi), lv.structure_volume)
        for name, stored, fresh in zip(FIELDS, row, live, strict=True):
            if isinstance(fresh, float):
                if abs(float(stored) - fresh) > 1e-9:
                    drift[name] += 1
            elif stored != fresh:
                drift[name] += 1

    print(f"\nСВЕРЕНО строк леджера против расчёта: {seen}")
    for name in FIELDS:
        mark = "  ← КОНТРОЛЬ, обязан быть 0" if name in REWRITTEN_BEFORE_FIX else ""
        print(f"  {name:>12}: {drift[name]:>5} ({drift[name] / seen * 100:5.1f}%){mark}")

    broken = [f for f in REWRITTEN_BEFORE_FIX if drift[f]]
    if broken:
        print(f"\nПЛОХО: контрольные поля разошлись ({', '.join(sorted(broken))}) — "
              f"замер недостоверен, остальные числа читать нельзя.")
        return 1
    print("\nКОНТРОЛЬ ПРОЙДЕН: переписываемые поля дали ноль — прибор различает выборки.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
