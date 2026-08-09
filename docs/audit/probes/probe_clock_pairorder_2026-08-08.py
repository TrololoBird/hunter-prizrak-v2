"""ЗОНД-РАЗБОР №2: отчего расходятся ПЕРВЫЙ и ВТОРОЙ замеры одной пары.

Первый разбор (probe_clock_ratelimit_2026-08-08) ОПРОВЕРГ моё объяснение: ограничитель
частоты ни при чём, rtt у замеров вплотную не выше, чем у замеров с паузой. Значит причина
расхождения К1 в чём-то другом, и её надо найти, а не заменить второй правдоподобной
историей.

ЧТО ИМЕННО ПРОВЕРЯЕТСЯ. Основной зонд строил пары так: A — замер после паузы 20 с, B —
замер СРАЗУ за ним. Разбор №1 гонял A и B РАЗНЫМИ сериями и разницы не увидел. Здесь
воспроизводится ТА ЖЕ петля, что в основном зонде: A и B чередуются внутри одной итерации.

Если расхождение вызвано ПОЛОЖЕНИЕМ в паре (первый против второго), оно проявится как
систематический сдвиг медиан A и B, а не как случайный разброс. Если медианы совпадут, а
расхождение останется, значит дело в разбросе отдельных замеров, и тогда нуль К1 негоден
по другой причине: он сравнивает шум с шумом.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_clock_pairorder_2026-08-08.py
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

N = 15
PAUSE_S = 5.0


async def run() -> tuple[list[int], list[int], list[int], list[int]]:
    ex = ccxt_async.binanceusdm({"enableRateLimit": True})

    async def fetch() -> int:
        return int(await ex.fetch_time())

    a_off: list[int] = []
    b_off: list[int] = []
    a_rtt: list[int] = []
    b_rtt: list[int] = []
    try:
        for i in range(N):
            a = await clock.measure(fetch, samples=1)
            b = await clock.measure(fetch, samples=1)
            a_off.append(a.offset_ms)
            b_off.append(b.offset_ms)
            a_rtt.append(a.rtt_ms)
            b_rtt.append(b.rtt_ms)
            if i < N - 1:
                await asyncio.sleep(PAUSE_S)
    finally:
        await ex.close()
    return a_off, b_off, a_rtt, b_rtt


def main() -> int:
    print("=" * 78)
    print(f"РАЗБОР №2: {N} пар (A после паузы {PAUSE_S:.0f} с, B сразу за A) в ОДНОЙ петле")
    print("=" * 78)
    a_off, b_off, a_rtt, b_rtt = asyncio.run(run())

    print(f"  A (первый в паре): сдвиг медиана {statistics.median(a_off):+7.0f} мс, "
          f"размах {max(a_off) - min(a_off):5d}; rtt медиана {statistics.median(a_rtt):5.0f} мс")
    print(f"  B (второй в паре): сдвиг медиана {statistics.median(b_off):+7.0f} мс, "
          f"размах {max(b_off) - min(b_off):5d}; rtt медиана {statistics.median(b_rtt):5.0f} мс")

    within = [abs(b - a) for a, b in zip(a_off, b_off, strict=True)]
    across = [abs(y - x) for x, y in zip(a_off, a_off[1:], strict=False)]
    med_within = statistics.median(within)
    med_across = statistics.median(across) if across else 0.0
    shift = statistics.median(b_off) - statistics.median(a_off)

    print(f"\n  |B − A| внутри пары:        медиана {med_within:6.1f} мс")
    print(f"  |A(i+1) − A(i)| через паузу: медиана {med_across:6.1f} мс")
    print(f"  систематический сдвиг медиан B − A: {shift:+.1f} мс")

    print("\nВЫВОД РАЗБОРА:")
    if abs(shift) > med_within * 0.5:
        print(f"  ✅ ПОЛОЖЕНИЕ В ПАРЕ ЗНАЧИМО: медианы A и B расходятся на {shift:+.1f} мс,")
        print("     то есть второй замер систематически смещён. Нуль К1 сравнивал не")
        print("     «прошло время / не прошло», а «первый в паре / второй в паре».")
    elif med_within > med_across * 2:
        print(f"  ✅ РАЗБРОС ОТДЕЛЬНОГО ЗАМЕРА ВЕЛИК: |B−A| = {med_within:.1f} мс против")
        print(f"     {med_across:.1f} мс через паузу, но систематического сдвига нет")
        print(f"     ({shift:+.1f} мс). Значит нуль К1 сравнивал ШУМ ОДНОГО ЗАМЕРА с УХОДОМ")
        print("     ЧАСОВ — величины разной природы, и такой нуль негоден по построению.")
    else:
        print(f"  ⚠ НИ ТО НИ ДРУГОЕ: |B−A| = {med_within:.1f}, через паузу {med_across:.1f},")
        print(f"     сдвиг {shift:+.1f}. Причина расхождения К1 этим разбором НЕ найдена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
