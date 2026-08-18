"""Диагностика расхождения скалярного и векторного окна: стадия за стадией."""
import asyncio
import bisect

import numpy as np

from hunter import barstore
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady, bin_index, tick_scale
from hunter.profile_source import MAX_BINS_PER_BAR

SYMBOL = "ONDO/USDT:USDT"
TF = "5m"
FROM_MS, TO_MS = 1772272800000, 1772286600000


async def main() -> None:
    uni = load_universe()
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        inst = ex.instrument(SYMBOL)
        assert not isinstance(inst, NotReady)
        tick = inst.tick_size
        bars = sorted(barstore.load(uni.venue, inst.market_id, TF, FROM_MS, TO_MS),
                      key=lambda b: b.open_ms)
        keys = [b.open_ms for b in bars]
        i = bisect.bisect_left(keys, FROM_MS)
        j = bisect.bisect_left(keys, TO_MS)
        chunk = bars[i:j]
        print("баров в окне:", len(chunk))

        # стадия 1: бинирование
        scale, step = tick_scale(tick)
        k0_old = [bin_index(b.low, tick) for b in chunk]
        k1_old = [bin_index(b.high, tick) for b in chunk]
        lows = np.array([b.low for b in chunk], dtype=np.float64)
        highs = np.array([b.high for b in chunk], dtype=np.float64)
        k0_new = (np.floor(lows * scale + 0.5).astype(np.int64) // step).tolist()
        k1_new = (np.floor(highs * scale + 0.5).astype(np.int64) // step).tolist()
        print("бины совпали:", k0_old == k0_new and k1_old == k1_new)

        # стадия 2: доли
        share_old = [b.volume / (k1 - k0 + 1)
                     for b, k0, k1 in zip(chunk, k0_old, k1_old)
                     if k1 - k0 + 1 <= MAX_BINS_PER_BAR]
        vols = np.array([b.volume for b in chunk], dtype=np.float64)
        widths = np.array(k1_new, dtype=np.int64) - np.array(k0_new, dtype=np.int64) + 1
        keep = widths <= MAX_BINS_PER_BAR
        share_new = (vols[keep] / widths[keep]).tolist()
        same_share = all(a.hex() == b.hex() for a, b in zip(share_old, share_new))
        print("доли совпали:", same_share, "точек:", len(share_old), len(share_new))

        # стадия 3: разностный массив
        k_lo = min(k0_old)
        k_hi = max(k1_old)
        width = k_hi - k_lo + 2
        diff_old = [0.0] * width
        for k0, k1, share in zip(k0_old, k1_old, share_old):
            diff_old[k0 - k_lo] += share
            diff_old[k1 - k_lo + 1] -= share
        k0a = np.array(k0_new, dtype=np.int64)[keep]
        k1a = np.array(k1_new, dtype=np.int64)[keep]
        sh = vols[keep] / widths[keep]
        d_new = np.zeros(width, dtype=np.float64)
        edge_idx = np.empty(2 * k0a.size, dtype=np.int64)
        edge_val = np.empty(2 * k0a.size, dtype=np.float64)
        edge_idx[0::2] = k0a - k_lo
        edge_idx[1::2] = k1a - k_lo + 1
        edge_val[0::2] = sh
        edge_val[1::2] = -sh
        np.add.at(d_new, edge_idx, edge_val)
        bad = [x for x in range(width) if diff_old[x] != d_new[x]]
        print("разностный массив: несовпавших ячеек", len(bad), "из", width)
        if bad:
            x = bad[0]
            print("  первая:", x, float(diff_old[x]).hex(), float(d_new[x]).hex())

        # стадия 4: префиксная сумма (на СТАРОМ diff, чтобы изолировать стадию)
        run_old = []
        run = 0.0
        for idx in range(width - 1):
            run += diff_old[idx]
            run_old.append(run)
        run_new = np.cumsum(np.array(diff_old, dtype=np.float64)[: width - 1]).tolist()
        bad4 = [x for x in range(width - 1) if run_old[x] != run_new[x]]
        print("префиксная сумма: несовпавших", len(bad4), "из", width - 1)
        if bad4:
            x = bad4[0]
            print("  первая:", x, run_old[x].hex(), run_new[x].hex())

        # стадия 5: qty_seen
        q_old = 0.0
        for b, k0, k1 in zip(chunk, k0_old, k1_old):
            if k1 - k0 + 1 <= MAX_BINS_PER_BAR:
                q_old += b.volume
        q_new = sum(vols[keep].tolist())
        print("qty_seen:", q_old.hex(), q_new.hex(), q_old == q_new)
    finally:
        await ex.close()


asyncio.run(main())
