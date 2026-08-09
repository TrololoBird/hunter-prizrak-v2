"""ЗОНД-РАЗБОР: почему утроение числа стоповых объёмов НЕ ИЗМЕНИЛО карточку ни на строку.

Правка П1 (все младшие ТФ вместо ТФ−1) замерена зондом до внесения: стоповых объёмов
91795 против 262517, прибавка 186%. После внесения дифф повтора на трёх символах —
ПУСТОЙ: 284, 427 и 362 строки совпали построчно.

⚠ Расхождение между «намерено втрое больше» и «не изменилось ничего» — это противоречие, и
CLAUDE.md требует РАЗБОРА, а не связного рассказа. Правдоподобное объяснение у меня есть
(якорь стопа берёт САМЫЙ ДАЛЬНИЙ из кандидатов, а прокол после правки 2026-08-08 есть
почти всегда и почти всегда дальше) — и ровно поэтому оно проверяется здесь.

ЧТО ПРОВЕРЯЕТСЯ. Для каждой стороны каждого уровня считается якорь стопа тремя способами:
  * как сейчас — прокол и стоповые объёмы вместе;
  * ТОЛЬКО прокол, стоповые объёмы не поданы;
  * ТОЛЬКО стоповые объёмы, прокол вычеркнут.
Если первый и второй совпадают всюду, стоповые объёмы на якорь не влияют ВООБЩЕ, и
причина пустого диффа найдена.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_stopvol_anchor_2026-08-09.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import BoundaryZone, detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.levels import LevelSide, stop_anchor  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.stop_volume import classify  # noqa: E402
from hunter.swings import SwingSet  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402


def load() -> dict[tuple[str, str], dict[str, list[Bar]]]:
    out: dict[tuple[str, str], dict[str, list[Bar]]] = {}
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(p for p in run.iterdir() if p.is_dir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
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
                out[(run.name, sym)] = per_tf
    return out


def without_puncture(z: BoundaryZone) -> BoundaryZone:
    return z.model_copy(update={"puncture": None})


def main() -> int:
    corpus = load()
    if not corpus:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    ladder = list(TIMEFRAME_MS)
    sides = 0
    same_as_puncture_only = 0
    anchor_none = 0
    sv_only_would_give = 0
    sv_changes_answer = 0

    for _key, per_tf in corpus.items():
        scans: dict[str, tuple] = {}
        for tf, bars in per_tf.items():
            sw = detect_swings(bars)
            if isinstance(sw, SwingSet):
                scans[tf] = detect(bars, sw, tf).closed
        for tf, hosts in scans.items():
            younger = [t for t in ladder[:ladder.index(tf)] if t in scans]
            for host in hosts:
                svs = []
                for y in younger:
                    svs.extend(classify(scans[y], per_tf[y], host, per_tf[tf], tf).items)
                svs_t = tuple(svs)
                for down in (True, False):
                    sides += 1
                    side = LevelSide.LONG if down else LevelSide.SHORT
                    zone = host.lower if down else host.upper
                    boundary = zone.edge
                    both = stop_anchor(side, boundary, zone, svs_t)
                    punct = stop_anchor(side, boundary, zone, ())
                    only_sv = stop_anchor(side, boundary, without_puncture(zone), svs_t)
                    if both == punct:
                        same_as_puncture_only += 1
                    else:
                        sv_changes_answer += 1
                    if both is None:
                        anchor_none += 1
                    if only_sv is not None:
                        sv_only_would_give += 1

    print("=" * 78)
    print("РАЗБОР: влияют ли стоповые объёмы на якорь стопа вообще")
    print("=" * 78)
    print(f"  сторон уровней проверено: {sides}")
    print(f"  якорь совпал с «только прокол»:      {same_as_puncture_only} "
          f"({same_as_puncture_only / max(sides, 1) * 100:.1f}%)")
    print(f"  стоповый объём ИЗМЕНИЛ ответ:        {sv_changes_answer} "
          f"({sv_changes_answer / max(sides, 1) * 100:.1f}%)")
    print(f"  якоря нет вовсе:                     {anchor_none}")
    print(f"  стоповый ДАЛ БЫ якорь без прокола:   {sv_only_would_give} "
          f"({sv_only_would_give / max(sides, 1) * 100:.1f}%)")

    print("\nВЫВОД РАЗБОРА:")
    if sv_changes_answer == 0:
        print("  ✅ ПРИЧИНА НАЙДЕНА: стоповые объёмы НЕ ВЛИЯЮТ на якорь НИ РАЗУ.")
        print("     Якорь берётся самый дальний, а прокол после правки 2026-08-08 есть")
        print("     почти всегда и почти всегда дальше. Сколько бы зон ни нашлось,")
        print("     карточка не изменится — правка П1 наблюдаема только внутри модуля.")
        if sv_only_would_give:
            print(f"     ⚠ И это НЕ «зон нет»: без прокола они дали бы якорь "
                  f"{sv_only_would_give} раз.")
    else:
        print(f"  ⚠ ОБЪЯСНЕНИЕ НЕВЕРНО: стоповые меняют ответ {sv_changes_answer} раз.")
        print("     Значит пустой дифф повтора вызван чем-то другим, и это надо искать.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
