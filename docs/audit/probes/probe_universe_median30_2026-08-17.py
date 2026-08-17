"""Отбор вселенной по МЕДИАНЕ суточного оборота за 30 суток. Решение владельца 2026-08-17.

Заменяет метрику прежнего отбора (`probe_universe.py`, оборот за последние 24 часа).
Повод — замер `docs/audit/frozen-rules-2026-08-17.md`: через 14 суток в топ-12 осталось
4 из 10 символов, отобранных однодневным оборотом, а два ушли на 93 и 169 места.

    вселенная = символы корпуса ∪ топ-12 по МЕДИАНЕ суточного оборота за 30 закрытых суток

Почему медиана, а не среднее: среднее по 30 суткам одиночный памп сдвигает почти так же,
как однодневный оборот, — оно делит всплеск на тридцать, но не отбрасывает. Медиана
отвечает на вопрос «сколько торгуется В ОБЫЧНЫЙ ДЕНЬ», то есть ровно на тот, ради
которого закрепляется вселенная.

Почему ЗАКРЫТЫХ: текущие сутки неполны, и их оборот тем меньше, чем раньше по UTC идёт
замер. Правило проекта §6 — считать только по закрытым барам; здесь оно даёт то же самое.

Оборот берётся ИЗ ОТВЕТА БИРЖИ (`quote asset volume`, поле 7 свечи), а не из
произведения объёма на цену: второе — наша оценка, первое — число самой биржи.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import statistics

import ccxt.async_support as ccxt

CORPUS = ["BTC", "ETH", "BCH", "POL", "ASTR", "AEVO", "ONDO", "ANKR", "ARPA", "UNI",
          "SAND", "CFX", "STX", "CHZ", "THETA", "SUPER", "NEO"]
NAMED_EXCLUSIONS = {"XAU", "XAG", "PAXG", "XAUT"}
"""§5 дословно: «Не-крипто инструменты (XAU, XAG, PAXG) исключены».

⚠ `XAUT` ДОБАВЛЕН 2026-08-17 и в §5 не назван. Это Tether Gold — то же токенизированное
золото, что и PAXG, и механическим признаком биржи оно не отсекается ровно по той же
причине (`underlyingType=COIN`, `contractType=PERPETUAL`). Проверка велась не подбором
тикеров: подтип `RWA` содержит ровно четыре рынка — CFG, MANTRA, PAXG, XAUT, — из них
золото два, а CFG и MANTRA это обычные крипто-токены платформ RWA, и отсекать их по
подтипу было бы ошибкой. Поэтому список именной, а не по подтипу.

На сегодняшний отбор это НЕ влияет: XAUT по медиане 34-й при пороге входа 12-го места.
Правка предупреждает молчаливое включение, а не исправляет состав.
"""
TOP_N = 12
DAYS = 30

CANDIDATE_MIN_24H = 5_000_000.0
"""Ниже какого оборота за сутки символ в замер не берётся. ⚠ ЭТО ГРАНИЦА ЗАМЕРА.

