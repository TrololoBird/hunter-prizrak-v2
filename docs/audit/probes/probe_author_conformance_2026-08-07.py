"""ЗОНД R-03: конформанс уровней проекта с разметкой АВТОРА. Фаза 6 аудита.

Допуск зафиксирован ДО прогона: `docs/audit/tolerance-R-03.md`,
sha256 285af7bffbdb547dbc3dcad6c821a9e90f57e2cd82701e0b7aecad4ac62e7e63.
Совпадение: |цена_автора − цена_уровня| <= 0.25 · ATR14(4ч) на момент отсечки.

⚠ ОТСЕЧКА ОБЯЗАТЕЛЬНА. Кадры автора сняты 2026-08-03 14:52 UTC. Ряды баров обрезаются
этой меткой; всё правее расчёту недоступно. Без обрезки замер пользовался бы будущим.

⚠ ФИЛЬТРОВ ПО СОСТОЯНИЮ УРОВНЯ ЗДЕСЬ НЕТ, и это принципиально. Прошлый замер проекта
(`docs/audit/author-poc-2026-08-04.md`) ввёл отбор «только активные» ПОСЛЕ того, как
первый контроль провалился, — то есть подобрал рамку под искомый ответ. Здесь берутся ВСЕ
построенные уровни, и это решение принято до прогона.

КОНТРОЛИ (из файла допуска, заданы заранее):
  К-1  прибор способен ответить иначе;
  К-2а РЕШЁТКА равноотстоящих цен вместо разметки — столько же уровней, тот же диапазон;
  К-2б СДВИГ всей разметки на +-1% и +-2%.
Оба названы владельцем в CLAUDE.md как обязательные для всякого «совпало N из M».

Команда воспроизведения:
    uv run python docs/audit/probes/probe_author_conformance_2026-08-07.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.engine import decide  # noqa: E402
from hunter.models import Bar, NotReady, TradeHistogram  # noqa: E402

CUTOFF_MS = 1785768720000
"""2026-08-03 14:52:00 UTC — штамп кадра автора.

