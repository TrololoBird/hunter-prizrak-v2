"""ЗОНД-РАЗБОР: почему нуль К1 оказался в 18 раз БОЛЬШЕ настоящего замера.

Прогон probe_clock_questions_2026-08-08 дал:
    пары с паузой 20 с — медиана |уход|/(rtt/2) = 0.021
    пары вплотную      — медиана 0.384
То есть замеры, между которыми НЕ ПРОШЛО ВРЕМЕНИ, расходятся сильнее, чем те, между
которыми прошло двадцать секунд. Это противоречит смыслу нуля, и вердикт К1 не вынесен.

⚠ У меня есть правдоподобное объяснение — ограничитель частоты ccxt спит ВНУТРИ окна
замера, — и ровно поэтому его надо проверить, а не рассказать. CLAUDE.md: ноль (и любая
аномалия) с красивой причиной опаснее всего, потому что не выглядит дефектом.

ГИПОТЕЗА РАЗБОРА. При `enableRateLimit=True` второй подряд запрос ждёт своей очереди, и
это ожидание попадает между t0 и t1. Тогда середина окна `(t0+t1)/2` смещается, а вместе с
ней и оценка сдвига. Если так, у «вплотную» замеров rtt обязан быть СИСТЕМАТИЧЕСКИ больше.

ПРОВЕРКА. Четыре набора по 12 замеров: {с паузой, вплотную} × {ограничитель включён,
выключен}. Сравниваются медианы rtt и разброс оценок сдвига.

⚠ Ограничитель выключается ТОЛЬКО в этом зонде и только для двенадцати запросов к
публичному `fetch_time`. Это осознанный расход лимита ради разбора; в боевом коде он
остаётся включённым.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_clock_ratelimit_2026-08-08.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

import ccxt.async_support as ccxt_async

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import clock  # noqa: E402

N = 12
PAUSE_S = 3.0


async def series(rate_limit: bool, spaced: bool) -> tuple[list[int], list[int]]:
    ex = ccxt_async.binanceusdm({"enableRateLimit": rate_limit})

    async def fetch() -> int:
        return int(await ex.fetch_time())

    offsets: list[int] = []
    rtts: list[int] = []
    try:
        for i in range(N):
            s = await clock.measure(fetch, samples=1)
            offsets.append(s.offset_ms)
            rtts.append(s.rtt_ms)
            if spaced and i < N - 1:
                await asyncio.sleep(PAUSE_S)
    finally:
        await ex.close()
    return offsets, rtts


def main() -> int:
    print("=" * 78)
    print(f"РАЗБОР: по {N} замеров в четырёх режимах")
    print("=" * 78)

    rows: list[tuple[str, list[int], list[int]]] = []
    for rl in (True, False):
        for sp in (True, False):
            name = f"{'с паузой' if sp else 'вплотную':>9}, ограничитель {'ВКЛ' if rl else 'ВЫКЛ'}"
            offs, rtts = asyncio.run(series(rl, sp))
            rows.append((name, offs, rtts))
            print(f"  {name}: rtt медиана {statistics.median(rtts):6.0f} мс "
                  f"(min {min(rtts)}, max {max(rtts)}); "
                  f"сдвиг медиана {statistics.median(offs):+7.0f} мс, "
                  f"размах {max(offs) - min(offs):5d} мс")

    print()
    by = {name: (statistics.median(r), max(o) - min(o)) for name, o, r in rows}
    tight_on = by[f"{'вплотную':>9}, ограничитель ВКЛ"]
    tight_off = by[f"{'вплотную':>9}, ограничитель ВЫКЛ"]
    spaced_on = by[f"{'с паузой':>9}, ограничитель ВКЛ"]

    print("ВЫВОД РАЗБОРА:")
    if tight_on[0] > spaced_on[0] * 1.5:
        print(f"  ✅ ГИПОТЕЗА ПОДТВЕРЖДЕНА: у замеров вплотную rtt медиана {tight_on[0]:.0f} мс")
        print(f"     против {spaced_on[0]:.0f} мс у замеров с паузой — ожидание ограничителя")
        print("     действительно попадает ВНУТРЬ окна замера и портит оценку сдвига.")
    elif tight_off[0] < tight_on[0] * 0.67:
        print(f"  ✅ ГИПОТЕЗА ПОДТВЕРЖДЕНА ИНАЧЕ: с выключенным ограничителем rtt вплотную")
        print(f"     {tight_off[0]:.0f} мс против {tight_on[0]:.0f} мс с включённым.")
    else:
        print("  ⚠ ГИПОТЕЗА НЕ ПОДТВЕРДИЛАСЬ: rtt замеров вплотную не выше, чем у замеров с")
        print("     паузой. Значит причина расхождения К1 в другом, и объяснение про")
        print("     ограничитель следует ОТБРОСИТЬ, а не оставить как правдоподобное.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
