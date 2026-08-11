"""Через сколько после границы бара биржа отдаёт ЗАКРЫТУЮ свечу — прямой замер (задача Ж-1).

Повод. `exchange.POLL_OFFSET_S = 3.0` — отступ от границы ТФ до опроса баров, и его
докстрока сама признаёт: «⚠ ЧИСЛО НЕ ЗАМЕРЕНО — это задание Ж-1». Три секунды взяты как
ВЕРХНЯЯ оценка по косвенному свидетельству: задержка флага `x=true` в потоке kline, по
сообщению сотрудника Binance, доходит до 3 с.

Косвенное свидетельство — не замер. Здесь величина меряется прямо.

КАК. Ждём границу минутного бара. Сразу после неё опрашиваем `fetch_ohlcv` каждые
`STEP_S` секунд и смотрим, когда в ответе ВПЕРВЫЕ появится бар с меткой открытия РАВНОЙ
ГРАНИЦЕ. Разница между его появлением и границей и есть искомая задержка.

⚠ ПОЧЕМУ ИМЕННО СЛЕДУЮЩИЙ БАР, А НЕ ЗАКРЫВШИЙСЯ. Первая редакция зонда ждала появления
бара предыдущей минуты — и КОНТРОЛЬ ЕЁ ОПРОВЕРГ: этот бар виден и ДО границы, потому что
`fetch_ohlcv` отдаёт текущую, ещё не закрытую свечу последней. Зонд мерил бы появление
незакрытого бара, то есть сетевой RTT (замер тогда дал 0.56 с — ровно RTT), и вывод
«отступ завышен впятеро» был бы ложным.

Признак закрытия у нас тот же, что в бою (`bars.closed_only`): бар считается закрытым,
когда существует БОЛЕЕ ПОЗДНИЙ. Значит мерить надо появление бара границы.

⚠ В замер входит и сеть: измеряется момент, когда бар оказался У НАС, а не когда он
появился на бирже. Для выбора отступа это верная величина — отступ и нужен, чтобы
опрос ЗАСТАВАЛ бар, — но называть её «задержкой биржи» нельзя.

КОНТРОЛЬ (обязателен по CLAUDE.md). Печатается ещё и результат опроса ДО границы: там
предыдущего бара быть НЕ ДОЛЖНО. Если он есть — прибор видит бар раньше, чем тот
закрылся, и всё остальное меряет не то. Плюс печатается сетевая задержка отдельным
столбцом: если она сопоставима с найденным отступом, вывод о бирже не следует.

Минутный ТФ взят потому, что границ у него много: за десять минут набирается десять
замеров, тогда как на 5м пришлось бы ждать час.

Воспроизведение:
    uv run python docs/audit/probes/probe_poll_offset_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import ccxt.async_support as ccxt

SYMBOL = "BTC/USDT:USDT"
MINUTE_MS = 60_000
ROUNDS = 8
"""Сколько границ минуты пронаблюдать. Восемь — чтобы медиана и максимум не стояли на
двух точках; больше упирается в терпение, замер идёт в реальном времени."""
STEP_S = 0.25
"""Шаг опроса после границы. Мельче — упирается в сеть (RTT здесь ~0.3 с) и меряет её,
а не биржу; крупнее — огрубляет ответ."""
GIVE_UP_S = 15.0


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%H:%M:%S")


async def one_round(ex: ccxt.binanceusdm, n: int) -> tuple[float, float, bool] | None:
    """Один замер: (задержка_с, сетевая_задержка_с, контроль_чист)."""
    now = int(await ex.fetch_time())
    boundary = (now // MINUTE_MS + 1) * MINUTE_MS

    # КОНТРОЛЬ: до границы бара С МЕТКОЙ ГРАНИЦЫ быть не должно — минута ещё не началась.
    # Если он есть, часы разошлись с биржей и всё остальное меряет не то.
    ahead = await ex.fetch_ohlcv(SYMBOL, "1m", limit=3)
    control_clean = not any(int(c[0]) >= boundary for c in ahead)

    await asyncio.sleep(max(0.0, (boundary - int(await ex.fetch_time())) / 1000))

    waited = 0.0
    while waited < GIVE_UP_S:
        t0 = time.perf_counter()
        try:
            candles = await ex.fetch_ohlcv(SYMBOL, "1m", limit=3)
        except ccxt.NetworkError as e:
            # ⚠ Разовый сетевой отказ не должен ронять замер — но и молчать о нём нельзя:
            # он входит в ожидание и потому завышает ответ. Печатается тут же.
            rtt = time.perf_counter() - t0
            waited += rtt
            print(f"    (сетевой отказ на {waited:.2f} с: {type(e).__name__} — ждём дальше)")
            continue
        rtt = time.perf_counter() - t0
        if any(int(c[0]) >= boundary for c in candles):
            print(f"  замер {n}: граница {iso(boundary)} — бар получен через "
                  f"{waited + rtt:5.2f} с (сеть {rtt:.2f} с), "
                  f"контроль {'чист' if control_clean else '⚠ ГРЯЗЕН'}")
            return waited + rtt, rtt, control_clean
        await asyncio.sleep(STEP_S)
        waited += STEP_S + rtt
    print(f"  замер {n}: граница {iso(boundary)} — бар НЕ ПРИШЁЛ за {GIVE_UP_S} с")
    return None


async def main() -> int:
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    try:
        await ex.load_markets()
        print(f"символ {SYMBOL}, ТФ 1м, границ {ROUNDS}, шаг опроса {STEP_S} с")
        print("проверяемое число: exchange.POLL_OFFSET_S = 3.0 с\n")
        got: list[tuple[float, float, bool]] = []
        lost = 0
        for n in range(1, ROUNDS + 1):
            try:
                r = await one_round(ex, n)
            except ccxt.NetworkError as e:
                # Отказ ВНЕ окна ожидания (сведение часов, контрольный опрос) замер не
                # искажает — эта граница просто пропускается. Пропуски считаются: без
                # счёта «замеров 5 из 8» читалось бы как «прибор так решил».
                lost += 1
                print(f"  замер {n}: пропущен — {type(e).__name__}")
                continue
            if r:
                got.append(r)
        if not got:
            print("\nни одного замера — вывод не следует")
            return 1
        if lost:
            print(f"\n⚠ границ пропущено по сети: {lost} из {ROUNDS}")
        delays = [d for d, _, _ in got]
        rtts = [r for _, r, _ in got]
        dirty = sum(1 for _, _, c in got if not c)

        print(f"\nзамеров {len(got)} из {ROUNDS}")
        print(f"задержка до получения бара: медиана {statistics.median(delays):.2f} с, "
              f"мин {min(delays):.2f}, макс {max(delays):.2f}")
        print(f"из неё сеть:                медиана {statistics.median(rtts):.2f} с, "
              f"макс {max(rtts):.2f}")
        print(f"контроль «бар не виден до границы»: "
              f"{'ПРОЙДЕН на всех' if dirty == 0 else f'⚠ ПРОВАЛЕН на {dirty}'}")
        print()
        if dirty:
            print("⚠ Контроль провален: прибор видит бар раньше закрытия. Вывод об")
            print("  отступе НЕ СЛЕДУЕТ — сначала чинить прибор.")
        else:
            print(f"Отступ 3.0 с против измеренного максимума {max(delays):.2f} с:")
            print("  запас есть — значит число завышено, но не опасно. Уменьшать его")
            print("  стоит только вместе с наблюдением за счётчиком `poll_late`, потому")
            print("  что максимум восьми замеров — не максимум рынка.")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
