"""ЗОНД №2 для Н4: мера в НАКОПЛЕНИЯХ, а не в рядах.

Допуск зафиксирован ДО прогона: docs/audit/tolerance-accumulation-alt.md,
sha256 4ce256bcd292afb15b532331698cb07ab4d759012425f6ad9211fc92c2397a6d.

⚠ Как и у первого допуска, первая редакция не несла команды воспроизведения (гейт
`repro_commands`, §7.3), и хеш был снят с неполного файла (`8d53b4ac…`). Команда дописана,
файл пере-хеширован, замер перегнан заново. Пороги не менялись.

Зачем отдельный файл. Первый зонд (probe_accumulation_questions_2026-08-07.py) уже дал
опубликованное число; править его нельзя — отредактированный зонд перестаёт быть тем, что
это число дало (pyproject.toml, соглашение о заморозке зондов). Поэтому машина берётся
импортом, а новая мера считается здесь.

Первая мера — «сколько РЯДОВ изменилось» — дала 18 из 18 и у чередования, и у нуля при всех
пяти затравках. Это потолок: вердикт по ней был бы свойством прибора. Здесь единица меры —
одно НАКОПЛЕНИЕ, и контроль насыщения печатается вместе с результатом.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_accumulation_alt_fine_2026-08-07.py
"""

from __future__ import annotations

import importlib.util
import random
import statistics
import sys
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _load_first_probe() -> ModuleType:
    """Первый зонд берётся ИМПОРТОМ, а не копированием: копия разошлась бы молча."""
    path = HERE / "probe_accumulation_questions_2026-08-07.py"
    spec = importlib.util.spec_from_file_location("probe_acc_q", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"не читается {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = _load_first_probe()

from hunter.accumulation import AccumulationScan  # noqa: E402
from hunter.swings import SwingSet  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402

SATURATED_PCT = 95.0
"""Порог насыщения из допуска: выше него вердикт не выносится."""


def marks(scan: AccumulationScan) -> set[str]:
    """Накопления как множество сравнимых строк. Порядок не участвует — сравниваются
    сами структуры, а не их последовательность."""
    return {
        f"{a.timeframe}|{a.first_index}|{a.last_index}"
        f"|{a.upper.lo:.10g}|{a.upper.hi:.10g}|{a.lower.lo:.10g}|{a.lower.hi:.10g}"
        f"|{a.upper.point_indices}|{a.lower.point_indices}"
        f"|{a.exit.direction.value}|{a.exit.first_body_index}|{a.exit.confirmed_at_index}"
        for a in scan.closed
    }


def main() -> int:
    corpus = P.load()
    if not corpus:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    prepared: list[tuple[str, str, list, SwingSet]] = []
    for (sym, tf), bars in sorted(corpus.items()):
        sw = detect_swings(bars)
        if isinstance(sw, SwingSet):
            prepared.append((sym, tf, bars, sw))

    base: dict[tuple[str, str], set[str]] = {}
    offered: dict[tuple[str, str], int] = {}
    for sym, tf, bars, sw in prepared:
        scan, off, _ = P.detect_variant(bars, sw, tf)
        base[(sym, tf)] = marks(scan)
        offered[(sym, tf)] = off
    total_base = sum(len(v) for v in base.values())

    print("=" * 78)
    print(f"КОРПУС: рядов {len(prepared)}, закрытых накоплений в базовой разметке {total_base}")
    print("=" * 78)

    alt_diff = 0
    refused: dict[tuple[str, str], int] = {}
    for sym, tf, bars, sw in prepared:
        scan, _off, ref = P.detect_variant(bars, sw, tf, gate=P.alternating)
        refused[(sym, tf)] = ref
        alt_diff += len(marks(scan) ^ base[(sym, tf)])

    print(f"\nЧЕРЕДОВАНИЕ: различий {alt_diff} накоплений "
          f"({alt_diff / max(total_base, 1) * 100:.1f}% от базовых)")

    null_diffs: list[int] = []
    for seed in P.NULL_SEEDS:
        diff = 0
        for sym, tf, bars, sw in prepared:
            k, n = refused[(sym, tf)], offered[(sym, tf)]
            if k == 0 or n == 0:
                continue
            rng = random.Random(P.row_seed(seed, sym, tf))
            drop = set(rng.sample(range(n), min(k, n)))
            scan, _o, _r = P.detect_variant(
                bars, sw, tf, gate=lambda o, _k, _l, d=drop: o not in d
            )
            diff += len(marks(scan) ^ base[(sym, tf)])
        null_diffs.append(diff)
        print(f"  нуль, затравка {seed}: различий {diff} "
              f"({diff / max(total_base, 1) * 100:.1f}%)")

    null_median = statistics.median(null_diffs)
    alt_pct = alt_diff / max(total_base, 1) * 100
    null_pct = null_median / max(total_base, 1) * 100

    print(f"\nмедиана нуля: {null_median}, разброс {min(null_diffs)}…{max(null_diffs)}")
    print(f"КОНТРОЛЬ НАСЫЩЕНИЯ (порог {SATURATED_PCT}%): "
          f"чередование {alt_pct:.1f}%, нуль {null_pct:.1f}%")
    if alt_pct > SATURATED_PCT and null_pct > SATURATED_PCT:
        print("⚠ МЕРА НАСЫЩЕНА: обе стороны у потолка, вердикт не выносится.")
        return 0

    need = 1.25 * null_median
    print(f"ПОРОГ (записан ДО прогона): принять при {alt_diff} >= 1.25 × {null_median} "
          f"= {need:.2f}")
    print(f"ВЕРДИКТ Н4: {'ПРИНЯТА' if alt_diff >= need else 'ОТВЕРГНУТА'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
