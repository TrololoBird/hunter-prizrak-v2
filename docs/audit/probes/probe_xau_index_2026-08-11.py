# Третья (и единственная непроверенная) интерпретация запрета FOUNDATION §5.
#
# ПРЕДПОСЫЛКА МЕТОДА. Уровень §2.2 — это ПОК профиля объёма внутри накопления: место,
# где крупный НАБИРАЛ позицию, и потому цена туда возвращается. Предпосылка в том, что
# сделки НА ЭТОЙ БИРЖЕ цену и делают.
#
# ПРОВЕРЯЕМОЕ. У товарного перпа цена ведома внешним индексом (FOUNDATION §6: Orderbook
# EWMA по спот-площадкам золота). Если перп — тень индекса, то ПОК показывает, где
# дольше стоял ИНДЕКС, а объём на Binance к рождению уровня отношения не имеет.
#
# МЕРА: расхождение баров перпа и баров индекса. Тень — расхождение около нуля.
# КОНТРОЛЬ: BTC, для которого метод писан, и у которого индекс считается по спотовым
# биржам — то есть тоже «внешний». Если у BTC расхождение такое же малое, мера ничего
# не различает и вывод не следует.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
import ccxt

ex = ccxt.binanceusdm()
ex.load_markets()


def compare(sym: str, label: str) -> None:
    mid = ex.market(sym)["id"]
    perp = ex.fetch_ohlcv(sym, "1h", limit=500)[:-1]
    idx = ex.fapiPublicGetIndexPriceKlines(
        {"pair": mid, "interval": "1h", "limit": 500})[:-1]
    by_ms = {int(r[0]): (float(r[1]), float(r[4])) for r in idx}
    diffs, rets_p, rets_i = [], [], []
    prev_p = prev_i = None
    for b in perp:
        got = by_ms.get(int(b[0]))
        if got is None:
            continue
        _io, ic = got
        pc = b[4]
        diffs.append(abs(pc - ic) / ic * 100)
        if prev_p is not None and prev_i is not None:
            rets_p.append((pc - prev_p) / prev_p)
            rets_i.append((ic - prev_i) / prev_i)
        prev_p, prev_i = pc, ic
    if not diffs:
        print(f"  {label}: пересечения баров нет")
        return
    diffs.sort()
    med = diffs[len(diffs) // 2]
    p95 = diffs[int(len(diffs) * 0.95)]
    # Доля дисперсии перпа, объяснённая индексом (R² простой линейной связи).
    n = len(rets_p)
    mp = sum(rets_p) / n
    mi = sum(rets_i) / n
    cov = sum((rets_p[i] - mp) * (rets_i[i] - mi) for i in range(n))
    vp = sum((x - mp) ** 2 for x in rets_p)
    vi = sum((x - mi) ** 2 for x in rets_i)
    r2 = (cov ** 2) / (vp * vi) if vp and vi else 0.0
    print(f"  {label:22} часов сверено {len(diffs):>4}   "
          f"медиана |перп−индекс| {med:.4f}%   95-й перцентиль {p95:.4f}%")
    print(f"  {'':22} движения перпа объяснены индексом на {r2:.2%}")


print("=== НАСКОЛЬКО ПЕРП — ТЕНЬ СВОЕГО ИНДЕКСА (бары 1ч, ~500 часов) ===")
compare("XAU/USDT:USDT", "ЗОЛОТО")
compare("BTC/USDT:USDT", "БИТКОИН (контроль)")
print("\n(100% означало бы, что торговля на этой бирже цену не двигает вовсе:")
print(" перп повторяет внешний ориентир, а ПОК — след чужого рынка, не набора позиции)")

