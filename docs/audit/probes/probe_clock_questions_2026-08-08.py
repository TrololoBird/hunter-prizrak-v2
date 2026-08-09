"""ЗОНД фазы 5 обзора «часы и сдвиг биржи»: гипотезы К1, К2, К3.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-clock.md,
sha256 bb12eea0001ecdef6c5c513a24395bd6152f1a6d185f1fd43d578a5ff4aff3a6
(форма с LF; проверять через `git show`, а не по рабочей копии — см. сквозной дефект CRLF).

К1  значим ли уход сдвига между сведениями на фоне точности замера;
К2  устойчивее ли регистр из восьми накопленных, чем очередь из пяти;
К3  расходится ли наш симметричный порог с несимметричным правилом биржи.

## Чем этот зонд отличается от прежних: КОПИИ БОЕВОГО КОДА В НЁМ НЕТ

Каждый отдельный замер берётся вызовом `clock.measure(fetch, samples=1)` — той самой
функции, что работает в проде. Прежние зонды проекта держали копию расчёта и обязаны были
доказывать, что копия верна; здесь доказывать нечего, потому что копии нет.

⚠ Побочное следствие: `clock.measure` вызывает `set_sync`, то есть меняет глобальное
состояние модуля. Для зонда это безвредно (процесс отдельный), но названо здесь, а не
обойдено молчанием.

## Сеть

Только публичный read-only `fetch_time` Binance USD-M через ccxt — тот же эндпоинт, что
использует прод. Ключей, подписей и торговли нет. Два запроса на итерацию, пауза между
итерациями; при SAMPLES=50 и PAUSE_S=20 это 100 запросов примерно за 17 минут.

## Контроли, без которых числа недействительны

* К1 — нуль обязан дать ЗАМЕТНО МЕНЬШЕЕ отношение: пары «вплотную» отличаются от пар «с
  паузой» только прошедшим временем. Если отношения совпадут, наблюдаемое не уход часов,
  а шум сети, и числа К1 недействительны;
* К2 — нуль (случайный выбор вместо лучшего по rtt) обязан дать БОЛЬШИЙ размах. Если не
  даст, выбор по наименьшей задержке не работает вовсе;
* К3 — зона обязана быть достижима: те же замеры со сдвигом −2000 мс обязаны в неё попасть;
* сводка отказов: сколько запросов не удалось, печатается отдельно. Отказ сети — не повод
  молча укоротить серию.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_clock_questions_2026-08-08.py
"""

from __future__ import annotations

import asyncio
import random
import statistics
import sys
from pathlib import Path

import ccxt.async_support as ccxt_async

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import clock  # noqa: E402

SAMPLES = 50
PAUSE_S = 20.0
"""Серия обязана покрыть интервал, сопоставимый с периодом пересведения (900 с), иначе К1
меряет не то, ради чего заведена. 50 × 20 = 1000 с."""

QUEUE = 5
REGISTER = 8
"""Размер очереди — наш; размер регистра — из NTP (восемь ступеней, RFC 5905)."""

NULL_SEED = 20260808

K1_NOISE = 1.0
K1_SIGNIFICANT = 3.0
K2_BETTER = 0.67
K2_NO_GAIN = 0.83
K3_ZONE_LO = -5000.0
K3_ZONE_HI = -1000.0
"""Пороги из файла допуска. Здесь КОПИЯ, а не источник: источник — хешированный файл."""


class Sample:
    __slots__ = ("offset", "rtt", "index")

    def __init__(self, offset: int, rtt: int, index: int) -> None:
        self.offset = offset
        self.rtt = rtt
        self.index = index


