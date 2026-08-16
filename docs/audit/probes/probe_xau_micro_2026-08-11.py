# СОБСТВЕННАЯ проверка: обоснован ли запрет FOUNDATION §5 на XAU.
# Утверждение документа: «микроструктурные предпосылки метода не выполняются».
# Метод (§2.2) строит уровень как ПОК профиля объёма по РЕАЛЬНЫМ сделкам внутри
# накопления. Значит проверяемое: достаточно ли сделок, чтобы профиль вообще имел
# форму, и не рвётся ли торговля так, что «накопление» окажется артефактом расписания.
#
# КОНТРОЛЬ: те же величины на BTC (эталон толстого) и ASTR (самый тонкий символ
# боевой вселенной, замер CLAUDE.md). Число без сравнения ничего не значит.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
import ccxt

ex = ccxt.binanceusdm()
ex.load_markets()

SYMS = ["XAU/USDT:USDT", "XAG/USDT:USDT", "PAXG/USDT:USDT",
        "BTC/USDT:USDT", "ASTR/USDT:USDT"]

print("=== 1. ОБОРОТ И ТИК (тикер биржи за 24ч) ===")
print(f"{'символ':18} {'оборот USDT/сут':>18} {'сделок/сут':>12} {'тик':>10} {'цена':>10}")
rows = {}
for s in SYMS:
    t = ex.fetch_ticker(s)
    m = ex.market(s)
    tick = m["precision"]["price"]
    qv = t.get("quoteVolume") or 0
    cnt = (t.get("info") or {}).get("count")
    rows[s] = (qv, tick, t["last"])
    print(f"{s:18} {qv:>18,.0f} {str(cnt or '—'):>12} {str(tick):>10} {t['last']:>10}")

btc_qv = rows["BTC/USDT:USDT"][0]
print("\nво сколько раз тоньше BTC:")
for s in SYMS:
    if s != "BTC/USDT:USDT" and rows[s][0]:
        print(f"  {s:18} в {btc_qv / rows[s][0]:>8,.0f} раз")

print("\n=== 2. ПЛОТНОСТЬ СДЕЛОК: сколько рынка накрывает 1000 сделок ===")
print("(для профиля объёма это прямой предел разрешения: чем реже сделки,")
print(" тем грубее гистограмма цены внутри накопления)")
for s in SYMS:
    tr = ex.fetch_trades(s, limit=1000)
    if len(tr) < 2:
        print(f"  {s:18} сделок в ответе {len(tr)} — замер невозможен")
        continue
    span_s = (tr[-1]["timestamp"] - tr[0]["timestamp"]) / 1000
    per_h = len(tr) * 3600 / max(span_s, 1e-9)
    prices = {t["price"] for t in tr}
    print(f"  {s:18} 1000 сделок = {span_s:>9,.0f} с рынка"
          f"   ≈{per_h:>10,.0f} сделок/час   различных цен в них: {len(prices)}")

print("\n=== 3. РАЗРЫВЫ ТОРГОВЛИ: бары 1ч с НУЛЕВЫМ объёмом за 30 суток ===")
print("(крипта торгуется непрерывно; у товарного перпа возможны сессии и выходные —")
print(" накопление, собранное из тишины, было бы артефактом расписания, а не рынка)")
for s in SYMS:
    ohlcv = ex.fetch_ohlcv(s, "1h", limit=720)
    zero = [b for b in ohlcv if b[5] == 0]
    tiny = [b for b in ohlcv if 0 < b[5] < 1e-9]
    flat = [b for b in ohlcv if b[2] == b[3]]  # high == low: цена не двигалась вовсе
    print(f"  {s:18} баров {len(ohlcv):>4}   нулевой объём {len(zero):>4}"
          f"   плоских (high==low) {len(flat):>4}")