Не порог отбора: отбирает медиана. Это ограничение стоимости — суточные свечи просятся по
одному запросу на символ, а рынков 524. Цена ограничения названа числом в самом протоколе:
чтобы попасть в топ-12 по медиане, символу нужен обычный день порядка сотни миллионов, а
здесь отсекаются те, у кого СЕГОДНЯ меньше пяти. Пропуск возможен только у символа,
рухнувшего за сутки в двадцать раз, и такой случай виден по рейтингу 24ч отдельно.
"""

# Символы текущей вселенной меряются ВСЕГДА, независимо от порога: без них не с чем
# сравнивать прежний отбор.
UNIVERSE_NOW = [f"{b}/USDT:USDT" for b in CORPUS] + [
    "SOL/USDT:USDT", "BLESS/USDT:USDT", "ZEC/USDT:USDT", "HYPE/USDT:USDT",
    "XRP/USDT:USDT", "1000RATS/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
    "BICO/USDT:USDT", "BNB/USDT:USDT",
]


def is_crypto_perp(market: dict) -> bool:
    info = market.get("info") or {}
    return info.get("underlyingType") == "COIN" and info.get("contractType") == "PERPETUAL"


async def main() -> None:
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    try:
        markets = await ex.load_markets()
        tickers = await ex.fetch_tickers()
        server_ms = await ex.fetch_time()

        pool: dict[str, float] = {}
        skipped_cheap = 0
        for sym, m in markets.items():
            if not (m.get("active") and m.get("settle") == "USDT" and m.get("quote") == "USDT"):
                continue
            if not is_crypto_perp(m) or str(m.get("base")) in NAMED_EXCLUSIONS:
                continue
            qv = (tickers.get(sym) or {}).get("quoteVolume")
            if qv is None:
                continue
            if float(qv) < CANDIDATE_MIN_24H and sym not in UNIVERSE_NOW:
                skipped_cheap += 1
                continue
            pool[sym] = float(qv)

        rank24 = {s: i + 1 for i, (s, _) in enumerate(
            sorted(((s, v) for s, v in pool.items()), key=lambda kv: -kv[1]))}

        med: dict[str, float] = {}
        short: list[str] = []
        series: dict[str, list[float]] = {}
        for sym in pool:
            raw = await ex.fapiPublicGetKlines({
                "symbol": markets[sym]["id"], "interval": "1d", "limit": DAYS + 1})
            # Последняя свеча — ТЕКУЩИЕ сутки, они неполны и в медиану не идут (§6).
            closed = raw[:-1][-DAYS:]
            if len(closed) < DAYS:
                # Молодой рынок: истории меньше окна. Ноль НЕ подставляется — символ
                # называется отдельно, иначе «мало истории» стало бы «мало оборота».
                short.append(f"{sym}({len(closed)})")
                continue
            series[sym] = [float(k[7]) for k in closed]
            med[sym] = statistics.median(series[sym])

        order = sorted(med.items(), key=lambda kv: -kv[1])
        rank_med = {s: i + 1 for i, (s, _) in enumerate(order)}
        top = [s for s, _ in order[:TOP_N]]

        day = dt.datetime.fromtimestamp(server_ms / 1000, dt.UTC)
        print(f"# замер (биржевое время) {day.isoformat()}")
        print(f"# метрика: МЕДИАНА суточного оборота за {DAYS} закрытых суток")
        print(f"# рынков крипто-бессрочных с расчётом USDT: {len(pool) + skipped_cheap}")
        print(f"# из них измерено: {len(med)}; отсечено порогом {CANDIDATE_MIN_24H:,.0f}$"
              f" за 24ч: {skipped_cheap}; истории меньше {DAYS} суток: {len(short)}")
        if short:
            print(f"# короткая история: {', '.join(short)}")
        print(f"# порог входа в топ-{TOP_N} по медиане: {order[TOP_N - 1][1]:,.0f}$")
        print()
        print(f"{'символ':22}{'медиана 30д, $':>18}{'место мед':>11}{'место 24ч':>11}"
              f"{'оборот 24ч, $':>18}")
        for sym, m in order[:40]:
            print(f"{sym:22}{m:>18,.0f}{rank_med[sym]:>11}{rank24.get(sym, 0):>11}"
                  f"{pool[sym]:>18,.0f}")
        print()
        print("=== ТЕКУЩАЯ ВСЕЛЕННАЯ ПО НОВОЙ МЕТРИКЕ ===")
        for sym in UNIVERSE_NOW:
            if sym in med:
                mark = "корпус" if sym.split("/")[0] in CORPUS else "по обороту"
                print(f"{sym:22}{med[sym]:>18,.0f}{rank_med[sym]:>11}"
                      f"{rank24.get(sym, 0):>11}   {mark}")
            else:
                print(f"{sym:22}{'не измерен':>18}")
        print()
        corpus_syms = [f"{b}/USDT:USDT" for b in CORPUS if f"{b}/USDT:USDT" in pool]
        chosen = list(dict.fromkeys(corpus_syms + top))
        print(f"# корпус: {len(corpus_syms)}; топ-{TOP_N} по медиане: "
              f"{', '.join(s.split('/')[0] for s in top)}")
        print(f"# ИТОГО {len(chosen)}")
        for s in chosen:
            print(f'  "{s}",')
        print()
        gone = [s for s in UNIVERSE_NOW if s not in chosen]
        new = [s for s in chosen if s not in UNIVERSE_NOW]
        print(f"# уходит из вселенной ({len(gone)}): "
              f"{', '.join(s.split('/')[0] for s in gone) or 'никто'}")
        print(f"# приходит ({len(new)}): "
              f"{', '.join(s.split('/')[0] for s in new) or 'никто'}")

        # --- КОНТРОЛЬ: устойчивее ли медиана однодневного оборота ------------------
        #
        # «Медиана стабильнее» — утверждение, а не факт, и проверяется оно на ТЕХ ЖЕ
        # данных: окно 30 суток делится пополам, и каждая метрика отбирает топ-12 по
        # первой половине и по второй. Совпадение двух списков и есть устойчивость.
        # Однодневная метрика при этом берётся так же, как её брал прежний отбор, —
        # оборот ОДНИХ суток (последних в своей половине).
        def top_by(score: dict[str, float]) -> set[str]:
            return {s for s, _ in sorted(score.items(), key=lambda kv: -kv[1])[:TOP_N]}

        first_med = {s: statistics.median(v[:DAYS // 2]) for s, v in series.items()}
        second_med = {s: statistics.median(v[DAYS // 2:]) for s, v in series.items()}
        first_day = {s: v[DAYS // 2 - 1] for s, v in series.items()}
        second_day = {s: v[-1] for s, v in series.items()}

        med_keep = len(top_by(first_med) & top_by(second_med))
        day_keep = len(top_by(first_day) & top_by(second_day))
        print()
        print("=== КОНТРОЛЬ УСТОЙЧИВОСТИ (одни и те же 30 суток, разбитые пополам) ===")
        print(f"  медиана 15 суток → медиана следующих 15: совпало {med_keep} из {TOP_N}")
        print(f"  оборот одних суток → оборот суток через 15: совпало {day_keep} из {TOP_N}")
        print(f"  разница: {med_keep - day_keep:+d} символа из {TOP_N}")
        print(f"  выпали у медианы: "
              f"{', '.join(s.split('/')[0] for s in top_by(first_med) - top_by(second_med)) or 'никто'}")
        print(f"  выпали у однодневной: "
              f"{', '.join(s.split('/')[0] for s in top_by(first_day) - top_by(second_day)) or 'никто'}")
    finally:
        await ex.close()


asyncio.run(main())
