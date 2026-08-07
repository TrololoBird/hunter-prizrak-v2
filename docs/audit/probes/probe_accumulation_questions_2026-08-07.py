"""ЗОНД: вопросы, оставшиеся после обзора 14 чужих реализаций накопления.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-accumulation.md,
sha256 00aa4f5949a9b1b43f6d93cf7e5df1617cd7c6a37a9385bb3ced9f69e8815a63.

⚠ Первая редакция допуска не несла команды воспроизведения — это поймал гейт `repro_commands`
(§7.3), и хеш был снят с НЕПОЛНОГО файла (`59eb5ca6…`). Порядок восстановлен целиком:
команда дописана, файл пере-хеширован, замер перегнан заново против нового хеша. Ни один
порог при этом не менялся; числа совпали до последней цифры, потому что зонд детерминирован
(затравки названы константами).

Боевой расчёт НЕ меняется. Зонд держит СВОЮ копию `accumulation.detect` с одним
дополнительным переключателем и обязан доказать, что копия верна: с выключенным
переключателем она даёт побитово тот же результат, что боевая функция.

  Н4  чередование точек границы — курс задаёт РИСУНКОМ (стр. 18), мы не требуем
  Н5  жив ли прокол за границу (счёт исходов, К-1)
  Н8  распределение ширины накоплений против порога 10% из двух чужих проектов
  Н9  распределение длительности против порогов 5 и 140 баров из четырёх чужих проектов

КОНТРОЛИ, без которых числа недействительны:
  * копия против боевой функции на всём корпусе — иначе Н4 меряет не то;
  * нуль для Н4: случайное прореживание ТОГО ЖЕ ЧИСЛА точек. Нуль обязан ломать
    свойство — он выбрасывает точки, не глядя на сторону, чередования не создаёт;
  * распределения Н8 и Н9 печатают min/медиану/max: константа означала бы, что зонд
    считает не то, что назвал.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_accumulation_questions_2026-08-07.py
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import (  # noqa: E402
    MIN_BOUNDARY_POINTS,
    Accumulation,
    AccumulationScan,
    BoundaryZone,
    OpenStructure,
    StructureExit,
    detect,
)
from hunter.bars import TIMEFRAME_MS, steps_between  # noqa: E402
from hunter.breach import CONFIRM_BODIES, Direction, body_beyond  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.swings import SwingKind, SwingSet  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402

SEED = 20260807
NULL_SEEDS = (1, 2, 3, 4, 5)


def row_seed(seed: int, sym: str, tf: str) -> int:
    """Затравка ряда: устойчивый дайджест, а НЕ встроенный `hash`.

    ⚠ Первая редакция брала `hash((sym, tf))`. Хеш строк в Python солится на каждый
    процесс (`PYTHONHASHSEED`), поэтому нуль получался разным от запуска к запуску: два
    прогона подряд дали медианы 481 и 479. Число, которое нельзя повторить, §7.3 не
    фиксирует. Поймано перегоном — тем самым, который затевался ради хеша допуска.
    """
    digest = hashlib.sha256(f"{sym}|{tf}".encode()).digest()
    return SEED + seed * 1000 + int.from_bytes(digest[:4], "big") % 997

WIDTH_LIMIT_PCT = 10.0
"""Порог ширины у Screeni-py (`percentage=10`) и TradingPatternScanner
(`channel_range = 0.1`) — два независимых проекта назвали одно число."""

SHORT_BARS = 5
"""Нижний предел длительности у трёх реализаций Дарваса и Consolidation Zones."""

LONG_BARS = 140
"""`BoxLength=140` у BreakOutBox — единственный найденный ВЕРХНИЙ ориентир."""

Gate = Callable[[int, SwingKind, SwingKind | None], bool]


def detect_variant(
    bars: list[Bar],
    swings: SwingSet,
    timeframe: str,
    *,
    gate: Gate | None = None,
    min_points: int = MIN_BOUNDARY_POINTS,
    confirm_bodies: int = CONFIRM_BODIES,
) -> tuple[AccumulationScan, int, int]:
    """Копия `accumulation.detect` с воротами на приёме точки границы.

    `gate(ordinal, kind, last_kind) -> bool` решает, принять ли точку, которую боевая
    функция приняла бы. `gate=None` — поведение боевой функции без изменений.

    Возвращает (разметка, сколько точек предъявлено воротам, сколько отвергнуто).
    """
    by_confirm: dict[int, list[tuple[SwingKind, int, float]]] = {}
    for s in swings.swings:
        by_confirm.setdefault(s.confirmed_at_index, []).append((s.kind, s.index, s.price))

    found: list[Accumulation] = []
    hi_px: list[float] = []
    hi_idx: list[int] = []
    lo_px: list[float] = []
    lo_idx: list[int] = []
    hi_punct: float | None = None
    lo_punct: float | None = None
    run_dir: Direction | None = None
    run_from = 0
    resets = 0
    offered = 0
    refused = 0
    last_kind: SwingKind | None = None

    def reset() -> None:
        nonlocal run_dir, run_from, hi_punct, lo_punct, resets, last_kind
        hi_px.clear()
        hi_idx.clear()
        lo_px.clear()
        lo_idx.clear()
        hi_punct = lo_punct = None
        run_dir, run_from = None, 0
        resets += 1
        last_kind = None

    def upper_zone() -> tuple[float, float]:
        return min(hi_px[:2]), max(hi_px[:2])

    def lower_zone() -> tuple[float, float]:
        return min(lo_px[:2]), max(lo_px[:2])

    def allowed(kind: SwingKind) -> bool:
        """Ворота. Порядковый номер считается ТОЛЬКО по предъявленным точкам."""
        nonlocal offered, refused, last_kind
        ordinal = offered
        offered += 1
        if gate is not None and not gate(ordinal, kind, last_kind):
            refused += 1
            return False
        last_kind = kind
        return True

    for k in range(len(bars)):
        for kind, index, price in by_confirm.get(k, []):
            if kind is SwingKind.HIGH:
                if len(hi_px) < 2:
                    if allowed(kind):
                        hi_px.append(price)
                        hi_idx.append(index)
                    continue
                zlo, zhi = upper_zone()
                if price < zlo:
                    continue  # локальный хай внутри базы — не точка границы
                if not allowed(kind):
                    continue
                hi_px.append(price)
                hi_idx.append(index)
                if price > zhi:
                    hi_punct = price if hi_punct is None else max(hi_punct, price)
            else:
                if len(lo_px) < 2:
                    if allowed(kind):
                        lo_px.append(price)
                        lo_idx.append(index)
                    continue
                zlo, zhi = lower_zone()
                if price > zhi:
                    continue
                if not allowed(kind):
                    continue
                lo_px.append(price)
                lo_idx.append(index)
                if price < zlo:
                    lo_punct = price if lo_punct is None else min(lo_punct, price)

        if len(hi_px) < 2 or len(lo_px) < 2:
            continue

        upper_lo, upper_hi = upper_zone()
        lower_lo, lower_hi = lower_zone()
        if upper_lo <= lower_hi:
            reset()
            continue

        bar = bars[k]
        if body_beyond(bar, upper_hi, Direction.ABOVE):
            direction = Direction.ABOVE
        elif body_beyond(bar, lower_lo, Direction.BELOW):
            direction = Direction.BELOW
        else:
            run_dir = None
            continue

        broken = k > 0 and steps_between(bars[k - 1], bars[k], timeframe) != 1
        if direction is not run_dir or broken:
            run_dir, run_from = direction, k
        bodies = k - run_from + 1
        if bodies < confirm_bodies:
            continue
        if len(hi_px) + len(lo_px) < min_points:
            reset()
            continue

        found.append(
            Accumulation(
                timeframe=timeframe,
                first_index=min(hi_idx + lo_idx),
                last_index=k,
                upper=BoundaryZone(lo=upper_lo, hi=upper_hi,
                                   point_indices=tuple(hi_idx), puncture=hi_punct),
                lower=BoundaryZone(lo=lower_lo, hi=lower_hi,
                                   point_indices=tuple(lo_idx), puncture=lo_punct),
                exit=StructureExit(
                    direction=direction, first_body_index=run_from, confirmed_at_index=k
                ),
            )
        )
        reset()
        resets -= 1

    tail: OpenStructure | None = None
    if len(hi_px) >= 2 and len(lo_px) >= 2:
        ulo, uhi = upper_zone()
        llo, lhi = lower_zone()
        first = min(hi_idx + lo_idx)
        tail = OpenStructure(
            first_index=first,
            bars_open=len(bars) - first,
            upper=BoundaryZone(lo=ulo, hi=uhi, point_indices=tuple(hi_idx), puncture=hi_punct),
            lower=BoundaryZone(lo=llo, hi=lhi, point_indices=tuple(lo_idx), puncture=lo_punct),
        )

    scan = AccumulationScan(
        closed=tuple(found), open_tail=tail, bars_scanned=len(bars), resets=resets
    )
    return scan, offered, refused


def alternating(_ordinal: int, kind: SwingKind, last_kind: SwingKind | None) -> bool:
    """Правило РИСУНКА стр. 18: точки границ идут строго через одну сторону."""
    return last_kind is None or kind is not last_kind


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


def shape(scan: AccumulationScan) -> str:
    """Сравнимая форма разметки: границы, точки, выход. Без множеств и словарей."""
    parts = [
        f"{a.timeframe}|{a.first_index}|{a.last_index}|{a.upper.lo:.10g}|{a.upper.hi:.10g}"
        f"|{a.lower.lo:.10g}|{a.lower.hi:.10g}|{a.upper.point_indices}|{a.lower.point_indices}"
        f"|{a.exit.direction.value}|{a.exit.first_body_index}|{a.exit.confirmed_at_index}"
        for a in scan.closed
    ]
    tail = scan.open_tail
    parts.append(
        "tail:none" if tail is None
        else f"tail:{tail.first_index}|{tail.bars_open}|{tail.upper.lo:.10g}|{tail.lower.hi:.10g}"
    )
    parts.append(f"resets:{scan.resets}")
    return ";".join(parts)


def describe(name: str, values: list[float], unit: str) -> None:
    if not values:
        print(f"  {name}: пусто")
        return
    lo, mid, hi = min(values), statistics.median(values), max(values)
    flat = lo == hi
    print(f"  {name}: n={len(values)}  min={lo:.3f}  медиана={mid:.3f}  max={hi:.3f} {unit}"
          f"{'   ⚠ КОНСТАНТА — зонд считает не то, что назвал' if flat and len(values) > 1 else ''}")


def main() -> int:
    corpus = load()
    if not corpus:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    print("=" * 78)
    print(f"КОРПУС: рядов {len(corpus)}, баров {sum(len(b) for b in corpus.values())}")
    print("=" * 78)

    prepared: list[tuple[str, str, list[Bar], SwingSet]] = []
    skipped = 0
    for (sym, tf), bars in sorted(corpus.items()):
        sw = detect_swings(bars)
        if not isinstance(sw, SwingSet):
            skipped += 1  # NotReady — ряд короче окна фрактала (§4.3)
            continue
        prepared.append((sym, tf, bars, sw))
    if skipped:
        print(f"рядов отброшено как NotReady: {skipped}")
    if not prepared:
        print("Ни одного ряда со свингами: замер не выполнен.")
        return 1

    # --- КОНТРОЛЬ: копия обязана совпасть с боевой функцией -------------------
    print()
    print("КОНТРОЛЬ: копия `detect` против боевой на всём корпусе")
    mismatched = []
    base_shapes: dict[tuple[str, str], str] = {}
    base_scans: dict[tuple[str, str], AccumulationScan] = {}
    base_offered: dict[tuple[str, str], int] = {}
    for sym, tf, bars, sw in prepared:
        real = detect(bars, sw, tf)
        copy, offered, refused = detect_variant(bars, sw, tf)
        base_shapes[(sym, tf)] = shape(copy)
        base_scans[(sym, tf)] = copy
        base_offered[(sym, tf)] = offered
        if shape(real) != shape(copy) or refused:
            mismatched.append((sym, tf))
    if mismatched:
        print(f"  ⚠ РАСХОЖДЕНИЙ {len(mismatched)}: копия неверна, числа Н4 недействительны.")
        for sym, tf in mismatched[:5]:
            print(f"      {sym} {tf}")
        return 1
    print(f"  совпало на всех {len(prepared)} рядах ✅")

    # --- Н4: чередование против нуля -----------------------------------------
    print()
    print("=" * 78)
    print("Н4. ЧЕРЕДОВАНИЕ ТОЧЕК ГРАНИЦЫ (курс задаёт РИСУНКОМ, стр. 18)")
    print("=" * 78)
    alt_changed = 0
    refused_by_row: dict[tuple[str, str], int] = {}
    alt_closed = base_closed = 0
    for sym, tf, bars, sw in prepared:
        scan, _offered, refused = detect_variant(bars, sw, tf, gate=alternating)
        refused_by_row[(sym, tf)] = refused
        base_closed += len(base_scans[(sym, tf)].closed)
        alt_closed += len(scan.closed)
        if shape(scan) != base_shapes[(sym, tf)]:
            alt_changed += 1
    total_refused = sum(refused_by_row.values())
    print(f"  точек отвергнуто чередованием: {total_refused} "
          f"из {sum(base_offered.values())} предъявленных")
    print(f"  рядов изменилось: {alt_changed} из {len(prepared)}")
    print(f"  закрытых накоплений: было {base_closed}, стало {alt_closed}")

    if total_refused == 0:
        print("  ⚠ ЧЕРЕДОВАНИЕ НИЧЕГО НЕ ОТВЕРГЛО: прибор заперт в одном ответе, "
              "сравнивать не с чем.")
        return 1

    print()
    print("  НУЛЬ: случайное прореживание того же числа точек, 5 затравок")
    null_changed: list[int] = []
    for seed in NULL_SEEDS:
        changed = 0
        for sym, tf, bars, sw in prepared:
            k = refused_by_row[(sym, tf)]
            n = base_offered[(sym, tf)]
            if k == 0 or n == 0:
                if shape(detect_variant(bars, sw, tf)[0]) != base_shapes[(sym, tf)]:
                    changed += 1
                continue
            rng = random.Random(row_seed(seed, sym, tf))
            drop = set(rng.sample(range(n), min(k, n)))
            scan, _o, _r = detect_variant(
                bars, sw, tf, gate=lambda o, _k, _l, d=drop: o not in d
            )
            if shape(scan) != base_shapes[(sym, tf)]:
                changed += 1
        null_changed.append(changed)
        print(f"    затравка {seed}: рядов изменилось {changed}")
    null_median = statistics.median(null_changed)
    need = 1.25 * null_median
    print(f"  медиана нуля: {null_median},  разброс {min(null_changed)}…{max(null_changed)}")
    print(f"  ПОРОГ (записан ДО прогона): принять при {alt_changed} >= 1.25 × {null_median} "
          f"= {need:.2f}")
    print(f"  ВЕРДИКТ Н4: {'ПРИНЯТА' if alt_changed >= need else 'ОТВЕРГНУТА'}")

    # --- Н5: жив ли прокол ---------------------------------------------------
    print()
    print("=" * 78)
    print("Н5. ЖИВ ЛИ ПРОКОЛ ЗА ГРАНИЦУ (счёт исходов)")
    print("=" * 78)
    zones = up_punct = lo_punct_n = 0
    for scan in base_scans.values():
        for acc in scan.closed:
            zones += 2
            up_punct += acc.upper.puncture is not None
            lo_punct_n += acc.lower.puncture is not None
        if scan.open_tail is not None:
            zones += 2
            up_punct += scan.open_tail.upper.puncture is not None
            lo_punct_n += scan.open_tail.lower.puncture is not None
    print(f"  зон границ всего: {zones}")
    print(f"  с проколом сверху: {up_punct}")
    print(f"  с проколом снизу:  {lo_punct_n}")
    if up_punct + lo_punct_n == 0:
        print("  ⚠ НОЛЬ: ветка «прокол засчитан, зона не расширена» ничем не наблюдается, "
              "и цитата стр. 18 в модуле держится ни на чём.")

    # --- Н8, Н9: ширина и длительность --------------------------------------
    print()
    print("=" * 78)
    print("Н8, Н9. ШИРИНА И ДЛИТЕЛЬНОСТЬ ПРОТИВ ЧУЖИХ ПОРОГОВ")
    print("=" * 78)
    widths: list[float] = []
    spans: list[float] = []
    for scan in base_scans.values():
        for acc in scan.closed:
            widths.append(max(acc.upper.width_pct, acc.lower.width_pct))
            spans.append(float(acc.last_index - acc.first_index + 1))
    describe("ширина зоны границы, %", widths, "%")
    describe("длительность структуры", spans, "баров")
    if widths:
        wide = sum(w > WIDTH_LIMIT_PCT for w in widths) / len(widths) * 100
        print(f"  шире {WIDTH_LIMIT_PCT}% (порог Screeni-py и TradingPatternScanner): "
              f"{wide:.1f}% структур")
        verdict = ("порог ничего не отсекает" if wide < 5
                   else "находка, идёт владельцу" if wide > 20
                   else "наблюдение без вывода")
        print(f"  ВЕРДИКТ Н8 по записанной шкале (<5% / 5…20% / >20%): {verdict}")
    if spans:
        short = sum(s < SHORT_BARS for s in spans) / len(spans) * 100
        long_ = sum(s > LONG_BARS for s in spans) / len(spans) * 100
        print(f"  короче {SHORT_BARS} баров: {short:.1f}%   длиннее {LONG_BARS}: {long_:.1f}%")
        verdict = ("порог ничего не отсекает" if short < 5
                   else "находка, идёт владельцу" if short > 20
                   else "наблюдение без вывода")
        print(f"  ВЕРДИКТ Н9 по той же шкале: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
