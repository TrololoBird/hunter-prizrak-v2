"""ЗОНД: четыре вопроса о свингах, оставшиеся после обзора 16 чужих проектов.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-swings.md,
sha256 1d32f3e9c4d132c29ec6fff2980cc3fe39b52613b914c1841a9fe2b3044c10fa.

Расчёт НЕ меняется. Зонд считает свойства текущего вывода `swings.detect` на сохранённых
кадрах — тех же рядах, из которых строится карточка.

  Г1  сколько баров стали бы фракталом при НЕСТРОГОМ сравнении (приём sheevv/pytrendline)
  Г2  доля свингов в сериях из двух и более подряд идущих одного вида (приём smc/talipp)
  Г3  сколько фракталов имеют соседа не через один шаг ТФ (вопрос, на который в чужих
      проектах не ответил НИКТО, а наш же breach.py время учитывает)
  Г4  сколько баров дают одновременно HIGH и LOW

КОНТРОЛИ, без которых числа недействительны:
  * Г1 — нуль: перемешанный ряд. Перед сравнением проверяется, что перемешивание ЛОМАЕТ
    свойство (число фракталов обязано измениться), иначе это не нуль.
  * Г3 — К-1: в ряд подсаживается искусственная дыра, счётчик обязан её увидеть.
  * Г4 — К-1: подсаживается бар-«волчок», счётчик обязан его увидеть.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_swing_questions_2026-08-07.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS, steps_between  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.swings import SIDE_BARS, SwingKind, detect  # noqa: E402

SEED = 20260807
"""Затравка перемешивания. Названа числом, чтобы нуль воспроизводился."""


def load() -> dict[tuple[str, str], list[Bar]]:
    out: dict[tuple[str, str], list[Bar]] = {}
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in pl.read_parquet(f).iter_rows(named=True)]
                if len(bars) > len(out.get((sym, tf), [])):
                    out[(sym, tf)] = bars
    return out


def loose_extra(bars: list[Bar]) -> tuple[int, int]:
    """(строгих фракталов, ДОПОЛНИТЕЛЬНЫХ при нестрогом сравнении)."""
    strict = loose = 0
    for i in range(SIDE_BARS, len(bars) - SIDE_BARS):
        mid = bars[i]
        left = bars[i - SIDE_BARS:i]
        right = bars[i + 1:i + 1 + SIDE_BARS]
        side = left + right
        for hi in (True, False):
            if hi:
                s = all(b.high < mid.high for b in side)
                lo_ = all(b.high <= mid.high for b in side)
            else:
                s = all(b.low > mid.low for b in side)
                lo_ = all(b.low >= mid.low for b in side)
            strict += s
            loose += lo_
    return strict, loose - strict


def runs_same_kind(kinds: list[SwingKind]) -> int:
    """Сколько свингов входит в серию из двух и более подряд идущих одного вида."""
    n = 0
    i = 0
    while i < len(kinds):
        j = i
        while j + 1 < len(kinds) and kinds[j + 1] is kinds[i]:
            j += 1
        if j > i:
            n += j - i + 1
        i = j + 1
    return n


def gapped(bars: list[Bar], tf: str, idx: list[int]) -> int:
    """Фракталы, у которых хотя бы один из четырёх соседей не через один шаг ТФ."""
    n = 0
    for i in idx:
        window = range(i - SIDE_BARS, i + SIDE_BARS)
        if any(steps_between(bars[k], bars[k + 1], tf) != 1 for k in window):
            n += 1
    return n


def main() -> int:
    data = load()
    if not data:
        print("КАДРОВ НЕТ — НЕ СМОГ ПРОВЕРИТЬ")
        return 1
    print("=" * 78)
    print("Свинги: четыре вопроса после обзора 16 чужих проектов")
    print("=" * 78)
    print(f"кадров: {len(data)} рядов, "
          f"{len({s for s, _ in data})} символов, {len({t for _, t in data})} ТФ")
    print()

    tot_strict = tot_extra = tot_runs = tot_sw = tot_gap = tot_both = 0
    null_strict = null_extra = 0
    null_changed = 0
    by_tf: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])

    for (sym, tf), bars in sorted(data.items()):
        sw = detect(bars)
        if not hasattr(sw, "swings"):
            continue
        strict, extra = loose_extra(bars)
        kinds = [s.kind for s in sw.swings]
        idx_h = {s.index for s in sw.swings if s.kind is SwingKind.HIGH}
        idx_l = {s.index for s in sw.swings if s.kind is SwingKind.LOW}
        both = len(idx_h & idx_l)
        g = gapped(bars, tf, sorted({s.index for s in sw.swings}))
        r = runs_same_kind(kinds)

        tot_strict += strict
        tot_extra += extra
        tot_sw += len(sw.swings)
        tot_runs += r
        tot_gap += g
        tot_both += both
        row = by_tf[tf]
        row[0] += len(sw.swings)
        row[1] += extra
        row[2] += r
        row[3] += g

        # нуль для Г1: перемешанный ряд
        rnd = random.Random(SEED + hash((sym, tf)) % 1000)
        mixed = bars[:]
        rnd.shuffle(mixed)
        ns, ne = loose_extra(mixed)
        null_strict += ns
        null_extra += ne
        if ns != strict:
            null_changed += 1

    print("--- Г1. Слипание почти равных экстремумов (приём sheevv / pytrendline) ---")
    share = tot_extra / tot_strict * 100 if tot_strict else 0.0
    print(f"  фракталов строгим сравнением: {tot_strict}")
    print(f"  ДОБАВИЛОСЬ бы при нестрогом:  {tot_extra}  ({share:.2f}%)")
    print(f"  порог из файла допуска: <2% закрыть, 2-10% записать, >=10% проверять")
    verdict = ("ЗАКРЫТЬ" if share < 2 else "ЗАПИСАТЬ" if share < 10 else "ПРОВЕРЯТЬ")
    print(f"  ВЕРДИКТ ПО ПОРОГУ: {verdict}")
    print()
    print("  НУЛЬ (перемешанный ряд):")
    print(f"    ломает ли перемешивание свойство: число фракталов изменилось "
          f"у {null_changed} из {len(data)} рядов")
    if null_changed < len(data):
        print("    ⚠ не у всех рядов — на этих перемешивание НЕ нуль")
    nshare = null_extra / null_strict * 100 if null_strict else 0.0
    print(f"    доля добавки на перемешанном: {nshare:.2f}%  "
          f"(на настоящем {share:.2f}%)")
    print(f"    {'✗ НУЛЬ НЕ ХУЖЕ — величина мерит гранулярность цены' if nshare >= share else '✓ на настоящем ряду больше'}")
    print()

    print("--- Г2. Серии подряд идущих свингов одного вида (приём smc / talipp) ---")
    rshare = tot_runs / tot_sw * 100 if tot_sw else 0.0
    print(f"  свингов всего: {tot_sw}; в сериях из 2+ одного вида: {tot_runs} ({rshare:.1f}%)")
    print(f"  порог: <5% закрыть, >=20% проверять")
    print(f"  ВЕРДИКТ ПО ПОРОГУ: "
          f"{'ЗАКРЫТЬ' if rshare < 5 else 'ПРОВЕРЯТЬ' if rshare >= 20 else 'РЕШЕНИЕ ВЛАДЕЛЬЦА'}")
    print()

    print("--- Г3. Дыра в ряду (вопрос, на который не ответил никто из 16) ---")
    print(f"  фракталов с соседом не через шаг ТФ: {tot_gap} из {tot_sw}")
    print(f"  ВЕРДИКТ ПО ПОРОГУ: {'ЗАКРЫТЬ — ряды сплошные' if tot_gap == 0 else 'РАСХОЖДЕНИЕ РЕАЛЬНО'}")
    print()

    print("--- Г4. Хай и лой на одном баре ---")
    print(f"  таких баров: {tot_both}")
    print(f"  ВЕРДИКТ ПО ПОРОГУ: {'записать причину и закрыть' if tot_both == 0 else 'назвать поведение явно'}")
    print()

    print("--- Разбивка по ТФ (сводка вдоль измерения, где возможен перекос) ---")
    print(f"  {'ТФ':<5} {'свингов':>8} {'добавка Г1':>11} {'серии Г2':>9} {'дыры Г3':>8}")
    for tf in sorted(by_tf, key=lambda t: TIMEFRAME_MS[t]):
        a, b, c, d = by_tf[tf]
        print(f"  {tf:<5} {a:>8} {b:>11} {c:>9} {d:>8}")
    print()

    print("=" * 78)
    print("КОНТРОЛЬ К-1: способны ли счётчики Г3 и Г4 дать НЕ-НОЛЬ?")
    # Шаг 5м: §2.8 задаёт список ТФ, и '1m' в нём нет — первая редакция контроля падала
    # на этом. Падение поймало то, ради чего контроль и заводился: ноль по Г3 без него
    # не доказан, потому что неизвестно, способен ли счётчик дать не-ноль.
    STEP = 300_000
    probe = [Bar(open_ms=i * STEP, open=100.0 + i, high=101.0 + i,
                 low=99.0 + i, close=100.5 + i, volume=1.0) for i in range(9)]
    # Г4: бар-«волчок» посередине — и выше всех соседей, и ниже всех
    probe[4] = Bar(open_ms=4 * STEP, open=104.0, high=200.0, low=1.0,
                   close=104.5, volume=1.0)
    sw = detect(probe)
    assert hasattr(sw, "swings")
    h = {s.index for s in sw.swings if s.kind is SwingKind.HIGH}
    lo = {s.index for s in sw.swings if s.kind is SwingKind.LOW}
    print(f"  Г4 на подсаженном волчке: совпавших индексов {len(h & lo)} "
          f"{'✓ счётчик видит' if h & lo else '✗ СЧЁТЧИК СЛЕП'}")
    # Г3: та же серия, но с дырой — бар 6 сдвинут на час вперёд
    holed = probe[:]
    holed[6] = Bar(open_ms=6 * STEP + 3_600_000, open=106.0, high=107.0,
                   low=105.0, close=106.5, volume=1.0)
    sw2 = detect(holed)
    assert hasattr(sw2, "swings")
    g = gapped(holed, "5m", sorted({s.index for s in sw2.swings}))
    print(f"  Г3 на подсаженной дыре: фракталов с дырой {g} "
          f"{'✓ счётчик видит' if g else '✗ СЧЁТЧИК СЛЕП'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
