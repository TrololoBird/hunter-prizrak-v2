# Количественная проверка: сколько НАКОПЛЕНИЙ XAU — артефакт выходных.
#
# Гипотеза (моя, из замера сессионности): у золота объём в СБ/ВС падает до 11%/27% от
# среднего, а цена стоит. Детектор накоплений (§2.3) ищет боковик — значит выходные
# обязаны порождать «структуры», в которых никто ничего не набирал. Это и есть та
# «микроструктурная предпосылка», о которой говорит FOUNDATION §5.
#
# КОНТРОЛЬ: тот же счёт на BTC. Если доля выходных структур у BTC такая же — гипотеза
# не о золоте, а о детекторе, и вывод про XAU не следует.
from datetime import UTC, datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
import ccxt

from hunter.accumulation import detect as detect_acc
from hunter.levels import structure_window_ms
from hunter.models import Bar
from hunter.swings import detect as detect_swings

ex = ccxt.binanceusdm()
TFS = ["15m", "1h", "4h"]


def bars_of(sym: str, tf: str) -> list[Bar]:
    raw = ex.fetch_ohlcv(sym, tf, limit=500)[:-1]
    return [Bar(open_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                close=float(r[4]), volume=float(r[5])) for r in raw]


def weekend_share(from_ms: int, to_ms: int) -> float:
    """Доля окна структуры, приходящаяся на субботу-воскресенье UTC."""
    total = to_ms - from_ms
    if total <= 0:
        return 0.0
    wknd = 0
    step = 3_600_000
    t = from_ms - from_ms % step
    while t < to_ms:
        seg = min(t + step, to_ms) - max(t, from_ms)
        if seg > 0 and datetime.fromtimestamp(t / 1000, UTC).weekday() >= 5:
            wknd += seg
        t += step
    return wknd / total


for sym, label in (("XAU/USDT:USDT", "ЗОЛОТО"), ("BTC/USDT:USDT", "БИТКОИН (контроль)")):
    print(f"\n=== {label} ===")
    tot = wk_heavy = 0
    for tf in TFS:
        bars = bars_of(sym, tf)
        sw = detect_swings(bars)
        if not hasattr(sw, "of"):
            print(f"  {tf}: свингов нет")
            continue
        scan = detect_acc(bars, sw, tf)  # type: ignore[arg-type]
        shares = []
        for acc in scan.closed:
            lo, hi = structure_window_ms(acc, bars, {"15m": 900_000, "1h": 3_600_000,
                                                     "4h": 14_400_000}[tf])
            shares.append(weekend_share(lo, hi))
        heavy = [s for s in shares if s >= 0.5]
        tot += len(shares)
        wk_heavy += len(heavy)
        avg = sum(shares) / len(shares) if shares else 0.0
        print(f"  {tf:>3}: структур {len(shares):>3}   "
              f"средняя доля выходных в окне {avg:>5.1%}   "
              f"структур, где выходные ≥ половины окна: {len(heavy)}")
    if tot:
        print(f"  ИТОГО: {wk_heavy} из {tot} структур ({wk_heavy / tot:.0%}) "
              f"наполовину и более лежат в выходных")
print("\n(календарная доля выходных = 2/7 = 28.6% — это тот уровень, вокруг которого")
print(" числа держались бы, если бы расписание на структуры не влияло)")

