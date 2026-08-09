"""ЗОНД: ЦЕНА двух правок обзора приоритета ТФ — П5 (условия хеджа) и П7 (ловушка ТФ).

⚠ Это НЕ проверка гипотез. Гипотез в обзоре ноль: курс молчит по одному вопросу из десяти,
и там мы совпадаем с большинством поля, поэтому пункты 7-9 шкалы пусты. Обе правки
предрешены курсом (фаза 3, пункт 3), и порог здесь отвечает не на вопрос «вносить ли», а на
вопрос «заметно ли изменение вообще». Ответ ниже порога означает подозрение к РЕАЛИЗАЦИИ
ЗАМЕРА, а не отмену требования курса.

Пороги записаны и хешированы ДО прогона: docs/audit/tolerance-priority.md,
sha256 caf882cf2335816dc0b3dcc6ad7e4599c74167582281bf1435a151a27cab6642

ЧТО СЧИТАЕТСЯ

  П5-A  доля структур, чья сторона расходится с приоритетом старшего ТФ (порог 1%).
        Считается по ЗАКРЫТЫМ накоплениям, а не по построенным уровням: сторону задаёт
        `acc.is_long`, и профиль на неё не влияет вовсе. Знаменатель назван явно.

  П7-A  доля закрытых структур младшего ТФ, чьи границы ЦЕЛИКОМ лежат внутри зоны уровня
        какого-либо СТАРШЕГО ТФ, существовавшего на момент их появления (порог 1%).

  П7-B  из найденных в П7-A — доля тех, где событие на уровне старшего ТФ разрешилось
        ПРОКОЛОМ, а не пробоем (порог 5%). Это и есть случай стр. 46.

КОНТРОЛИ

  П5   печатаются доли всех трёх исходов; ноль хоть у одного означает, что счётчик
       различает не то, что объявляет.
  П7   ⚠ нуль обязан ЛОМАТЬ отношение: границы каждой младшей структуры сдвигаются на
       +7% и −7%, доля считается заново. Наблюдаемая обязана быть заметно выше сдвинутой;
       если доли сходятся в пределах трети, отношение — свойство плотности карты.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_priority_price_2026-08-10.py
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import archive  # noqa: E402
from hunter.accumulation import detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.breach import BreachKind, Direction, first_breach  # noqa: E402
from hunter.geometry import TF_ORDER  # noqa: E402
from hunter.levels import TV_ROWS, LevelSide, created_at_ms, structure_window_ms  # noqa: E402
from hunter.models import Bar, NotReady  # noqa: E402
from hunter.priority import Agreement, agreement, resolve  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402
from hunter.swings import trend as trend_of  # noqa: E402
from hunter.volume_profile import build_tv  # noqa: E402

SHIFTS = (0.07, -0.07)


def load_frames() -> list[tuple[str, Decimal, dict[str, list[Bar]]]]:
    out: list[tuple[str, Decimal, dict[str, list[Bar]]]] = []
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(p for p in run.iterdir() if p.is_dir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            meta = json.loads(mp.read_text(encoding="utf-8"))
            per_tf: dict[str, list[Bar]] = {}
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                per_tf[tf] = [
                    Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                        high=float(r["high"]), low=float(r["low"]),
                        close=float(r["close"]), volume=float(r["volume"]))
                    for r in pl.read_parquet(f).iter_rows(named=True)
                ]
            if per_tf:
                out.append((meta["symbol"], Decimal(meta["tick_size"]), per_tf))
    return out


def main() -> None:
    frames = load_frames()
    cache_dir = archive.CACHE_DIR
    cache_files = len(list(cache_dir.glob("*.parquet"))) if cache_dir.exists() else 0

    counts = {Agreement.BY_TREND: 0, Agreement.AGAINST_TREND: 0, Agreement.NO_PRIORITY: 0}
    structures = 0
    senior_levels = 0
    under_senior = 0
    under_senior_shift = {s: 0 for s in SHIFTS}
    trap_puncture = 0
    trap_breakout = 0
    trap_unresolved = 0
    younger_total = 0

    for symbol, tick, per_tf in frames:
        market_id = symbol.split(":")[0].replace("/", "")
        src = archive.WindowSource(symbol, market_id, tick, cache_dir=cache_dir)

        # разбор ряда один раз на ТФ — как в engine.read_series
        accs_by_tf: dict[str, list] = {}
        trends = {}
        for tf, bars in per_tf.items():
            sw = detect_swings(bars)
            if isinstance(sw, NotReady):
                continue
            trends[tf] = trend_of(sw)
            accs_by_tf[tf] = list(detect(bars, sw, tf).closed)

        # П5-A: сторона против приоритета — по закрытым накоплениям
        for tf, accs in accs_by_tf.items():
            pr = resolve(trends, tf)
            for acc in accs:
                structures += 1
                side = LevelSide.LONG if acc.is_long else LevelSide.SHORT
                counts[agreement(side, pr)] += 1

        # уровни СТАРШИХ ТФ с зонами — для П7
        seniors: list[tuple[str, float, float, float, int, bool]] = []
        for tf, accs in accs_by_tf.items():
            bars = per_tf[tf]
            for acc in accs:
                w = structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
                hist = src.window(w[0], w[1])
                if isinstance(hist, NotReady):
                    continue
                seg = bars[acc.first_index:acc.last_index + 1]
                if not seg:
                    continue
                prof = build_tv(hist,
                                bottom=Decimal(str(min(b.low for b in seg))),
                                top=Decimal(str(max(b.high for b in seg))),
                                rows=TV_ROWS)
                if isinstance(prof, NotReady):
                    continue
                senior_levels += 1
                seniors.append((tf, float(prof.val_price), float(prof.vah_price),
                                float(prof.poc_price),
                                created_at_ms(acc, bars, TIMEFRAME_MS[tf]), acc.is_long))

        # П7: младшая структура целиком внутри зоны уровня СТАРШЕГО ТФ
        for tf, accs in accs_by_tf.items():
            if tf not in TF_ORDER:
                continue
            rank = TF_ORDER.index(tf)
            bars = per_tf[tf]
            for acc in accs:
                younger_total += 1
                born = created_at_ms(acc, bars, TIMEFRAME_MS[tf])
                lo, hi = acc.lower.edge, acc.upper.edge
                mid = (lo + hi) / 2
                hit = None
                for stf, val, vah, poc, sborn, s_long in seniors:
                    if stf not in TF_ORDER or TF_ORDER.index(stf) <= rank:
                        continue
                    if sborn > born:
                        continue
                    if val <= lo and hi <= vah:
                        hit = (stf, poc, sborn, s_long)
                        break
                if hit is not None:
                    under_senior += 1
                    stf, poc, sborn, s_long = hit
                    sbars = per_tf.get(stf, [])
                    idx = next((i for i, b in enumerate(sbars)
                                if b.open_ms + TIMEFRAME_MS[stf] >= sborn), None)
                    if idx is None:
                        trap_unresolved += 1
                    else:
                        d = Direction.BELOW if s_long else Direction.ABOVE
                        ev = first_breach(sbars, poc, d, stf, from_index=idx + 1)
                        if ev is None or ev.kind in (BreachKind.OPEN, BreachKind.UNRESOLVED):
                            trap_unresolved += 1
                        elif ev.kind is BreachKind.BREAKOUT:
                            trap_breakout += 1
                        else:
                            trap_puncture += 1
                # НУЛЬ: та же проверка на сдвинутых границах
                for s in SHIFTS:
                    lo2, hi2 = lo + mid * s, hi + mid * s
                    for stf, val, vah, _poc, sborn, _s_long in seniors:
                        if stf not in TF_ORDER or TF_ORDER.index(stf) <= rank:
                            continue
                        if sborn > born:
                            continue
                        if val <= lo2 and hi2 <= vah:
                            under_senior_shift[s] += 1
                            break

    print("ОТПЕЧАТОК ДАННЫХ")
    print(f"  кадров (прогон × символ): {len(frames)}")
    print(f"  файлов суток в aggcache:  {cache_files}")
    print(f"  структур закрытых:        {structures}")
    print(f"  уровней с зоной построено: {senior_levels}")
    print()

    print("П5-A  сторона против приоритета старшего ТФ (порог заметности 1%)")
    total = sum(counts.values())
    for k in (Agreement.BY_TREND, Agreement.AGAINST_TREND, Agreement.NO_PRIORITY):
        share = 100.0 * counts[k] / total if total else 0.0
        print(f"  {k.value:15} {counts[k]:6d} = {share:5.1f}%")
    print("  ← КОНТРОЛЬ: ноль хоть у одного означает, что счётчик различает не то")
    print()

    print("П7-A  младшая структура ЦЕЛИКОМ внутри зоны уровня старшего ТФ (порог 1%)")
    if younger_total:
        obs = 100.0 * under_senior / younger_total
        print(f"  наблюдаемая доля: {under_senior} из {younger_total} = {obs:.2f}%")
        for s in SHIFTS:
            v = 100.0 * under_senior_shift[s] / younger_total
            print(f"  НУЛЬ, сдвиг {s:+.0%}: {under_senior_shift[s]} = {v:.2f}%")
        worst = max(100.0 * under_senior_shift[s] / younger_total for s in SHIFTS)
        verdict = ("отношение НЕ отличимо от плотности карты"
                   if worst >= obs * 2 / 3 else "наблюдаемая заметно выше сдвинутой")
        print(f"  ВЕРДИКТ КОНТРОЛЯ: {verdict}")
    print()

    print("П7-B  чем разрешилось событие на уровне СТАРШЕГО ТФ (порог 5%)")
    if under_senior:
        for name, v in (("прокол", trap_puncture), ("пробой", trap_breakout),
                        ("не разрешилось", trap_unresolved)):
            print(f"  {name:16} {v:5d} = {100.0 * v / under_senior:5.1f}%")
    else:
        print("  выборка пуста")


if __name__ == "__main__":
    main()
