"""ЗОНД: накопление ПОСЛЕ правок Н2/Н10/Н1/Н6 и отката чужой границы (коммит 0f07c3b).

Зачем новый файл, а не правка старых. Соглашение о заморозке (pyproject.toml, раздел
`tool.ruff`) запрещает править зонд: отредактированный зонд перестаёт быть тем, что дало
опубликованное число. Три зонда накопления после правок сломались, и каждый — по своей
причине; ни одну нельзя починить, не поменяв то, что зонд меряет:

  probe_accumulation_questions_2026-08-07.py  (коммит 65144ca)
      строит BoundaryZone без поля `edge` → ValidationError. Его копия `detect` — это код
      ДО чередования, а чередование он подавал ГИПОТЕЗОЙ и проверял нулём. Так больше
      нельзя: правило рисунка стр. 21 исполняется, а не проверяется (CLAUDE.md).
  probe_accumulation_alt_fine_2026-08-07.py   (коммит 65144ca)
      импортирует машину предыдущего — падает вместе с ним.
  probe_accumulation_wedge_2026-08-08.py      (коммит 9622f1d)
      НЕ падает: его собственный контроль честно печатает «РАСХОЖДЕНИЙ 18» и возвращает 1.
      Копия `detect` в нём — код до правки границы. Контроль сработал ровно так, как
      задуман, и потому старые числа сами себя отозвали.

Каждый из трёх воспроизводится на своём коммите:
    git worktree add /tmp/pin <коммит> && uv run python /tmp/pin/docs/audit/probes/<файл>

ЧТО СЧИТАЕТ ЭТОТ ЗОНД (всё — по коду, который стоит в дереве сейчас):
  К    контроль: инструментованная копия обязана дать побитово ту же разметку, что боевой
       `detect`, на каждом ряду. Без этого недействительно всё остальное.
  С1   сколько раз сработала ветка «границы сошлись» (сброс структуры);
  С2   сколько экстремумов отвергнуто как касание внутри базы — те, что НЕ продолжают
       сужение. До правки Н10 сюда уходили и точки клина;
  С2б  сколько точек ПРИНЯТО сужением — ветка, которой до правки не было вовсе;
  С3   распределение `narrowed` по закрытым структурам: сколько баз в сужении (стр. 34);
  С4   доля границ, унаследованных лесенкой (стр. 40);
  Н4   ЦЕНА чередования: разметка с правилом против разметки без него. Это НЕ вердикт —
       правило взято с рисунка курса и уже исполнено. Нуль печатается затем, чтобы
       отделить «важна сторона точки» от «важно просто выбросить столько же точек»;
  Н5   живы ли проколы: сколько зон имеют `puncture`. После правки 1 (внешняя из первых
       двух точек пишется проколом) число обязано вырасти;
  Н8   ширина структуры edge↔edge против чужого порога 10%. ⚠ Прежний зонд сравнивал с
       этим порогом `width_pct`, то есть ДОПУСК приёма точек, а не высоту коробки —
       сравнивалось не то. Здесь печатаются обе величины;
  Н9   длительность структуры против чужих порогов 5 и 140 баров.

КОНТРОЛИ, без которых числа недействительны:
  * копия против боевой функции — иначе меряется зонд, а не код;
  * каждый счётчик печатается вместе со знаменателем: ноль обязан быть отличим от
    «ветка недостижима»;
  * нуль для Н4 ломает именно сторону точки: он выбрасывает СТОЛЬКО ЖЕ точек, но не
    глядя, верхняя она или нижняя;
  * распределения печатают min/медиану/max: константа означала бы, что зонд считает не то.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_accumulation_after_fixes_2026-08-08.py
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
    BorderSource,
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

SEED = 20260808
NULL_SEEDS = (1, 2, 3, 4, 5)

WIDTH_LIMIT_PCT = 10.0
"""Порог ширины у Screeni-py (`percentage=10`) и TradingPatternScanner
(`channel_range = 0.1`) — два независимых проекта назвали одно число."""

SHORT_BARS = 5
"""Нижний предел длительности у трёх реализаций Дарваса и Consolidation Zones."""

LONG_BARS = 140
"""`BoxLength=140` у BreakOutBox — единственный найденный ВЕРХНИЙ ориентир."""


def row_seed(seed: int, sym: str, tf: str) -> int:
    """Затравка ряда: устойчивый дайджест, а НЕ встроенный `hash`.

    Встроенный `hash` строк солится на каждый процесс (`PYTHONHASHSEED`), и нуль
    получался бы разным от запуска к запуску — два прогона предыдущего зонда дали
    медианы 481 и 479. Число, которое нельзя повторить, §7.3 не фиксирует.
    """
    digest = hashlib.sha256(f"{sym}|{tf}".encode()).digest()
    return SEED + seed * 1000 + int.from_bytes(digest[:4], "big") % 997


Gate = Callable[[int], bool]
"""Ворота нуля: решает по ПОРЯДКОВОМУ НОМЕРУ пришедшей точки, впустить ли её.

