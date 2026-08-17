"""РАЗБОР ОДНОГО СИМВОЛА: все доступные данные и карта уровней на всех таймфреймах.

Запрос владельца 2026-08-12: «запроси ВСЕ возможные данные и рассчитай карту уровней,
точек входа, сетапы и так далее на всех доступных таймфреймах».

⚠ ГРАНИЦА, КОТОРУЮ ЗОНД НЕ ПЕРЕХОДИТ И НАЗЫВАЕТ. Площадка отдаёт шестнадцать
таймфреймов, курс называет шесть (§2.8, список старшинства стр. 17). Поэтому:

  * УРОВНИ (структура + профиль объёма + ПОК) считаются на ВСЕХ, где длительность
    определена: геометрия от списка курса не зависит;
  * ПРИОРИТЕТ, СОГЛАСИЕ СТАРШИХ И СЕТАП — только на шести. Они определены ЧЕРЕЗ
    старшинство таймфреймов, а старшинство `2h` относительно `4h` курс не задаёт.
    Придумать его «по смыслу» — завести правило без источника, чего §0 не разрешает;
  * `1M` (календарный месяц) пропускается: его длительность непостоянна, и любое число
    миллисекунд было бы выдумкой.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_symbol_full_2026-08-12.py GPS/USDT:USDT
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import barstore, card  # noqa: E402
from hunter.accumulation import detect as detect_accumulations  # noqa: E402
from hunter.bars import TIMEFRAME_MS, VENUE_MS, tf_ms  # noqa: E402
from hunter.config import load_universe  # noqa: E402
from hunter.engine import decide  # noqa: E402
from hunter.exchange import OHLCV_PAGE, Exchange  # noqa: E402
from hunter.levels import structure_window_ms  # noqa: E402
from hunter.models import Bar, NotReady  # noqa: E402
from hunter.profile_source import CandleWindows  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402
from hunter.volume_profile import point_of_control  # noqa: E402

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "GPS/USDT:USDT"

SECOND_CAP = 1_000
"""Сколько секундных баров брать. Полная история на `1s` за полтора года — сорок семь
миллионов баров: это не «все данные», а отказ в обслуживании самим себе. Усечение НАЗВАНО
здесь и печатается в сводке, потому что молчаливое читалось бы как «взяли всё»."""


async def fetch_all(ex: Exchange, symbol: str, res: str, since: int,
                    until: int) -> list[Bar]:
    """Полная история разрешения `res` страницами по `OHLCV_PAGE`."""
    step = tf_ms(res)
    out: dict[int, Bar] = {}
    cursor = since
    while cursor <= until:
        raw: Any = await ex._rest(
            f"свечи {res}", f"{symbol} {res}",
            lambda c=cursor: ex._ex.fetch_ohlcv(symbol, res, since=c, limit=OHLCV_PAGE))
        if isinstance(raw, NotReady) or not raw:
            break
        for r in raw:
            t = int(r[0])
            try:
                out[t] = Bar(open_ms=t, open=float(r[1]), high=float(r[2]),
                             low=float(r[3]), close=float(r[4]), volume=float(r[5]))
            except ValueError:
                pass
        last = int(raw[-1][0])
        if last < cursor:
            break
        cursor = last + step
        if res == "1s" and len(out) >= SECOND_CAP:
            break
    return [out[k] for k in sorted(out)]


async def main() -> int:
    uni = load_universe()
    ex = Exchange(uni.venue)
    sync = await ex.open()
    print(f"\n{'=' * 78}\nСИМВОЛ {SYMBOL} — площадка {uni.venue}\n{'=' * 78}")

    inst = ex.instrument(SYMBOL)
    if isinstance(inst, NotReady):
        print(f"инструмент недоступен: {inst.reason}")
        await ex.close()
        return 1
    market = ex._ex.market(SYMBOL)
    created = int(market.get("created") or 0)
    now = sync.measured_at_local_ms + sync.offset_ms
    print(f"идентификатор {inst.market_id}, шаг цены {inst.tick_size}, "
          f"рынок создан {created}, возраст {(now - created) / 86_400_000:.0f} суток")

    # ---------- 1. ВСЕ ПУБЛИЧНЫЕ ДАННЫЕ, КРОМЕ СВЕЧЕЙ
    print(f"\n{'-' * 78}\n1. СПРАВОЧНЫЕ ДАННЫЕ (каждый пункт — отдельный запрос)\n")
    probes: list[tuple[str, Any]] = [
        ("суточная статистика", ex.fetch_ticker(SYMBOL)),
        ("лучшие цены", ex.fetch_book_top(SYMBOL)),
        ("стакан (100 уровней)", ex.fetch_order_book(SYMBOL, 100)),
        ("маркировочная цена", ex.fetch_mark_price(SYMBOL)),
        ("ставка финансирования", ex.fetch_funding(SYMBOL)),
        ("история ставки", ex.fetch_funding_rate_history(SYMBOL, None, 100)),
        ("открытый интерес", ex.fetch_open_interest(SYMBOL)),
        ("история интереса", ex.fetch_open_interest_history(SYMBOL, "1h", 100)),
        ("соотношение позиций", ex.fetch_long_short_ratio(SYMBOL, "1h", 100)),
        ("ликвидации", ex.fetch_liquidations(SYMBOL, None, 100)),
    ]
    for name, coro in probes:
        got = await coro
        if isinstance(got, NotReady):
            print(f"  {name:<24} ОТКАЗ: {got.reason}")
        elif isinstance(got, list):
            print(f"  {name:<24} записей {len(got)}"
                  + (f"; последняя {got[-1]}" if got else ""))
        else:
            print(f"  {name:<24} {got}")

    # ---------- 2. СВЕЧИ НА ВСЕХ ТАЙМФРЕЙМАХ ПЛОЩАДКИ
    print(f"\n{'-' * 78}\n2. СВЕЧИ: полная история по каждому таймфрейму площадки\n")
    venue_tfs = [t for t in ex._ex.timeframes if t in VENUE_MS]
    skipped = [t for t in ex._ex.timeframes if t not in VENUE_MS]
    series: dict[str, list[Bar]] = {}
    for res in sorted(venue_tfs, key=lambda t: VENUE_MS[t]):
        since = created if res != "1s" else now - SECOND_CAP * 1000
        bars = await fetch_all(ex, SYMBOL, res, since, now)
        series[res] = bars
        if bars:
            span = (bars[-1].open_ms - bars[0].open_ms) / 86_400_000
            print(f"  {res:<4} баров {len(bars):>7}  охват {span:>7.1f} суток  "
                  f"от {bars[0].open_ms} до {bars[-1].open_ms}"
                  + ("  ⚠ УСЕЧЕНО" if res == "1s" else ""))
        else:
            print(f"  {res:<4} баров       0  — биржа не отдала")
    if skipped:
        print(f"  пропущено (длительность непостоянна): {skipped}")

    # ---------- 3. ПРОФИЛЬ: минутные свечи как источник
    prof_tf = uni.profile_timeframe
    prof_bars = series.get(prof_tf, [])
    src = CandleWindows(SYMBOL, inst.tick_size, prof_bars, prof_tf)
    print(f"\n{'-' * 78}\n3. ИСТОЧНИК ПРОФИЛЯ: свечи {prof_tf}, {len(prof_bars)} баров\n")
    if prof_bars:
        barstore.append(uni.venue, inst.market_id, prof_tf, prof_bars)

    # ---------- 4. УРОВНИ НА ВСЕХ ТАЙМФРЕЙМАХ
    print(f"{'-' * 78}\n4. КАРТА УРОВНЕЙ: структура и ПОК на КАЖДОМ таймфрейме\n")
    print(f"  {'ТФ':<5}{'баров':>8}{'свингов':>9}{'структур':>10}{'уровней':>9}  "
          f"{'ПОК (цена)':>34}")
    for res in sorted(series, key=lambda t: VENUE_MS[t]):
        bars = series[res]
        if len(bars) < 20:
            print(f"  {res:<5}{len(bars):>8}{'—':>9}{'—':>10}{'—':>9}  баров мало")
            continue
        sw = detect_swings(bars)
        if isinstance(sw, NotReady):
            print(f"  {res:<5}{len(bars):>8}  свинги: {sw.reason}")
            continue
        scan = detect_accumulations(bars, sw, res)
        pocs: list[str] = []
        for acc in scan.closed:
            lo, hi = structure_window_ms(acc, bars, tf_ms(res))
            h = src.window(lo, hi)
            if isinstance(h, NotReady):
                continue
            poc = point_of_control(h)
            if not isinstance(poc, NotReady):
                pocs.append(f"{float(poc) * float(inst.tick_size):.6f}")
        shown = ", ".join(pocs[-4:]) if pocs else "нет"
        print(f"  {res:<5}{len(bars):>8}{len(sw.swings):>9}{len(scan.closed):>10}"
              f"{len(pocs):>9}  {shown:>34}")

    # ---------- 5. ПРИОРИТЕТ, СОГЛАСИЕ И СЕТАП — ТОЛЬКО НА ШЕСТИ ТФ КУРСА
    print(f"\n{'-' * 78}\n5. СЕТАПЫ И ТОЧКИ ВХОДА — на шести ТФ курса (§2.8)\n")
    method = {t: series[t] for t in TIMEFRAME_MS if series.get(t)}
    print(f"  считаем на: {sorted(method, key=lambda t: TIMEFRAME_MS[t])}")
    d = decide(SYMBOL, method, src, tuple(method))
    print(f"  уровней построено: {len(d.mapped)}, решений: {len(d.decisions)}, "
          f"сигналов ПП: {len(d.pp_signals)}")
    print(f"  окон профиля: построено {src.windows_built}, отказано {src.windows_refused}")
    print()
    print(card.render(d, method))

    await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
