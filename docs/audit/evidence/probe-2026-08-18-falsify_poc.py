"""Контроль к замеру ПОК-ТФ против ПОК-1м: ширина VA, сетка, прижатый объём, ничьи."""
import asyncio, random
from decimal import Decimal
from statistics import median

from hunter import barstore
from hunter.bars import tf_ms
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.profile_source import CandleWindows
from hunter.tgbot import read_map
from hunter.volume_profile import TV_ROWS, build_tv

SYMBOLS = ["ONDO/USDT:USDT", "BTC/USDT:USDT"]
LIMIT = 400


async def main() -> None:
    uni = load_universe()
    ex = Exchange(uni.venue)
    await ex.open()
    rnd = random.Random(1)
    try:
        for symbol in SYMBOLS:
            inst = ex.instrument(symbol)
            got = read_map(symbol)
            if isinstance(inst, NotReady) or isinstance(got, NotReady):
                print(symbol, "нет данных"); continue
            ok, tie, shifted = [], [], []
            n = 0
            for z in got.zones:
                if z.kind != "level" or z.from_ms <= 0 or z.to_ms <= z.from_ms:
                    continue
                if n >= LIMIT: break
                tf_bars = barstore.load(uni.venue, inst.market_id, z.timeframe, z.from_ms, z.to_ms)
                m_bars = barstore.load(uni.venue, inst.market_id, "1m", z.from_ms, z.to_ms)
                if not tf_bars or not m_bars: continue
                n += 1
                cw_tf = CandleWindows(symbol, inst.tick_size, tf_bars, z.timeframe)
                cw_1m = CandleWindows(symbol, inst.tick_size, m_bars, "1m")
                w_tf = cw_tf.window(z.from_ms, z.to_ms)
                w_1m = cw_1m.window(z.from_ms, z.to_ms)
                if isinstance(w_tf, NotReady) or isinstance(w_1m, NotReady): continue
                bottom = Decimal(str(min(b.low for b in tf_bars)))
                top = Decimal(str(max(b.high for b in tf_bars)))
                # КОНТРОЛЬ ПОКРЫТИЯ: сколько времени ТФ-бары захватывают сверх окна
                span_tf = (max(b.open_ms for b in tf_bars) + tf_ms(z.timeframe)) - min(b.open_ms for b in tf_bars)
                over = (span_tf - (z.to_ms - z.from_ms)) / (z.to_ms - z.from_ms)
                p_tf = build_tv(w_tf, bottom, top, rows=TV_ROWS)
                p_1m = build_tv(w_1m, bottom, top, rows=TV_ROWS)
                if isinstance(p_tf, NotReady) or isinstance(p_1m, NotReady):
                    ticks = int((top - bottom) / inst.tick_size)
                    tie.append((z.timeframe, ticks, len(tf_bars), over))
                    continue
                rb = p_1m.rows_built
                val_row = int((p_1m.val_price - bottom) / (top - bottom) * rb)
                vah_row = int((p_1m.vah_price - bottom) / (top - bottom) * rb)
                va_rows = vah_row - val_row + 1
                # КОНТРОЛЬ 2: случайная строка сетки вместо ТФ-ПОК
                rr = rnd.randrange(rb)
                rand_in_va = val_row <= rr <= vah_row
                rand_same = rr == p_1m.poc_row
                ok.append((z.timeframe, abs(p_tf.poc_row - p_1m.poc_row), rb, va_rows,
                           va_rows / rb, rand_in_va, rand_same,
                           p_1m.clamped_volume / p_1m.total_volume,
                           p_tf.clamped_volume / p_tf.total_volume, over,
                           abs(float(p_tf.poc_price - p_1m.poc_price)) / float(p_1m.poc_price) * 100))
            print(f"\n==== {symbol}: посчитано {len(ok)}, ничьих {len(tie)} (из {n}) ====")
            for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
                s = [r for r in ok if r[0] == tf]
                t = [r for r in tie if r[0] == tf]
                if not s and not t: continue
                if s:
                    print(f"{tf}: n={len(s)} ничьих={len(t)} медиана строк сетки={median(r[2] for r in s):.0f} "
                          f"медиана ширины VA={median(r[3] for r in s):.0f} строк "
                          f"({median(r[4] for r in s)*100:.0f}% сетки) "
                          f"ТФ-ПОК в VA={sum(1 for r in s if r[1] and True and (r[1] is not None) and abs(r[1])>=0)}/{len(s)} "
                          f"СЛУЧАЙНАЯ строка в VA={sum(1 for r in s if r[5])}/{len(s)} "
                          f"случайная строка-в-строку={sum(1 for r in s if r[6])}/{len(s)} "
                          f"прижато 1м={median(r[7] for r in s)*100:.3f}% тф={median(r[8] for r in s)*100:.3f}% "
                          f"перебор окна ТФ={median(r[9] for r in s)*100:.1f}%")
                else:
                    print(f"{tf}: считаемых нет, ничьих {len(t)}")
                if t:
                    print(f"    ничьи {tf}: медиана тиков диапазона={median(r[1] for r in t):.0f} "
                          f"баров ТФ={median(r[2] for r in t):.0f}")
                if s:
                    print(f"    считаемые {tf}: медиана тиков диапазона="
                          f"{median(r[2] for r in s):.0f} строк сетки")
    finally:
        await ex.close()


asyncio.run(main())