Номер один на весь ряд и считается по всем пришедшим точкам, независимо от стороны —
именно так нуль ломает свойство «сторона важна», оставляя «точек стало меньше».
"""


class Counters:
    """Счётчики развилок. Обычный класс, а не словарь: §10.1 запрещает словари между
    слоями, и опечатка в имени ключа здесь была бы не видна."""

    __slots__ = (
        "offered", "refused_alternation", "refused_gate", "refused_inside",
        "accepted_narrowing", "crossings", "ladder_borders", "emitted",
    )

    def __init__(self) -> None:
        self.offered = 0
        self.refused_alternation = 0
        self.refused_gate = 0
        self.refused_inside = 0
        self.accepted_narrowing = 0
        self.crossings = 0
        self.ladder_borders = 0
        self.emitted = 0


def walk(
    bars: list[Bar],
    swings: SwingSet,
    timeframe: str,
    *,
    alternation: bool = True,
    gate: Gate | None = None,
    min_points: int = MIN_BOUNDARY_POINTS,
    confirm_bodies: int = CONFIRM_BODIES,
) -> tuple[AccumulationScan, Counters]:
    """ПОЛНАЯ копия `accumulation.detect` со счётчиками и двумя переключателями.

    `alternation=True` и `gate=None` — поведение боевой функции без изменений; это
    проверяется контролем в `main`, и без совпадения числа ниже недействительны.
    """
    by_confirm: dict[int, list[tuple[SwingKind, int, float]]] = {}
    for s in swings.swings:
        by_confirm.setdefault(s.confirmed_at_index, []).append((s.kind, s.index, s.price))

    c = Counters()
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
    last_kind: SwingKind | None = None
    up_edge: float | None = None
    lo_edge: float | None = None
    up_narrowed = 0
    lo_narrowed = 0
    up_source = BorderSource.OWN
    lo_source = BorderSource.OWN
    ladder_up: float | None = None
    ladder_lo: float | None = None

    def reset() -> None:
        nonlocal run_dir, run_from, hi_punct, lo_punct, resets, last_kind
        nonlocal up_edge, lo_edge, up_narrowed, lo_narrowed
        nonlocal up_source, lo_source
        hi_px.clear()
        hi_idx.clear()
        lo_px.clear()
        lo_idx.clear()
        hi_punct = lo_punct = None
        run_dir, run_from = None, 0
        resets += 1
        last_kind = None
        up_edge = lo_edge = None
        up_narrowed = lo_narrowed = 0
        up_source = lo_source = BorderSource.OWN

    def upper_zone() -> tuple[float, float]:
        return min(hi_px[:2]), max(hi_px[:2])

    def lower_zone() -> tuple[float, float]:
        return min(lo_px[:2]), max(lo_px[:2])

    def converging(price: float, kind: SwingKind) -> bool:
        if len(hi_px) < 2 or len(lo_px) < 2:
            return False
        if kind is SwingKind.HIGH:
            return price < hi_px[-1] and lo_px[-1] > lo_px[-2]
        return price > lo_px[-1] and hi_px[-1] < hi_px[-2]

    for k in range(len(bars)):
        for kind, index, price in by_confirm.get(k, []):
            ordinal = c.offered
            c.offered += 1
            if gate is not None and not gate(ordinal):
                c.refused_gate += 1
                continue
            if alternation and kind is last_kind:
                c.refused_alternation += 1
                continue
            if kind is SwingKind.HIGH:
                if len(hi_px) < 2:
                    hi_px.append(price)
                    hi_idx.append(index)
                    last_kind = kind
                    if len(hi_px) == 2:
                        up_edge = min(hi_px)
                        if (ladder_up is not None
                                and min(hi_px) <= ladder_up <= max(hi_px)):
                            up_edge = ladder_up
                            up_source = BorderSource.LADDER
                            c.ladder_borders += 1
                        beyond = [p for p in hi_px[:2] if p > up_edge]
                        if beyond:
                            hi_punct = (max(beyond) if hi_punct is None
                                        else max(hi_punct, max(beyond)))
                    continue
                assert up_edge is not None
                if price < up_edge:
                    if not converging(price, kind):
                        c.refused_inside += 1
                        continue
                    hi_px.append(price)
                    hi_idx.append(index)
                    last_kind = kind
                    up_edge = price
                    up_narrowed += 1
                    c.accepted_narrowing += 1
                    continue
                hi_px.append(price)
                hi_idx.append(index)
                last_kind = kind
                if price > up_edge:
                    hi_punct = price if hi_punct is None else max(hi_punct, price)
            else:
                if len(lo_px) < 2:
                    lo_px.append(price)
                    lo_idx.append(index)
                    last_kind = kind
                    if len(lo_px) == 2:
                        lo_edge = max(lo_px)
                        if (ladder_lo is not None
                                and min(lo_px) <= ladder_lo <= max(lo_px)):
                            lo_edge = ladder_lo
                            lo_source = BorderSource.LADDER
                            c.ladder_borders += 1
                        beyond = [p for p in lo_px[:2] if p < lo_edge]
                        if beyond:
                            lo_punct = (min(beyond) if lo_punct is None
                                        else min(lo_punct, min(beyond)))
                    continue
                assert lo_edge is not None
                if price > lo_edge:
                    if not converging(price, kind):
                        c.refused_inside += 1
                        continue
                    lo_px.append(price)
                    lo_idx.append(index)
                    last_kind = kind
                    lo_edge = price
                    lo_narrowed += 1
                    c.accepted_narrowing += 1
                    continue
                lo_px.append(price)
                lo_idx.append(index)
                last_kind = kind
                if price < lo_edge:
                    lo_punct = price if lo_punct is None else min(lo_punct, price)

        if len(hi_px) < 2 or len(lo_px) < 2:
            continue

        upper_lo, upper_hi = upper_zone()
        lower_lo, lower_hi = lower_zone()
        assert up_edge is not None and lo_edge is not None
        upper_edge, lower_edge = up_edge, lo_edge
        if upper_edge <= lower_edge:
            c.crossings += 1
            reset()
            continue

        bar = bars[k]
        if body_beyond(bar, upper_edge, Direction.ABOVE):
            direction = Direction.ABOVE
        elif body_beyond(bar, lower_edge, Direction.BELOW):
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
                upper=BoundaryZone(edge=upper_edge, lo=upper_lo, hi=upper_hi,
                                   narrowed=up_narrowed, source=up_source,
                                   point_indices=tuple(hi_idx), puncture=hi_punct),
                lower=BoundaryZone(edge=lower_edge, lo=lower_lo, hi=lower_hi,
                                   narrowed=lo_narrowed, source=lo_source,
                                   point_indices=tuple(lo_idx), puncture=lo_punct),
                exit=StructureExit(
                    direction=direction,
                    first_body_index=run_from,
                    confirmed_at_index=k,
                ),
            )
        )
        c.emitted += 1
        if direction is Direction.ABOVE:
            ladder_lo = upper_edge
        else:
            ladder_up = lower_edge
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
            upper=BoundaryZone(edge=up_edge if up_edge is not None else ulo,
                               lo=ulo, hi=uhi, narrowed=up_narrowed,
                               source=up_source,
                               point_indices=tuple(hi_idx), puncture=hi_punct),
            lower=BoundaryZone(edge=lo_edge if lo_edge is not None else lhi,
                               lo=llo, hi=lhi, narrowed=lo_narrowed,
                               source=lo_source,
                               point_indices=tuple(lo_idx), puncture=lo_punct),
        )

    scan = AccumulationScan(
        closed=tuple(found), open_tail=tail, bars_scanned=len(bars), resets=resets
    )
    return scan, c


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


def zone_shape(z: BoundaryZone) -> str:
    return (f"{z.edge:.10g}|{z.source.value}|{z.narrowed}|{z.lo:.10g}|{z.hi:.10g}"
            f"|{z.point_indices}|{'-' if z.puncture is None else format(z.puncture, '.10g')}")


def shape(scan: AccumulationScan) -> str:
    """Сравнимая форма разметки. Без множеств и словарей — порядок обязан быть один."""
    parts = [
        f"{a.timeframe}|{a.first_index}|{a.last_index}"
        f"|{zone_shape(a.upper)}|{zone_shape(a.lower)}"
        f"|{a.exit.direction.value}|{a.exit.first_body_index}|{a.exit.confirmed_at_index}"
        for a in scan.closed
    ]
    t = scan.open_tail
    parts.append("tail:none" if t is None
                 else f"tail:{t.first_index}|{t.bars_open}|{zone_shape(t.upper)}"
                      f"|{zone_shape(t.lower)}")
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


def main() -> int:  # noqa: C901
    corpus = load()
    if not corpus:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    prepared: list[tuple[str, str, list[Bar], SwingSet]] = []
    skipped = 0
    for (sym, tf), bars in sorted(corpus.items()):
        sw = detect_swings(bars)
        if not isinstance(sw, SwingSet):
            skipped += 1  # NotReady — ряд короче окна фрактала (§4.3)
            continue
        prepared.append((sym, tf, bars, sw))
    if not prepared:
        print("Ни одного ряда со свингами: замер не выполнен.")
        return 1

    print("=" * 78)
    print(f"КОРПУС: рядов {len(prepared)} (отброшено NotReady {skipped}), "
          f"баров {sum(len(b) for b in corpus.values())}")
    print("=" * 78)

    # --- К. КОНТРОЛЬ: копия обязана совпасть с боевой функцией ------------------
    base: dict[tuple[str, str], AccumulationScan] = {}
    base_shape: dict[tuple[str, str], str] = {}
    total = Counters()
    mismatched: list[str] = []
    for sym, tf, bars, sw in prepared:
        scan, c = walk(bars, sw, tf)
        real = detect(bars, sw, tf)
        if shape(scan) != shape(real):
            mismatched.append(f"{sym} {tf}")
        base[(sym, tf)] = scan
        base_shape[(sym, tf)] = shape(scan)
        for slot in Counters.__slots__:
            setattr(total, slot, getattr(total, slot) + getattr(c, slot))

    print("\nК. КОНТРОЛЬ: инструментованная копия против боевого `detect`")
    if mismatched:
        print(f"  ⚠ РАСХОЖДЕНИЙ {len(mismatched)} — ВСЕ ЧИСЛА НИЖЕ НЕДЕЙСТВИТЕЛЬНЫ:")
        for m in mismatched[:5]:
            print(f"      {m}")
        return 1
    print(f"  разметка совпала побитово на всех {len(prepared)} рядах ✅")

    closed = [a for s in base.values() for a in s.closed]
    print(f"  закрытых накоплений: {len(closed)}, точек предъявлено: {total.offered}")

    # --- С1…С4: развилки приёма точки -----------------------------------------
    print("\n" + "=" * 78)
    print("С. ЧТО КОД ДЕЛАЕТ С ТОЧКАМИ И ГРАНИЦАМИ")
    print("=" * 78)
    print(f"С1  ветка «границы сошлись» (сброс структуры): {total.crossings}")
    if total.crossings == 0:
        print("    ⚠ НОЛЬ — ветка ничем не наблюдается, рассказ про схождение держится ни на чём.")

    pct = total.refused_inside / max(total.offered, 1) * 100
    print(f"С2  отвергнуто как касание внутри базы: {total.refused_inside} "
          f"из {total.offered} ({pct:.1f}%)")
    print(f"    отвергнуто чередованием (стр. 21):   {total.refused_alternation} "
          f"({total.refused_alternation / max(total.offered, 1) * 100:.1f}%)")
    if total.refused_inside == 0:
        print("    ⚠ НОЛЬ — счётчик не может сработать, числа недействительны.")

    print(f"С2б ПРИНЯТО сужением (ветки не было до правки Н10): {total.accepted_narrowing}")
    if total.accepted_narrowing == 0:
        print("    ⚠ НОЛЬ — правка Н10 не наблюдается ни на одном ряду.")

    narrow_hist: dict[int, int] = {}
    for a in closed:
        n = a.upper.narrowed + a.lower.narrowed
        narrow_hist[n] = narrow_hist.get(n, 0) + 1
    wedges = sum(v for k, v in narrow_hist.items() if k > 0)
    print(f"С3  структур в сужении (narrowed > 0): {wedges} из {len(closed)} "
          f"({wedges / max(len(closed), 1) * 100:.1f}%)")
    for n in sorted(narrow_hist):
        print(f"      narrowed={n:2d}: {narrow_hist[n]:4d}")

    zones = [z for a in closed for z in (a.upper, a.lower)]
    ladder = sum(z.source is BorderSource.LADDER for z in zones)
    print(f"С4  границ от лесенки (стр. 40): {ladder} из {len(zones)} зон "
          f"({ladder / max(len(zones), 1) * 100:.1f}%)")
    if ladder == 0:
        print("    ⚠ НОЛЬ — наследование границы не наблюдается.")

    # --- Н4: ЦЕНА чередования (не вердикт) ------------------------------------
    print("\n" + "=" * 78)
    print("Н4. ЦЕНА ЧЕРЕДОВАНИЯ ТОЧЕК — правило РИСУНКА стр. 21, уже исполнено")
    print("=" * 78)
    print("    Это замер ЦЕНЫ принятой правки, а не проверка правила нулём:")
    print("    правило курса исполняется, а не подтверждается замером (CLAUDE.md).")

    off_shape: dict[tuple[str, str], str] = {}
    off_offered: dict[tuple[str, str], int] = {}
    refused_row: dict[tuple[str, str], int] = {}
    no_alt_closed = 0
    changed = 0
    for sym, tf, bars, sw in prepared:
        scan, c = walk(bars, sw, tf, alternation=False)
        off_shape[(sym, tf)] = shape(scan)
        off_offered[(sym, tf)] = c.offered
        no_alt_closed += len(scan.closed)
        _s, c_on = walk(bars, sw, tf)
        refused_row[(sym, tf)] = c_on.refused_alternation
        if shape(scan) != base_shape[(sym, tf)]:
            changed += 1
    print(f"  рядов, где разметка отличается от «без чередования»: {changed} из {len(prepared)}")
    print(f"  закрытых накоплений: с правилом {len(closed)}, без правила {no_alt_closed}")
    if changed == len(prepared):
        print("  ⚠ МЕРА НАСЫЩЕНА (18 из 18): по ней ничего сравнивать нельзя, "
              "смотри число накоплений ниже.")

    print("\n  НУЛЬ: выбросить СТОЛЬКО ЖЕ точек случайно, не глядя на сторону")
    null_changed: list[int] = []
    null_closed: list[int] = []
    for seed in NULL_SEEDS:
        ch = shut = 0
        for sym, tf, bars, sw in prepared:
            k, n = refused_row[(sym, tf)], off_offered[(sym, tf)]
            if k == 0 or n == 0:
                if off_shape[(sym, tf)] != base_shape[(sym, tf)]:
                    ch += 1
                scan, _c = walk(bars, sw, tf, alternation=False)
                shut += len(scan.closed)
                continue
            rng = random.Random(row_seed(seed, sym, tf))
            drop = set(rng.sample(range(n), min(k, n)))
            scan, _c = walk(bars, sw, tf, alternation=False,
                            gate=lambda o, d=drop: o not in d)
            if shape(scan) != off_shape[(sym, tf)]:
                ch += 1
            shut += len(scan.closed)
        null_changed.append(ch)
        null_closed.append(shut)
        print(f"    затравка {seed}: рядов изменилось {ch}, закрытых накоплений {shut}")
    print(f"  медиана нуля по накоплениям: {statistics.median(null_closed)}  "
          f"(с правилом {len(closed)}, без правила {no_alt_closed})")

    # --- Н5: живы ли проколы ---------------------------------------------------
    print("\n" + "=" * 78)
    print("Н5. ПРОКОЛЫ ЗА ГРАНИЦУ (стр. 18) — после правки 1 обязаны вырасти")
    print("=" * 78)
    all_zones = list(zones)
    for s in base.values():
        if s.open_tail is not None:
            all_zones.extend((s.open_tail.upper, s.open_tail.lower))
    punct = sum(z.puncture is not None for z in all_zones)
    print(f"  зон границ всего: {len(all_zones)}")
    print(f"  с проколом: {punct} ({punct / max(len(all_zones), 1) * 100:.1f}%)")
    if punct == 0:
        print("  ⚠ НОЛЬ: ветка «прокол засчитан, граница не сдвинута» не наблюдается, "
              "и цитата стр. 18 в модуле держится ни на чём.")

    # --- Н8, Н9 ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("Н8, Н9. ВЫСОТА И ДЛИТЕЛЬНОСТЬ ПРОТИВ ЧУЖИХ ПОРОГОВ")
    print("=" * 78)
    heights: list[float] = []
    tolerances: list[float] = []
    spans: list[float] = []
    for a in closed:
        mid = (a.upper.edge + a.lower.edge) / 2
        heights.append(0.0 if mid == 0 else (a.upper.edge - a.lower.edge) / mid * 100)
        tolerances.append(max(a.upper.width_pct, a.lower.width_pct))
        spans.append(float(a.last_index - a.first_index + 1))
    describe("ВЫСОТА коробки edge↔edge", heights, "%")
    describe("допуск приёма точек (прежний зонд мерил ЕГО)", tolerances, "%")
    describe("длительность структуры", spans, "баров")
    if heights:
        wide = sum(h > WIDTH_LIMIT_PCT for h in heights) / len(heights) * 100
        print(f"  выше {WIDTH_LIMIT_PCT}% (порог Screeni-py и TradingPatternScanner): "
              f"{wide:.1f}% структур")
        print(f"  ВЕРДИКТ Н8 по записанной шкале (<5% / 5…20% / >20%): "
              f"{'порог ничего не отсекает' if wide < 5 else 'находка, идёт владельцу' if wide > 20 else 'наблюдение без вывода'}")
    if spans:
        short = sum(s < SHORT_BARS for s in spans) / len(spans) * 100
        long_ = sum(s > LONG_BARS for s in spans) / len(spans) * 100
        print(f"  короче {SHORT_BARS} баров: {short:.1f}%   длиннее {LONG_BARS}: {long_:.1f}%")
        print(f"  ВЕРДИКТ Н9 по той же шкале: "
              f"{'порог ничего не отсекает' if short < 5 else 'находка, идёт владельцу' if short > 20 else 'наблюдение без вывода'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
