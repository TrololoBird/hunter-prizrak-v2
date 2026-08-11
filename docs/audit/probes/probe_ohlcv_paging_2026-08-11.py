"""Пробивает ли пагинация предел ccxt в 1000 свечей — и не портит ли она ряд.

Повод. `bars_per_timeframe = 1000` выглядело выбором глубины, а было чужим потолком: ccxt
режет выдачу свечей на 1000 жёстко. Отсюда карта младших ТФ обрывалась не там, где кончается
горизонт, а там, где кончается предел библиотеки — на 5м это 3.5 суток против горизонта 180.

ЧТО МЕРЯЕТСЯ. Запрос глубже предела: сколько баров пришло, какой календарь накрыт, сколько
это стоило запросов.

КОНТРОЛЬ (обязателен по CLAUDE.md), тройной — и третий здесь главный:

  1. `ДЛИНА` — при запросе выше предела баров обязано прийти БОЛЬШЕ 1000. Ровно 1000
     означало бы, что пагинация не сработала и вернулась старая ветка;
  2. `ЦЕЛОСТНОСТЬ` — метки открытия строго возрастают, лежат на сетке ТФ, без дубликатов
     и без дыр. Склейка страниц — ровно то место, где живут дубликаты и немонотонность;
  3. `ОРАКУЛ` — последние 1000 баров длинного ряда обязаны СОВПАСТЬ ПОБАЙТОВО с обычным
     запросом на 1000 баров, сделанным отдельно. Это независимая проверка: если пагинация
     сдвигает, теряет или дублирует бары, совпадения не будет. Без этого контроля
     «пришло 3000 баров» ничего не значит — прийти могло что угодно.

⚠ Замер живой. Стоимость в весе: запрос 1000 свечей стоит 5 (ступени зашиты в ccxt как
byLimit [[99,1],[499,2],[1000,5],[10000,10]]), то есть длинный ряд — это 5 × число страниц.

Воспроизведение:
    uv run python docs/audit/probes/probe_ohlcv_paging_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter.bars import on_grid, tf_ms
from hunter.exchange import CCXT_EFFECTIVE_LIMIT, Exchange
from hunter.models import NotReady

SYMBOL = "BTC/USDT:USDT"
TF = "5m"
DEEP = 3000
"""Втрое выше предела: одной страницы мало, двух мало, значит склеек будет минимум две."""


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


async def main() -> int:
    ex = Exchange()
    try:
        await ex.open()
        step = tf_ms(TF)

        print(f"предел ccxt: {CCXT_EFFECTIVE_LIMIT} свечей за запрос")
        print(f"просим {DEEP} баров {TF} по {SYMBOL}\n")

        deep = await ex.fetch_closed_ohlcv(SYMBOL, TF, limit=DEEP)
        if isinstance(deep, NotReady):
            print(f"длинный ряд не пришёл: {deep.reason}")
            return 1
        bars = deep.bars
        span_days = (bars[-1].open_ms - bars[0].open_ms) / 86_400_000
        print(f"пришло баров: {len(bars)}")
        print(f"накрыто: {iso(bars[0].open_ms)} … {iso(bars[-1].open_ms)} "
              f"({span_days:.1f} суток)")
        print(f"для сравнения, {CCXT_EFFECTIVE_LIMIT} баров {TF} это "
              f"{CCXT_EFFECTIVE_LIMIT * step / 86_400_000:.1f} суток")
        print(f"рядов добрано не целиком: {ex.bars_pages_short}")

        # --- КОНТРОЛЬ 1 ---
        c1 = len(bars) > CCXT_EFFECTIVE_LIMIT
        print(f"\nКОНТРОЛЬ 1 (длина > предела): {len(bars)} > {CCXT_EFFECTIVE_LIMIT} — "
              f"{'ДА' if c1 else 'НЕТ'}")

        # --- КОНТРОЛЬ 2 ---
        opens = [b.open_ms for b in bars]
        dupes = len(opens) - len(set(opens))
        unsorted_at = [i for i in range(1, len(opens)) if opens[i] <= opens[i - 1]]
        off = [o for o in opens if not on_grid(o, TF)]
        holes = [i for i in range(1, len(opens)) if opens[i] - opens[i - 1] != step]
        c2 = not dupes and not unsorted_at and not off and not holes
        print(f"КОНТРОЛЬ 2 (целостность): дубликатов {dupes}, немонотонных "
              f"{len(unsorted_at)}, вне сетки {len(off)}, дыр {len(holes)} — "
              f"{'ЧИСТО' if c2 else 'ГРЯЗНО'}")

        # --- КОНТРОЛЬ 3: ОРАКУЛ ---
        plain = await ex.fetch_closed_ohlcv(SYMBOL, TF, limit=CCXT_EFFECTIVE_LIMIT)
        if isinstance(plain, NotReady):
            print(f"⚠ оракул не построен: {plain.reason} — контроль 3 не проведён")
            return 1
        tail = bars[-len(plain.bars):]
        same = [a for a, b in zip(tail, plain.bars, strict=True) if a == b]
        c3 = len(same) == len(plain.bars)
        print(f"КОНТРОЛЬ 3 (оракул: хвост длинного = обычный запрос): "
              f"совпало {len(same)} из {len(plain.bars)} — {'ДА' if c3 else 'НЕТ'}")

        print()
        if not c1:
            print("⚠ КОНТРОЛЬ 1 ПРОВАЛЕН: пагинация не сработала, вернулась старая ветка")
            return 1
        if not c2:
            print("⚠ КОНТРОЛЬ 2 ПРОВАЛЕН: склейка страниц испортила ряд")
            return 1
        if not c3:
            print("⚠ КОНТРОЛЬ 3 ПРОВАЛЕН: длинный ряд расходится с обычным запросом —")
            print("  пагинация сдвигает или теряет бары. «Пришло N баров» НЕ ЗНАЧИТ ничего.")
            return 1
        print(f"Все три контроля пройдены: глубина выросла с "
              f"{CCXT_EFFECTIVE_LIMIT * step / 86_400_000:.1f} до {span_days:.1f} суток,")
        print("ряд цел, и его хвост совпадает с независимым запросом бар в бар.")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
