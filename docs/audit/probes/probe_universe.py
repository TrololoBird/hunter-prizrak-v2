"""ЗОНД (FOUNDATION.md §7.4): составить вселенную. Один прогон → config/universe.toml.

Правило (§5 + решение 2.2 плана):
    вселенная = символы корпуса ∪ топ по суточному обороту, только крипто-бессрочные.

«Не-крипто» берётся не подбором тикеров, а механическим признаком самой биржи
(/fapi/v1/exchangeInfo): underlyingType == "COIN" и contractType == "PERPETUAL".
Замер 2026-08-03 по 852 символам: COIN 698, EQUITY 131, COMMODITY 8, HK_EQUITY 7,
INDEX 3, KR_EQUITY 3, PREMARKET 2.
PAXG этим признаком НЕ отсекается (underlyingType=COIN, subType=RWA), поэтому назван
явно — §5 перечисляет его дословно.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import ccxt.async_support as ccxt

# Символы, которые автор разбирает в корпусе research/prizrak_corpus/.
# Источник: имена разборов + таблица «Уровни по монетам» в
# prizrak_alts_10_overview.razbor.md.
# RES ИСКЛЮЧЁН: опознать нечем — манифест хранит только имя файла
# 37627648-CwU1oJ1HmYUI3-_N.mp4, транскрипт слышит «RSI», цен для сверки нет.
CORPUS = [
    "BTC", "ETH", "BCH", "POL", "ASTR", "AEVO", "ONDO",
    "ANKR", "ARPA", "UNI", "SAND", "CFX", "STX", "CHZ", "THETA", "SUPER", "NEO",
]

# FOUNDATION.md §5 дословно: «Не-крипто инструменты (XAU, XAG, PAXG) исключены».
NAMED_EXCLUSIONS = {"XAU", "XAG", "PAXG"}

TOP_N = 12


def is_crypto_perp(market: dict[str, object]) -> bool:
    info = market.get("info") or {}
    assert isinstance(info, dict)
    return info.get("underlyingType") == "COIN" and info.get("contractType") == "PERPETUAL"


async def main() -> None:
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    try:
        markets = await ex.load_markets()
        tickers = await ex.fetch_tickers()

        cand: dict[str, float] = {}
        excluded_tradfi = 0
        excluded_named: list[str] = []
        no_volume: list[str] = []
        for sym, m in markets.items():
            if not (m.get("active") and m.get("settle") == "USDT" and m.get("quote") == "USDT"):
                continue
            if not is_crypto_perp(m):
                excluded_tradfi += 1
                continue
            if str(m.get("base")) in NAMED_EXCLUSIONS:
                excluded_named.append(sym)
                continue
            qv = (tickers.get(sym) or {}).get("quoteVolume")
            if qv is None:
                no_volume.append(sym)  # §4.3: не подставляем ноль
                continue
            cand[sym] = float(qv)

        by_vol = sorted(cand.items(), key=lambda kv: kv[1], reverse=True)
        top = [s for s, _ in by_vol[:TOP_N]]

        corpus_syms: list[str] = []
        missing: list[str] = []
        for b in CORPUS:
            sym = f"{b}/USDT:USDT"
            (corpus_syms if sym in cand else missing).append(sym if sym in cand else b)

        chosen = list(dict.fromkeys(corpus_syms + top))
        server_day = dt.datetime.fromtimestamp(await ex.fetch_time() / 1000, dt.UTC)

        print(f"# замер (биржевое время) {server_day.isoformat()}")
        print(f"# рынков всего {len(markets)}; крипто-бессрочных с оборотом {len(cand)}")
        print(f"# отсечено не-крипто по underlyingType/contractType: {excluded_tradfi}")
        print(f"# отсечено по явному списку §5: {excluded_named}")
        print(f"# без данных об обороте (не ранжированы, не подставлен ноль): {no_volume}")
        print(f"# корпус: найдено {len(corpus_syms)}, не найдено {missing}")
        print(f"# ИТОГО {len(chosen)}")
        print()
        for s in chosen:
            src = "+".join(
                ([f"корпус"] if s in corpus_syms else []) + ([f"топ{TOP_N}"] if s in top else [])
            )
            print(f'  "{s}",  # qv_24h={cand[s]:,.0f} {src}')
    finally:
        await ex.close()


asyncio.run(main())