async def collect() -> tuple[list[Sample], list[Sample], int]:
    """Серия замеров: с паузой (`spaced`) и вплотную к каждому из них (`back`)."""
    ex = ccxt_async.binanceusdm({"enableRateLimit": True})

    async def fetch_server_ms() -> int:
        return int(await ex.fetch_time())

    spaced: list[Sample] = []
    back: list[Sample] = []
    failures = 0
    try:
        for i in range(SAMPLES):
            try:
                a = await clock.measure(fetch_server_ms, samples=1)
                b = await clock.measure(fetch_server_ms, samples=1)
            except Exception as e:  # noqa: BLE001 — отказ сети не должен ронять серию
                failures += 1
                print(f"  ⚠ замер {i} не удался: {type(e).__name__} {e}")
                await asyncio.sleep(PAUSE_S)
                continue
            spaced.append(Sample(a.offset_ms, a.rtt_ms, i))
            back.append(Sample(b.offset_ms, b.rtt_ms, i))
            if i % 10 == 0:
                print(f"  замер {i:3d}/{SAMPLES}: сдвиг {a.offset_ms:+d} мс, rtt {a.rtt_ms} мс")
            if i < SAMPLES - 1:
                await asyncio.sleep(PAUSE_S)
    finally:
        await ex.close()
    return spaced, back, failures


def ratios(pairs: list[tuple[Sample, Sample]]) -> list[float]:
    out: list[float] = []
    for x, y in pairs:
        precision = max(x.rtt, y.rtt) / 2
        if precision <= 0:
            continue
        out.append(abs(y.offset - x.offset) / precision)
    return out


def spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def main() -> int:  # noqa: C901
    print("=" * 78)
    print(f"СЕРИЯ: {SAMPLES} замеров с паузой {PAUSE_S:.0f} с "
          f"(покрытие {SAMPLES * PAUSE_S:.0f} с), плюс столько же вплотную")
    print("ДОПУСК: docs/audit/tolerance-clock.md, хеш снят ДО прогона")
    print("=" * 78)

    spaced, back, failures = asyncio.run(collect())
    print(f"\nполучено замеров: {len(spaced)} из {SAMPLES}; отказов сети: {failures}")
    if len(spaced) < QUEUE * 2:
        print("⚠ ЗАМЕРОВ СЛИШКОМ МАЛО — числа недействительны.")
        return 1

    offsets = [s.offset for s in spaced]
    rtts = [s.rtt for s in spaced]
    print(f"сдвиг: min {min(offsets):+d}  медиана {statistics.median(offsets):+.0f}  "
          f"max {max(offsets):+d} мс")
    print(f"rtt:   min {min(rtts)}  медиана {statistics.median(rtts):.0f}  max {max(rtts)} мс")

    # ---------------------------------------------------------------- К1
    print("\n" + "=" * 78)
    print("К1. ЗНАЧИМ ЛИ УХОД СДВИГА НА ФОНЕ ТОЧНОСТИ ЗАМЕРА")
    print("=" * 78)

    real_pairs = list(zip(spaced, spaced[1:], strict=False))
    null_pairs = list(zip(spaced, back, strict=False))
    real = ratios(real_pairs)
    null = ratios(null_pairs)
    if not real or not null:
        print("  ⚠ пар нет — числа недействительны.")
        return 1

    med_real = statistics.median(real)
    med_null = statistics.median(null)
    print(f"  пар с паузой {PAUSE_S:.0f} с: {len(real)}, медиана |уход|/(rtt/2) = {med_real:.3f}")
    print(f"  НУЛЬ, пары вплотную:  {len(null)}, медиана = {med_null:.3f}")

    if med_null >= med_real:
        print("  ⚠⚠ НУЛЬ НЕ СЛАБЕЕ НАСТОЯЩЕГО — наблюдаемое НЕ уход часов, а шум сети.")
        print("     Вердикт К1 НЕ ВЫНОСИТСЯ: прибор мерит не то, что названо.")
    else:
        print(f"  ✅ нуль слабее в {med_real / max(med_null, 1e-9):.2f} раза — "
              "прошедшее время действительно влияет")
        print(f"  ПОРОГ (записан ДО прогона): <{K1_NOISE} тонет в шуме · "
              f"{K1_NOISE}…{K1_SIGNIFICANT} без вывода · >{K1_SIGNIFICANT} бюджет обязан стареть")
        verdict = ("уход ТОНЕТ в шуме замера" if med_real < K1_NOISE
                   else "уход ЗНАЧИМ, решение владельцу" if med_real > K1_SIGNIFICANT
                   else "наблюдение без вывода")
        print(f"  ВЕРДИКТ К1: {med_real:.3f} → {verdict}")

    # ---------------------------------------------------------------- К2
    print("\n" + "=" * 78)
    print("К2. УСТОЙЧИВЕЕ ЛИ РЕГИСТР ИЗ ВОСЬМИ, ЧЕМ ОЧЕРЕДЬ ИЗ ПЯТИ")
    print("=" * 78)

    queue_est = [min(spaced[i:i + QUEUE], key=lambda s: s.rtt).offset
                 for i in range(0, len(spaced) - QUEUE + 1, QUEUE)]
    register_est = [min(spaced[i - REGISTER:i], key=lambda s: s.rtt).offset
                    for i in range(REGISTER, len(spaced) + 1)]
    rng = random.Random(NULL_SEED)
    random_est = [rng.choice(spaced[i:i + QUEUE]).offset
                  for i in range(0, len(spaced) - QUEUE + 1, QUEUE)]

    prec = statistics.median(rtts) / 2
    s_q, s_r, s_n = spread(queue_est), spread(register_est), spread(random_est)
    print(f"  очередь по {QUEUE}: оценок {len(queue_est)}, размах {s_q:.0f} мс "
          f"({s_q / max(prec, 1e-9):.2f} точности)")
    print(f"  регистр  по {REGISTER}: оценок {len(register_est)}, размах {s_r:.0f} мс "
          f"({s_r / max(prec, 1e-9):.2f} точности)")
    print(f"  НУЛЬ, случайный выбор: размах {s_n:.0f} мс")

    if s_n <= s_q:
        print("  ⚠⚠ НУЛЬ НЕ ХУЖЕ ОЧЕРЕДИ — выбор по наименьшему rtt не работает вовсе.")
        print("     Вердикт К2 НЕ ВЫНОСИТСЯ.")
    else:
        ratio = s_r / max(s_q, 1e-9)
        print(f"  ✅ случайный выбор хуже очереди в {s_n / max(s_q, 1e-9):.2f} раза — "
              "выбор по rtt работает")
        print(f"  ПОРОГ (записан ДО прогона): ≤{K2_BETTER} регистр устойчивее · "
              f"{K2_BETTER}…{K2_NO_GAIN} без вывода · >{K2_NO_GAIN} ОТВЕРГНУТА")
        verdict = ("регистр УСТОЙЧИВЕЕ, кандидат на внесение" if ratio <= K2_BETTER
                   else "преимущества НЕТ, гипотеза ОТВЕРГНУТА" if ratio > K2_NO_GAIN
                   else "наблюдение без вывода")
        print(f"  ВЕРДИКТ К2: отношение размахов {ratio:.3f} → {verdict}")

    # ---------------------------------------------------------------- К3
    print("\n" + "=" * 78)
    print("К3. РАСХОДИТСЯ ЛИ НАШ СИММЕТРИЧНЫЙ ПОРОГ С ПРАВИЛОМ БИРЖИ")
    print("=" * 78)
    print(f"  зона расхождения: {K3_ZONE_LO:.0f} < сдвиг < {K3_ZONE_HI:.0f} мс — наши часы")
    print("  впереди биржевых больше чем на секунду; мы такое принимаем, биржа отвергла бы")

    def in_zone(vals: list[float]) -> int:
        return sum(1 for v in vals if K3_ZONE_LO < v < K3_ZONE_HI)

    real_zone = in_zone([float(o) for o in offsets])
    shifted = [float(o) - 2000.0 for o in offsets]
    null_zone = in_zone(shifted)
    print(f"  замеров в зоне: {real_zone} из {len(offsets)} "
          f"({real_zone / len(offsets) * 100:.1f}%)")
    print(f"  НУЛЬ, те же замеры со сдвигом −2000 мс: в зоне {null_zone} из {len(shifted)}")
    if null_zone == 0:
        print("  ⚠⚠ ЗОНА НЕДОСТИЖИМА даже на сдвинутых — условие написано так, что не")
        print("     срабатывает никогда. Вердикт К3 НЕ ВЫНОСИТСЯ.")
    else:
        print("  ✅ зона достижима — «ноль» отличим от «проверка не работает»")
        print("  ПОРОГ (записан ДО прогона): 0 — расхождение теоретическое; "
              ">0 — находка владельцу")
        print(f"  ВЕРДИКТ К3: {real_zone} → "
              f"{'расхождение ТЕОРЕТИЧЕСКОЕ' if real_zone == 0 else 'НАХОДКА, идёт владельцу'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
