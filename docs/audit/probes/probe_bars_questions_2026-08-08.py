"""ЗОНД фазы 5 обзора «сетка баров и закрытость»: гипотезы Г1, Г2, Г3.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-bars.md,
sha256 45ae723efa6641d572dea80fba2102851bb39a5774656b4c576c3d5ead897b48.

Г1  цена выбора между «целый ряд», «ряд с дырой как есть» и «непрерывный хвост»;
Г2  привязаны ли разрывы ко времени суток;
Г3  нарушает ли живой корпус молчаливую предпосылку «левый край, левая метка».

## Два решения зонда, которые надо назвать вслух

**Что здесь «целый ряд».** Допуск этого слова не определяет. Взято прочтение, принятое в
поле: ряд, ДОСТРОЕННЫЙ по сетке — пропущенные бары получают цену предыдущего закрытия и
нулевой объём. Ровно так делает freqtrade (`ohlcv_fill_up_missing_data`), и это единственный
доступный на реальных данных смысл слова «целый»: настоящих баров за пропуск взять негде.
⚠ Это ВЫБОР, а не данность, и он влияет на числа Г1.

**Откуда ATR.** `indicators.atr` из проекта УДАЛЕНА 2026-08-06 за отсутствием потребителя.
Возвращать её ради зонда нельзя — §0 запрещает величину без референта. Поэтому нормировка
считается здесь напрямую через `plta.atr`, ровно как это делают три индикаторных гейта.
Боевого кода это не касается.

## Контроли, без которых числа недействительны

* Г1 — нуль обязан дать РОВНО НОЛЬ, а не «мало»: на ряду без дыр все три способа совпадают
  тождественно. Печатается максимум по корпусу, а не среднее: одно ненулевое значение уже
  означает, что прибор неверен;
* Г2 — контроль достижимости: если разрывов ноль, замер не выполняется, и это печатается
  как результат;
* Г3 — контроль ДВУСТОРОННИЙ: подсаженный сдвиг обязан быть пойман, чистый корпус обязан
  вернуть прежнее число;
* СВОДКА ОТКАЗОВ ПО ТАЙМФРЕЙМУ обязательна во всех трёх. Прецедент 2026-08-04: сто
  девяносто честных отказов подряд читались как «рынок такой», пока их не разложили по ТФ.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_bars_questions_2026-08-08.py
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
from dataclasses import replace
from pathlib import Path

import polars as pl
import polars_talib as plta

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import (  # noqa: E402
    TIMEFRAME_MS,
    continuous_tail,
    find_gaps,
    on_grid,
    tf_ms,
)
from hunter.indicators import ema  # noqa: E402
from hunter.models import Bar  # noqa: E402

SEED = 20260808
NULL_SEEDS = (1, 2, 3, 4, 5)

EMA_PERIOD = 200
ATR_PERIOD = 14

G1_NEGLIGIBLE = 0.05
G1_MATERIAL = 0.25
"""Пороги Г1 из файла допуска. Здесь они КОПИЯ, а не источник: источник — хешированный файл."""

G2_UNIFORM_PCT = 100.0 / 24
G2_NOT_RANDOM = 12.5
G2_RANDOM = 8.4
"""Пороги Г2 из файла допуска."""


def row_seed(seed: int, tag: str) -> int:
    """Затравка: устойчивый дайджест, а НЕ встроенный `hash` — тот солится на процесс."""
    digest = hashlib.sha256(tag.encode()).digest()
    return SEED + seed * 1000 + int.from_bytes(digest[:4], "big") % 997


def load() -> list[tuple[str, str, str, list[Bar]]]:
    """ВСЕ ряды под data/frames: (набор кадров, символ, ТФ, бары)."""
    out: list[tuple[str, str, str, list[Bar]]] = []
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(p for p in run.iterdir() if p.is_dir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in pl.read_parquet(f).iter_rows(named=True)]
                out.append((run.name, sym, tf, bars))
    return out


def filled(bars: list[Bar], timeframe: str) -> list[Bar]:
    """Ряд, ДОСТРОЕННЫЙ по сетке: пропуск получает цену предыдущего закрытия, объём ноль.

    Прочтение слова «целый ряд», принятое в поле (freqtrade `ohlcv_fill_up_missing_data`).
    """
    if len(bars) < 2:
        return list(bars)
    step = tf_ms(timeframe)
    out: list[Bar] = [bars[0]]
    for cur in bars[1:]:
        prev = out[-1]
        t = prev.open_ms + step
        while t < cur.open_ms:
            c = prev.close
            out.append(Bar(open_ms=t, open=c, high=c, low=c, close=c, volume=0.0))
            t += step
        out.append(cur)
    return out


def frame(bars: list[Bar]) -> pl.DataFrame:
    return pl.DataFrame({
        "open": [b.open for b in bars], "high": [b.high for b in bars],
        "low": [b.low for b in bars], "close": [b.close for b in bars],
    })


def last_ema(bars: list[Bar]) -> float | None:
    if len(bars) < EMA_PERIOD:
        return None
    v = frame(bars).select(ema(EMA_PERIOD).alias("v"))["v"].to_list()[-1]
    return None if v is None else float(v)


def last_atr(bars: list[Bar]) -> float | None:
    if len(bars) < ATR_PERIOD + 1:
        return None
    v = frame(bars).select(
        plta.atr(pl.col("high"), pl.col("low"), pl.col("close"),
                 timeperiod=ATR_PERIOD).alias("v")
    )["v"].to_list()[-1]
    return None if v is None or v == 0 else float(v)


def g1_pair(raw: list[Bar], timeframe: str) -> tuple[float, float] | None:
    """(d_хвост, d_дыра) для одного ряда либо None, если посчитать нечем."""
    whole = filled(raw, timeframe)
    tail = continuous_tail(raw, timeframe)
    e_whole, e_raw, e_tail = last_ema(whole), last_ema(raw), last_ema(tail)
    a = last_atr(whole)
    if e_whole is None or e_raw is None or e_tail is None or a is None:
        return None
    return abs(e_whole - e_tail) / a, abs(e_whole - e_raw) / a


def describe(name: str, values: list[float]) -> None:
    if not values:
        print(f"  {name}: пусто")
        return
    lo, mid, hi = min(values), statistics.median(values), max(values)
    print(f"  {name}: n={len(values)}  min={lo:.4f}  медиана={mid:.4f}  max={hi:.4f} ATR"
          f"{'   ⚠ КОНСТАНТА' if lo == hi and len(values) > 1 else ''}")


def main() -> int:  # noqa: C901
    rows = load()
    if not rows:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    print("=" * 78)
    print(f"КОРПУС: рядов {len(rows)}, баров {sum(len(b) for _r, _s, _t, b in rows)}")
    print(f"ДОПУСК: docs/audit/tolerance-bars.md, хеш снят ДО прогона")
    print("=" * 78)

    # ---------------------------------------------------------------- Г1
    print("\n" + "=" * 78)
    print("Г1. ЦЕНА ВЫБОРА МЕЖДУ ТРЕМЯ СПОСОБАМИ ОБОЙТИСЬ С ДЫРОЙ")
    print("=" * 78)

    gapped = [(r, s, t, b) for r, s, t, b in rows if find_gaps(b, t)]
    print(f"  рядов С ДЫРАМИ: {len(gapped)} из {len(rows)}")
    if not gapped:
        print("  ⚠ ДЫР В КОРПУСЕ НЕТ ВОВСЕ — Г1 и Г2 не выполняются.")
        print("     Это результат, а не пустая строка: он означает, что find_gaps на живом")
        print("     корпусе не срабатывает никогда, и проверять нечего.")
        return 0

    d_tail: list[float] = []
    d_raw: list[float] = []
    skipped_tf: dict[str, int] = {}
    used_tf: dict[str, int] = {}
    for _r, _s, tf, b in gapped:
        pair = g1_pair(b, tf)
        if pair is None:
            skipped_tf[tf] = skipped_tf.get(tf, 0) + 1
            continue
        used_tf[tf] = used_tf.get(tf, 0) + 1
        d_tail.append(pair[0])
        d_raw.append(pair[1])

    print(f"  посчитано рядов: {len(d_tail)}; отброшено (короче {EMA_PERIOD} баров "
          f"либо ATR не определён): {sum(skipped_tf.values())}")
    print("  СВОДКА ОТКАЗОВ ПО ТФ (перекос вдоль ТФ — известный дефект проекта):")
    for tf in sorted(set(used_tf) | set(skipped_tf), key=lambda t: TIMEFRAME_MS.get(t, 0)):
        print(f"      {tf:>4}: посчитано {used_tf.get(tf, 0):3d}, отброшено "
              f"{skipped_tf.get(tf, 0):3d}")

    if not d_tail:
        print("  ⚠ НИ ОДНОГО ряда не посчитано — числа Г1 недействительны.")
        return 1

    describe("d_хвост = |EMA200(целый) − EMA200(хвост)| / ATR14", d_tail)
    describe("d_дыра  = |EMA200(целый) − EMA200(с дырой)| / ATR14", d_raw)

    print("\n  НУЛЬ: те же ряды БЕЗ дыр (взят непрерывный хвост как весь ряд).")
    print("        Все три способа обязаны совпасть ТОЖДЕСТВЕННО.")
    null_vals: list[float] = []
    for _r, _s, tf, b in gapped:
        tail = continuous_tail(b, tf)
        if find_gaps(tail, tf):
            print("    ⚠ хвост САМ содержит дыру — прибор неверен.")
            return 1
        pair = g1_pair(tail, tf)
        if pair is not None:
            null_vals.extend(pair)
    if not null_vals:
        print("    ⚠ нуль не посчитан ни на одном ряду — сравнивать не с чем.")
        return 1
    worst = max(null_vals)
    print(f"    рядов в нуле: {len(null_vals) // 2}, МАКСИМУМ расхождения: {worst:.10g}")
    if worst != 0.0:
        print("    ⚠ НУЛЬ НЕ НУЛЕВОЙ — прибор считает не то, числа Г1 НЕДЕЙСТВИТЕЛЬНЫ.")
        return 1
    print("    ✅ ровно ноль — прибор на бездырых рядах трёх способов не различает")

    med = statistics.median(d_tail)
    print(f"\n  ПОРОГ (записан ДО прогона): <{G1_NEGLIGIBLE} незаметно · "
          f"{G1_NEGLIGIBLE}…{G1_MATERIAL} без вывода · ≥{G1_MATERIAL} решение владельца")
    verdict = ("расхождение НЕЗАМЕТНО" if med < G1_NEGLIGIBLE
               else "СУЩЕСТВЕННО, требует решения владельца" if med >= G1_MATERIAL
               else "наблюдение без вывода")
    print(f"  ВЕРДИКТ Г1: медиана {med:.4f} ATR → {verdict}")

    # ---------------------------------------------------------------- Г2
    print("\n" + "=" * 78)
    print("Г2. ПРИВЯЗАНЫ ЛИ РАЗРЫВЫ КО ВРЕМЕНИ СУТОК")
    print("=" * 78)

    gap_ms: list[int] = []
    gap_tf: dict[str, int] = {}
    for _r, _s, tf, b in rows:
        for start, _end in find_gaps(b, tf):
            gap_ms.append(start)
            gap_tf[tf] = gap_tf.get(tf, 0) + 1
    print(f"  разрывов всего: {len(gap_ms)}")
    print("  СВОДКА ПО ТФ:")
    for tf in sorted(gap_tf, key=lambda t: TIMEFRAME_MS.get(t, 0)):
        print(f"      {tf:>4}: {gap_tf[tf]}")
    if not gap_ms:
        print("  ⚠ РАЗРЫВОВ НОЛЬ — замер Г2 не выполняется. Это результат, а не пустая")
        print("     строка: find_gaps на живом корпусе не срабатывает.")
        return 0

    def top_share(times: list[int]) -> tuple[int, float]:
        hist: dict[int, int] = {}
        for t in times:
            h = (t // 3_600_000) % 24
            hist[h] = hist.get(h, 0) + 1
        top = max(hist, key=lambda h: hist[h])
        return top, hist[top] / len(times) * 100

    hour, share = top_share(gap_ms)
    print(f"  самый плотный час UTC: {hour:02d}:00, в нём {share:.1f}% разрывов "
          f"(равномерно было бы {G2_UNIFORM_PCT:.1f}%)")

    print("\n  НУЛЬ по файлу допуска: метки времени разрывов ПЕРЕМЕШАНЫ МЕЖДУ СОБОЙ.")
    null_shares: list[float] = []
    for seed in NULL_SEEDS:
        rng = random.Random(row_seed(seed, "g2"))
        shuffled = gap_ms[:]
        rng.shuffle(shuffled)
        null_shares.append(top_share(shuffled)[1])
    null_med = statistics.median(null_shares)
    print(f"    доли по пяти затравкам: "
          f"{', '.join(f'{v:.1f}%' for v in null_shares)}, медиана {null_med:.1f}%")

    if abs(null_med - share) < 1e-9:
        print("\n  ⚠⚠ НУЛЬ ВЫРОЖДЕН, И ВЕРДИКТ Г2 НЕ ВЫНОСИТСЯ.")
        print("     Перестановка меток МЕЖДУ СОБОЙ не меняет множество меток, значит не")
        print("     меняет и гистограмму по часам: нуль даёт то же число, что настоящие")
        print("     данные. Нуль обязан ЛОМАТЬ проверяемое свойство, а этот его сохраняет.")
        print("     Ошибка в файле допуска, а не в данных. Файл после прогона не правится;")
        print("     исправленный нуль (равномерные случайные метки внутри окна ряда)")
        print("     требует НОВОГО файла допуска и нового зонда.")
        print(f"     Само число остаётся фактом: {share:.1f}% в часе {hour:02d}:00.")
    else:
        verdict2 = ("разрывы НЕ случайны" if share >= G2_NOT_RANDOM
                    else "рассеяны равномерно" if share < G2_RANDOM
                    else "наблюдение без вывода")
        print(f"  ВЕРДИКТ Г2: {share:.1f}% против нуля {null_med:.1f}% → {verdict2}")

    # ---------------------------------------------------------------- Г3
    print("\n" + "=" * 78)
    print("Г3. МОЛЧАЛИВАЯ ПРЕДПОСЫЛКА «ЛЕВЫЙ КРАЙ, ЛЕВАЯ МЕТКА»")
    print("=" * 78)

    def violations(series: list[tuple[str, str, str, list[Bar]]]) -> tuple[int, int, dict[str, int]]:
        off_grid = 0
        bad_step = 0
        by_tf: dict[str, int] = {}
        for _r, _s, tf, b in series:
            step = tf_ms(tf)
            for bar in b:
                if not on_grid(bar.open_ms, tf):
                    off_grid += 1
                    by_tf[tf] = by_tf.get(tf, 0) + 1
            for prev, cur in zip(b, b[1:], strict=False):
                d = cur.open_ms - prev.open_ms
                if d <= 0 or d % step != 0:
                    bad_step += 1
                    by_tf[tf] = by_tf.get(tf, 0) + 1
        return off_grid, bad_step, by_tf

    off, bad, by_tf = violations(rows)
    print(f"  баров вне сетки: {off}")
    print(f"  шагов, не кратных ТФ: {bad}")
    if by_tf:
        print("  СВОДКА ПО ТФ:")
        for tf in sorted(by_tf, key=lambda t: TIMEFRAME_MS.get(t, 0)):
            print(f"      {tf:>4}: {by_tf[tf]}")

    print("\n  КОНТРОЛЬ, сторона 1: подсаженный сдвиг на ПОЛШАГА обязан быть пойман.")
    r0, s0, t0, b0 = rows[0]
    step0 = tf_ms(t0)
    spoiled = list(b0)
    idx = min(5, len(spoiled) - 1)
    # `dataclasses.replace`, а не `model_copy`: `Bar` переведён с модели pydantic на
    # срезовый dataclass 2026-08-11 ради памяти (1113 -> 121 байт на бар).
    spoiled[idx] = replace(spoiled[idx], open_ms=spoiled[idx].open_ms + step0 // 2)
    off_s, bad_s, _ = violations([(r0, s0, t0, spoiled)])
    off_c, bad_c, _ = violations([(r0, s0, t0, b0)])
    print(f"    чистый ряд:      вне сетки {off_c}, шаг не кратен {bad_c}")
    print(f"    с подсадкой:     вне сетки {off_s}, шаг не кратен {bad_s}")
    if (off_s + bad_s) <= (off_c + bad_c):
        print("    ⚠ ПОДСАДКА НЕ ПОЙМАНА — проверка ничего не проверяет, число Г3 "
              "недействительно.")
        return 1
    print("    ✅ поймана")
    print("  КОНТРОЛЬ, сторона 2: чистый корпус обязан вернуть прежнее число.")
    off2, bad2, _ = violations(rows)
    if (off2, bad2) != (off, bad):
        print("    ⚠ ПОВТОР ДАЛ ДРУГОЕ ЧИСЛО — замер недетерминирован.")
        return 1
    print(f"    ✅ повтор совпал: {off2} и {bad2}")

    print(f"\n  ПОРОГ (записан ДО прогона): 0 — предпосылку ЗАПИСАТЬ в модуле; "
          f"≥1 — находка владельцу")
    total = off + bad
    print(f"  ВЕРДИКТ Г3: нарушений {total} → "
          f"{'предпосылка подтверждена, её надо записать' if total == 0 else 'НАХОДКА, идёт владельцу'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
