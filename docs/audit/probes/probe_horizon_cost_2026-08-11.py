"""Сколько СУТОК СДЕЛОК требует горизонт — и потому чем на самом деле ограничена карта.

Повод. `--horizon-days` по умолчанию РАВНЯЛСЯ 90, и у того числа НЕ БЫЛО РЕФЕРЕНТА: оно
значится «альтернативой» в разборе `docs/audit/01-recreation-attempt.md` (A-15) и больше
нигде не обосновано. Хуже: оно противоречит собственному обоснованию в коде — докстрока
`run.needed_days` объясняет отсечение тем, что «структура, из которой цена ушла ГОД
назад, уровнем по стр. 25 уже не является», а отсекает по трём месяцам.

Вопрос стал острым 2026-08-11: суточный архив удалён, глубина REST замерена (около года),
и теперь карту ограничивает не источник данных, а наше собственное число.

По итогам этого замера умолчание переведено на 180 (`__main__.HORIZON_DAYS`): переход с
90 не стоит ни одних дополнительных суток у всех трёх символов, а структур даёт на 12-15%
больше.

⚠ Курс календарного среза не даёт ВООБЩЕ. Стр. 25 говорит об отработке уровня касанием,
а не о его старении по календарю. Значит `horizon_days` — не правило методики, а
ресурсный вентиль, и выбирать его надо ЦЕНОЙ, а не вкусом.

ЧТО МЕРЯЕТСЯ. Для каждого символа и горизонта: сколько структур попадает в карту и
сколько СУТОК СДЕЛОК под них надо иметь в кэше. Второе — и есть цена: сутки плотного
символа добираются REST-ом порядка шести минут.

⚠ Это замер ТРЕБОВАНИЯ, а не времени. Сколько из этих суток уже лежит в кэше, зонд не
спрашивает: он отвечает на вопрос «сколько нужно всего», а не «сколько осталось».

КОНТРОЛЬ. Горизонты идут по возрастанию, и число суток обязано расти вместе с ними. Если
оно не меняется — набор суток определяется не горизонтом, и мерить надо другое.

Воспроизведение:
    uv run python docs/audit/probes/probe_horizon_cost_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.run import needed_days

HORIZONS = (30, 90, 180, 365)
SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "ARPA/USDT:USDT")
BARS = 1000


async def main() -> int:
    uni = load_universe()
    ex = Exchange()
    await ex.open()
    try:
        print(f"баров на ряд {BARS}; ТФ {', '.join(uni.timeframes)}\n")
        print(f"{'символ':<20} {'горизонт':>9} {'структур':>9} {'суток сделок':>13}")
        print("-" * 56)
        totals: dict[int, int] = dict.fromkeys(HORIZONS, 0)
        for sym in SYMBOLS:
            series = {}
            for tf in uni.timeframes:
                got = await ex.fetch_closed_ohlcv(sym, tf, limit=BARS)
                if isinstance(got, NotReady):
                    print(f"{sym:<20} {tf}: {got.reason}")
                    continue
                series[tf] = got.bars
            if not series:
                continue
            for h in HORIZONS:
                days, used, _dropped, _hi = needed_days(series, h)
                totals[h] += len(days)
                print(f"{sym:<20} {h:>9} {used:>9} {len(days):>13}")
            print()

        print(f"{'ИТОГО по 3 символам':<20} {'горизонт':>9} {'':>9} {'суток сделок':>13}")
        for h in HORIZONS:
            per_symbol = totals[h] / len(SYMBOLS)
            whole = per_symbol * len(uni.symbols)
            print(f"{'':<20} {h:>9} {'':>9} {totals[h]:>13}"
                  f"   на вселенную из {len(uni.symbols)}: ~{whole:.0f} символ-суток")
        print()
        print("⚠ Цена в часах: сутки плотного символа добираются REST-ом ~6 минут,")
        print("  тонкого — заметно быстрее. Умножать надо на символ-сутки, которых НЕТ")
        print("  в кэше; кэш накапливается и добирается один раз.")
        print()
        print("КОНТРОЛЬ: число суток обязано расти с горизонтом. Не растёт — набор суток")
        print("определяется не горизонтом, и вывод о цене горизонта не следует.")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
