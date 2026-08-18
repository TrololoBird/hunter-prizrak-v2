"""Зонд: ПОК по МИНУТНЫМ свечам против ПОК по свечам СВОЕГО ТФ — на тех же окнах.

Вопрос владельца 2026-08-18: есть ли смысл грузить огромную историю минутных свечей,
если профиль можно считать по свечам таймфрейма анализа (они уже собраны)?

Честность сравнения: ОДНА сетка build_tv (одинаковые bottom/top = крайние цены баров
структуры своего ТФ, rows=TV_ROWS) — различается только гистограмма-источник.
Метрики: |ΔПОК| в % цены, в строках сетки, и попал ли ТФ-ПОК в зону (VAL–VAH) минутного.
"""
import asyncio
from decimal import Decimal
from statistics import median

from hunter import barstore
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.profile_source import CandleWindows
from hunter.tgbot import read_map
from hunter.volume_profile import TV_ROWS, build_tv

SYMBOLS = ["ONDO/USDT:USDT", "BTC/USDT:USDT"]


async def main() -> None:
    uni = load_universe()
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        for symbol in SYMBOLS:
            inst = ex.instrument(symbol)
            if isinstance(inst, NotReady):
                print(f"{symbol}: инструмента нет — {inst.reason}")
                continue
            got = read_map(symbol)
            if isinstance(got, NotReady):
                print(f"{symbol}: карты нет — {got.reason}")
                continue
            rows_out = []
            skipped: dict[str, int] = {}
            for z in got.zones:
                if z.kind != "level" or z.from_ms <= 0 or z.to_ms <= z.from_ms:
                    skipped["без окна"] = skipped.get("без окна", 0) + 1
                    continue
                tf_bars = barstore.load(uni.venue, inst.market_id, z.timeframe,
                                        z.from_ms, z.to_ms)
                m_bars = barstore.load(uni.venue, inst.market_id, "1m",
                                       z.from_ms, z.to_ms)
                if not tf_bars or not m_bars:
                    skipped["нет баров"] = skipped.get("нет баров", 0) + 1
                    continue
                w_tf = CandleWindows(symbol, inst.tick_size, tf_bars,
                                     z.timeframe).window(z.from_ms, z.to_ms)
                w_1m = CandleWindows(symbol, inst.tick_size, m_bars,
                                     "1m").window(z.from_ms, z.to_ms)
                if isinstance(w_tf, NotReady) or isinstance(w_1m, NotReady):
                    skipped["окно не покрыто"] = skipped.get("окно не покрыто", 0) + 1
                    continue
                bottom = Decimal(str(min(b.low for b in tf_bars)))
                top = Decimal(str(max(b.high for b in tf_bars)))
                p_tf = build_tv(w_tf, bottom, top, rows=TV_ROWS)
                p_1m = build_tv(w_1m, bottom, top, rows=TV_ROWS)
                if isinstance(p_tf, NotReady) or isinstance(p_1m, NotReady):
                    if isinstance(p_tf, NotReady):
                        key = f"ТФ-сторона: {p_tf.reason.split('—')[-1][:40]}"
                        skipped[key] = skipped.get(key, 0) + 1
                    if isinstance(p_1m, NotReady):
                        key = f"1м-сторона: {p_1m.reason.split('—')[-1][:40]}"
                        skipped[key] = skipped.get(key, 0) + 1
                    continue
                d_pct = abs(float(p_tf.poc_price - p_1m.poc_price)) \
                    / float(p_1m.poc_price) * 100
                d_rows = abs(p_tf.poc_row - p_1m.poc_row)
                in_va = float(p_1m.val_price) <= float(p_tf.poc_price) \
                    <= float(p_1m.vah_price)
                rows_out.append((z.timeframe, d_pct, d_rows, in_va,
                                 len(m_bars), len(tf_bars)))
            print(f"\n==== {symbol}: уровней с обоими профилями {len(rows_out)}, "
                  f"пропущено {skipped} ====")
            for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
                sel = [r for r in rows_out if r[0] == tf]
                if not sel:
                    continue
                pcts = sorted(r[1] for r in sel)
                rws = [r[2] for r in sel]
                same = sum(1 for r in sel if r[2] == 0)
                near = sum(1 for r in sel if r[2] <= 1)
                inva = sum(1 for r in sel if r[3])
                mb = median(r[4] for r in sel)
                tb = median(r[5] for r in sel)
                print(f"{tf}: n={len(sel)} медиана|Δ|={median(pcts):.4f}% "
                      f"макс={pcts[-1]:.3f}% строка-в-строку={same}/{len(sel)} "
                      f"±1 строка={near}/{len(sel)} ТФ-ПОК в зоне 1м={inva}/{len(sel)} "
                      f"медиана баров: 1м={mb:.0f} тф={tb:.0f} "
                      f"медиана|Δ|строк={median(rws)}")
    finally:
        await ex.close()


asyncio.run(main())
