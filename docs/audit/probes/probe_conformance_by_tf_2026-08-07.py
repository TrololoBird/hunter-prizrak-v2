"""ЗОНД R-06: метрика №4 допуска R-03 — С КАКОГО ТФ приходит совпадение с разметкой.

Отдельный файл, а НЕ правка `probe_author_conformance_2026-08-07.py`, потому что
`pyproject.toml` говорит прямо: «отредактированный зонд перестаёт быть тем, что дало
опубликованное число». Числа R-03/R-05 остаются за тем зондом; здесь только разбивка,
которую файл допуска обещал («разбивка по символам и по ТФ уровня проекта»), но которую
тот зонд напечатал лишь для ПОСТРОЕННЫХ уровней, а не для совпавших.

Вопрос, на который зонд отвечает ровно один: контроль К-2а показал, что решётка
ПРОИЗВОЛЬНЫХ равноотстоящих цен накрывается уровнями проекта не хуже настоящей разметки.
Гипотеза объяснения — ПЛОТНОСТЬ: уровней на младших ТФ столько, что попадёт что угодно.
Гипотеза проверяема: если так, то каждый ТФ накрывает решётку и разметку ОДИНАКОВО, и ни
один ТФ не отличает настоящее от произвольного.

⚠ Опровержимость. Зонд способен дать и обратный ответ: если какой-то ТФ накрывает
разметку заметно лучше решётки, объяснение «одна плотность» неверно, и в этом ТФ есть
согласие с автором. Ответ «все ТФ ровно одинаковы» не встроен.

Допуск, отсечка и разметка НЕ трогаются — берутся импортом из зонда R-03, чтобы
разойтись было нельзя.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_conformance_by_tf_2026-08-07.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location(
    "probe_r03", HERE.parent / "probe_author_conformance_2026-08-07.py"
)
assert _spec is not None and _spec.loader is not None
r03 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r03)  # main() под __name__-guard, здесь не исполняется

from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.engine import decide  # noqa: E402
from hunter.models import Bar  # noqa: E402


def nearest_tf(targets: list[float], pairs: list[tuple[float, str]], tol: float) -> dict[str, int]:
    """С какого ТФ пришёл уровень, накрывший цель. Не накрытые цели не считаются нигде."""
    out: dict[str, int] = defaultdict(int)
    for t in targets:
        near = [(abs(t - p), tf) for p, tf in pairs if abs(t - p) <= tol]
        if near:
            out[min(near)[1]] += 1
    return dict(out)


def covered_by_tf(targets: list[float], pairs: list[tuple[float, str]], tol: float) -> dict[str, int]:
    """Сколько целей накрыл КАЖДЫЙ ТФ сам по себе — без спора за ближайший.

    Так честнее для сравнения «разметка против решётки»: если считать только ближайший,
    ТФ с плотной сеткой отбирает попадания у остальных, и разбивка мерила бы плотность
    второй раз.
    """
    out: dict[str, int] = defaultdict(int)
    for tf in sorted({t for _, t in pairs}):
        px = [p for p, t in pairs if t == tf]
        out[tf] = sum(1 for t in targets if any(abs(t - p) <= tol for p in px))
    return dict(out)


def main() -> int:
    print("=" * 80)
    print("R-06  Разбивка конформанса по ТФ уровня проекта (метрика №4 допуска R-03)")
    print("=" * 80)
    print(f"отсечка и допуск взяты из зонда R-03: {r03.CUTOFF_MS}, "
          f"{r03.TOLERANCE_ATR} · ATR14(4ч)")
    print("разметка автора снята с графика 4ч — см. research/author_markup/")
    print()

    author = r03.parse_markup()
    frames = ROOT / "data" / "frames"
    series_all: dict[str, dict[str, list[Bar]]] = defaultdict(dict)
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
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in df.iter_rows(named=True)]
                if len(bars) > len(series_all[sym].get(tf, [])):
                    series_all[sym][tf] = bars

    agg_real: dict[str, int] = defaultdict(int)
    agg_grid: dict[str, int] = defaultdict(int)
    agg_built: dict[str, int] = defaultdict(int)
    agg_alive: dict[str, int] = defaultdict(int)
    n_targets = 0
    checked = 0

    for sym, prices in sorted(author.items()):
        tfs = series_all.get(sym)
        if not tfs:
            print(f"{sym}: кадров нет — НЕ СМОГ ПРОВЕРИТЬ")
            continue
        cut = {tf: [b for b in bars if b.open_ms + TIMEFRAME_MS[tf] <= r03.CUTOFF_MS]
               for tf, bars in tfs.items()}
        cut = {tf: b for tf, b in cut.items() if len(b) >= 20}
        loaded = r03.load_cache(sym.split("/")[0] + "USDT")
        trades = None if loaded is None else r03.CacheWindows(sym, loaded[0], loaded[1])
        d = decide(sym, cut, trades, tuple(cut))
        a = r03.atr14(cut.get("4h", []))
        if a is None or a <= 0:
            print(f"{sym}: ATR14(4ч) не посчитан — НЕ СМОГ ПРОВЕРИТЬ")
            continue
        tol = r03.TOLERANCE_ATR * a

        pairs = [(float(m.level.price), m.level.timeframe) for m in d.mapped]
        pairs_alive = [(float(m.level.price), m.level.timeframe)
                       for m in d.mapped if m.alive_at(r03.CUTOFF_MS)]
        lo, hi = min(prices), max(prices)
        grid = [lo + (hi - lo) * i / (len(prices) - 1) for i in range(len(prices))]

        real_tf = covered_by_tf(prices, pairs, tol)
        grid_tf = covered_by_tf(grid, pairs, tol)
        near_tf = nearest_tf(prices, pairs, tol)
        built_tf: dict[str, int] = defaultdict(int)
        alive_tf: dict[str, int] = defaultdict(int)
        for p, tf in pairs:
            built_tf[tf] += 1
        for p, tf in pairs_alive:
            alive_tf[tf] += 1

        print(f"--- {sym} ---  допуск {tol:.2f}; целей автора {len(prices)}")
        print(f"  {'ТФ':<5} {'уровней':>8} {'живых':>7} "
              f"{'накрыл разметку':>16} {'накрыл РЕШЁТКУ':>16}   вердикт по ТФ")
        for tf in sorted(built_tf, key=lambda t: TIMEFRAME_MS[t]):
            rr, gg = real_tf.get(tf, 0), grid_tf.get(tf, 0)
            v = "= не отличает" if rr == gg else ("✓ разметку лучше" if rr > gg
                                                 else "✗ РЕШЁТКУ лучше")
            print(f"  {tf:<5} {built_tf[tf]:>8} {alive_tf.get(tf, 0):>7} "
                  f"{rr:>10} из {len(prices):<2} {gg:>10} из {len(grid):<2}   {v}")
        print(f"  ближайший уровень к цели пришёл с ТФ: {dict(sorted(near_tf.items()))}")
        print()

        for tf in built_tf:
            agg_real[tf] += real_tf.get(tf, 0)
            agg_grid[tf] += grid_tf.get(tf, 0)
            agg_built[tf] += built_tf[tf]
            agg_alive[tf] += alive_tf.get(tf, 0)
        n_targets += len(prices)
        checked += 1

    if not checked:
        print("ЗАМЕР НЕ СОСТОЯЛСЯ")
        return 1

    print("=" * 80)
    print(f"ИТОГО по двум символам, целей автора {n_targets}")
    print(f"  {'ТФ':<5} {'уровней':>8} {'живых':>7} {'разметка':>9} {'решётка':>9}   вердикт")
    same = 0
    better = 0
    for tf in sorted(agg_built, key=lambda t: TIMEFRAME_MS[t]):
        rr, gg = agg_real[tf], agg_grid[tf]
        v = "= не отличает" if rr == gg else ("✓ разметку лучше" if rr > gg
                                             else "✗ РЕШЁТКУ лучше")
        if rr == gg:
            same += 1
        elif rr > gg:
            better += 1
        print(f"  {tf:<5} {agg_built[tf]:>8} {agg_alive[tf]:>7} "
              f"{rr:>6} из {n_targets:<2} {gg:>6} из {n_targets:<2}   {v}")
    print()
    print("КОНТРОЛЬ: способен ли зонд ответить иначе?")
    print(f"   ТФ, различающих разметку и решётку: {better} из {len(agg_built)}; "
          f"не различающих: {same}")
    if better == 0:
        print("   ⚠ НИ ОДИН ТФ не отличает настоящую разметку от произвольной решётки —")
        print("     совпадение объясняется плотностью уровней, а не согласием с автором.")
    else:
        print("   ⚠ Ответ «все одинаковы» НЕ получен: часть ТФ разметку различает.")
        print("     Значит объяснение «одна лишь плотность» неполно — смотреть по строкам.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