⚠ Первая редакция несла 1785466320000, что есть 2026-07-31 02:52 UTC — на трое суток
раньше. Ошибка поймана тем, что зонд ПЕЧАТАЕТ дату отсечки человекочитаемо, а не только
число. Если бы печаталось число — замер молча мерил бы не тот момент."""

TOLERANCE_ATR = 0.25
MARKUP = ROOT / "research" / "author_markup" / "2026-08-03_overcarder.md"
CACHE = ROOT / "data" / "aggcache"
BUCKET_MS = 300_000
NAME_RE = re.compile(r"^([A-Z0-9]+)-(\d{4}-\d{2}-\d{2})-300000-t([0-9p]+)-b2\.parquet$")
SHIFTS = (0.01, -0.01, 0.02, -0.02)


def parse_markup() -> dict[str, list[float]]:
    """Цены из таблиц файла разметки. Символ определяется заголовком раздела."""
    out: dict[str, list[float]] = {}
    cur: str | None = None
    for line in MARKUP.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+([A-Z]+/USDT:USDT)", line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        if cur and line.startswith("|"):
            cell = line.split("|")[1].strip()
            try:
                out[cur].append(float(cell))
            except ValueError:
                pass
    return {k: sorted(set(v)) for k, v in out.items() if v}


def atr14(bars: list[Bar]) -> float | None:
    if len(bars) < 15:
        return None
    trs = [max(bars[i].high - bars[i].low,
               abs(bars[i].high - bars[i - 1].close),
               abs(bars[i].low - bars[i - 1].close)) for i in range(1, len(bars))]
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


class CacheWindows:
    """Профиль по окну из суточного кэша. Тот же источник, что у боевого WindowSource."""

    def __init__(self, symbol: str, tick: Decimal, cells: dict[int, dict[int, float]]):
        self.symbol, self.tick, self.cells = symbol, tick, cells

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady:
        h = TradeHistogram(symbol=self.symbol, tick_size=self.tick)
        missing = 0
        for b in range(from_ms - from_ms % BUCKET_MS, to_ms, BUCKET_MS):
            cell = self.cells.get(b)
            if cell is None:
                missing += 1
                continue
            for i, q in cell.items():
                h.qty_by_bin[i] = h.qty_by_bin.get(i, 0.0) + q
                h.trades_seen += 1
                h.qty_seen += q
        if missing:
            return NotReady(reason=f"{self.symbol}: окно не покрыто кэшем ({missing} корзин)")
        if not h.qty_by_bin:
            return NotReady(reason=f"{self.symbol}: в окне нет сделок")
        return h


def load_cache(market_id: str) -> tuple[Decimal, dict[int, dict[int, float]]] | None:
    tick: Decimal | None = None
    cells: dict[int, dict[int, float]] = defaultdict(dict)
    for p in sorted(CACHE.iterdir()):
        m = NAME_RE.match(p.name)
        if not m or m.group(1) != market_id:
            continue
        tick = Decimal(m.group(3).replace("p", "."))
        df = pl.read_parquet(p)
        for b, i, q in zip(df["bucket"], df["bin"], df["qty"], strict=True):
            c = cells[int(b)]
            c[int(i)] = c.get(int(i), 0.0) + float(q)
    return None if tick is None else (tick, dict(cells))


def matched(targets: list[float], levels: list[float], tol: float) -> tuple[int, list[float]]:
    """Сколько целей накрыто хоть одним уровнем и расстояния до ближайшего."""
    hits = 0
    dists: list[float] = []
    for t in targets:
        if not levels:
            dists.append(float("inf"))
            continue
        d = min(abs(t - x) for x in levels)
        dists.append(d)
        if d <= tol:
            hits += 1
    return hits, dists


def main() -> int:
    import datetime as dt
    print("=" * 80)
    print("R-03  Конформанс с разметкой автора (фаза 6)")
    print("=" * 80)
    print(f"отсечка: {CUTOFF_MS} = "
          f"{dt.datetime.fromtimestamp(CUTOFF_MS / 1000, dt.UTC):%Y-%m-%d %H:%M UTC}")
    print(f"допуск:  {TOLERANCE_ATR} · ATR14(4ч), зафиксирован до прогона")

    author = parse_markup()
    print(f"разметка: {', '.join(f'{k} — {len(v)} цен' for k, v in author.items())}")

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

    total_hits = 0
    total_targets = 0
    alive_hits = [0]
    alive_total = [0]
    built_total = [0]
    ctrl: dict[str, list[tuple[int, int]]] = defaultdict(list)
    ctrl_alive: dict[str, list[tuple[int, int]]] = defaultdict(list)
    print()
    for sym, prices in sorted(author.items()):
        tfs = series_all.get(sym)
        if not tfs:
            print(f"{sym}: кадров нет — НЕ СМОГ ПРОВЕРИТЬ")
            continue
        cut = {tf: [b for b in bars if b.open_ms + TIMEFRAME_MS[tf] <= CUTOFF_MS]
               for tf, bars in tfs.items()}
        cut = {tf: b for tf, b in cut.items() if len(b) >= 20}
        loaded = load_cache(sym.split("/")[0] + "USDT")
        trades = None if loaded is None else CacheWindows(sym, loaded[0], loaded[1])
        d = decide(sym, cut, trades, tuple(cut))
        levels = [float(m.level.price) for m in d.mapped]
        # R-05, зафиксировано ДО прогона в `docs/audit/tolerance-R-03.md`: автор публикует
        # уровни, КОТОРЫМИ ТОРГУЕТ. Стр. 25 удаляет отработанный, стр. 43 переворачивает
        # пробитый. Сравнивать с разметкой отработанные и флипнутые — сравнивать разные
        # множества. Отбор взят из курса, а не из результата.
        alive = [float(m.level.price) for m in d.mapped if m.alive_at(CUTOFF_MS)]
        by_tf: dict[str, int] = defaultdict(int)
        for m in d.mapped:
            by_tf[m.level.timeframe] += 1
        four = cut.get("4h", [])
        a = atr14(four)
        if a is None or a <= 0:
            print(f"{sym}: ATR14(4ч) не посчитан — НЕ СМОГ ПРОВЕРИТЬ")
            continue
        tol = TOLERANCE_ATR * a

        print(f"--- {sym} ---")
        print(f"  ATR14(4ч) на отсечке {a:.2f}; допуск {tol:.2f}")
        print(f"  баров после обрезки: " + ", ".join(f"{t}={len(b)}" for t, b in cut.items()))
        print(f"  уровней построено: {len(levels)}  ({dict(by_tf)})")
        if not levels:
            print("  уровней НЕТ — сравнивать нечем")
            continue
        hits, dists = matched(prices, levels, tol)
        a_hits, _ = matched(prices, alive, tol)
        total_hits += hits
        total_targets += len(prices)
        alive_hits[0] += a_hits
        fin = [x for x in dists if x != float("inf")]
        print(f"  ВОСПРОИЗВЕДЕНО {hits} из {len(prices)} ({hits / len(prices) * 100:.1f}%)")
        print(f"  расстояние до ближайшего уровня, ATR: медиана "
              f"{statistics.median(fin) / a:.2f}, минимум {min(fin) / a:.2f}")
        back, _ = matched(levels, prices, tol)
        print(f"  ОБРАТНО: уровней проекта рядом с ценой автора {back} из {len(levels)}"
              f"  → ложных {len(levels) - back}")

        lo, hi = min(prices), max(prices)
        grid = [lo + (hi - lo) * i / (len(prices) - 1) for i in range(len(prices))]
        g_hits, _ = matched(grid, levels, tol)
        ctrl["решётка"].append((g_hits, len(grid)))
        print(f"  К-2а решётка равноотстоящих: {g_hits} из {len(grid)} "
              f"({g_hits / len(grid) * 100:.1f}%)")
        for s in SHIFTS:
            sh = [p * (1 + s) for p in prices]
            s_hits, _ = matched(sh, levels, tol)
            ctrl[f"сдвиг {s:+.0%}"].append((s_hits, len(sh)))
            print(f"  К-2б сдвиг {s:+.0%}: {s_hits} из {len(sh)} "
                  f"({s_hits / len(sh) * 100:.1f}%)")
        print(f"  --- R-05: только ЖИВЫЕ уровни (стр. 25, 43) — их {len(alive)} "
              f"из {len(levels)} ---")
        print(f"  ВОСПРОИЗВЕДЕНО {a_hits} из {len(prices)} "
              f"({a_hits / len(prices) * 100:.1f}%)")
        g2, _ = matched(grid, alive, tol)
        ctrl_alive["решётка"].append((g2, len(grid)))
        print(f"  К-2а решётка: {g2} из {len(grid)} ({g2 / len(grid) * 100:.1f}%)")
        for s in SHIFTS:
            sh = [p * (1 + s) for p in prices]
            s2, _ = matched(sh, alive, tol)
            ctrl_alive[f"сдвиг {s:+.0%}"].append((s2, len(sh)))
            print(f"  К-2б сдвиг {s:+.0%}: {s2} из {len(sh)} "
                  f"({s2 / len(sh) * 100:.1f}%)")
        alive_total[0] += len(alive)
        built_total[0] += len(levels)
        print()

    if not total_targets:
        print("ЗАМЕР НЕ СОСТОЯЛСЯ")
        return 1

    real = total_hits / total_targets * 100
    print("=" * 80)
    print(f"ИТОГО ВОСПРОИЗВЕДЕНО: {total_hits} из {total_targets} ({real:.1f}%)")
    print()
    print("КОНТРОЛЬ К-1: способен ли прибор ответить иначе?")
    print("   ✓ пройден" if 0 < total_hits < total_targets
          else "   ⚠ НЕ ПРОЙДЕН: ответ вырожден")
    print()
    print("КОНТРОЛЬ К-2: лучше ли настоящая разметка заведомо неверной?")
    print(f"   {'нуль':<16} {'совпало':>9} {'доля':>8}   вердикт")
    worse = 0
    for name, pairs in ctrl.items():
        h = sum(x for x, _ in pairs)
        n = sum(y for _, y in pairs)
        p = h / n * 100 if n else 0.0
        v = "✓ хуже настоящей" if p < real else ("= НЕ ХУЖЕ" if p == real else "✗ ЛУЧШЕ")
        if p >= real:
            worse += 1
        print(f"   {name:<16} {h:>4} из {n:<4} {p:>7.1f}%   {v}")
    if worse:
        print(f"   ⚠ {worse} нулей воспроизводятся не хуже настоящей разметки —")
        print("     число публикации не подлежит без разбора.")

    print()
    print("=" * 80)
    print("R-05  ТО ЖЕ, но только по ЖИВЫМ уровням (отбор из курса, зафиксирован до прогона)")
    print("=" * 80)
    ar = alive_hits[0] / total_targets * 100
    print(f"уровней живых {alive_total[0]} из {built_total[0]} построенных")
    print(f"ВОСПРОИЗВЕДЕНО {alive_hits[0]} из {total_targets} ({ar:.1f}%)")
    print(f"   {'нуль':<16} {'совпало':>9} {'доля':>8}   вердикт")
    worse2 = 0
    for name, pairs in ctrl_alive.items():
        h = sum(x for x, _ in pairs)
        n = sum(y for _, y in pairs)
        pv = h / n * 100 if n else 0.0
        v = "✓ хуже настоящей" if pv < ar else ("= НЕ ХУЖЕ" if pv == ar else "✗ ЛУЧШЕ")
        if pv >= ar:
            worse2 += 1
        print(f"   {name:<16} {h:>4} из {n:<4} {pv:>7.1f}%   {v}")
    print()
    if worse2:
        print(f"   ⚠ {worse2} нулей не хуже — конформанс НЕ ПОДТВЕРЖДЁН и на живых уровнях.")
    else:
        print("   ✓ ВСЕ нули хуже настоящей разметки — конформанс подтверждён.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
