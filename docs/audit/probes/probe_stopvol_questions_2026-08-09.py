"""ЗОНД фазы 5 обзора «стоповый объём»: Т1 и цена четырёх правок П1–П4.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-stopvol.md,
sha256 9901f6dea34f00d4554a5d3a20dd06d6f61f195727df2e8ec31d4f3d65b2bd1d (форма LF).

Т1  не ограничен ли размер стопового объёма — ЕДИНСТВЕННАЯ гипотеза;
П1  цена перехода с ТФ−1 на ВСЕ младшие ТФ (стр. 34, пример стр. 40);
П2  цена роли, в которой стоповый служит границей накопления (стр. 39) — С КОНТРОЛЕМ;
П3  цена якоря «лой того же ТФ или ТФ−1» (стр. 18);
П4  цена переноса признака сужения (стр. 34, 57).

⚠ БОЕВОЙ ПУТЬ НЕ ТРОГАЕТСЯ. Зонд зовёт `accumulation.detect`, `swings.detect` и
`stop_volume.classify` как есть; всё, что мерится, считается ЗДЕСЬ поверх их результата.
Копии расчёта накопления в зонде нет.

## Контроли, без которых числа недействительны

* Т1 — нуль: высота каждого стопового умножается на 3, доля обязана ВЫРАСТИ;
* П1 — обязан появиться частный случай стр. 40: стоповый 15м под структурой 4ч;
* П2 — ГЛАВНЫЙ контроль: тот же замер на заведомо неверных ценах (сдвиг +0.7%, равномерная
  решётка, случайные цены). Прецедент 2026-08-08: прежний критерий дал на ETH 22% на
  настоящих против 23% на сдвинутых и был откачен;
* П3 — доля 0 означает ошибку реализации, а не отсутствие свингов;
* сводка отказов по ТФ обязательна во всех.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_stopvol_questions_2026-08-09.py
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import Accumulation, detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.stop_volume import Placement, classify  # noqa: E402
from hunter.swings import SwingKind, SwingSet  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402

SEED = 20260809
NULL_SEEDS = (1, 2, 3, 4, 5)

T1_NEGLIGIBLE = 0.0
T1_OBSERVATION = 5.0
P1_NOTICEABLE = 5.0
P2_MIN_RATIO = 1.5
BAND_LO_PCT, BAND_HI_PCT = 2.0, 5.0
SHIFT = 1.007
"""Пороги и полоса из файла допуска и из стр. 18. Здесь КОПИЯ, источник — хешированный файл."""

LADDER = [tf for tf in TIMEFRAME_MS]
"""Лестница ТФ в порядке возрастания — тот же порядок, что в TIMEFRAME_MS."""


def row_seed(tag: str, seed: int) -> int:
    digest = hashlib.sha256(tag.encode()).digest()
    return SEED + seed * 1000 + int.from_bytes(digest[:4], "big") % 997


def load() -> dict[tuple[str, str], dict[str, list[Bar]]]:
    """{(набор кадров, символ): {ТФ: бары}} — все ряды под data/frames."""
    out: dict[tuple[str, str], dict[str, list[Bar]]] = {}
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(p for p in run.iterdir() if p.is_dir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            per_tf: dict[str, list[Bar]] = {}
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                per_tf[tf] = [
                    Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                        high=float(r["high"]), low=float(r["low"]),
                        close=float(r["close"]), volume=float(r["volume"]))
                    for r in pl.read_parquet(f).iter_rows(named=True)
                ]
            if per_tf:
                out[(run.name, sym)] = per_tf
    return out


def younger_tfs(tf: str, only_one: bool) -> list[str]:
    i = LADDER.index(tf)
    if i == 0:
        return []
    return [LADDER[i - 1]] if only_one else LADDER[:i]


def height(a: Accumulation) -> float:
    return a.upper.edge - a.lower.edge


def swing_prices(sw: SwingSet, kind: SwingKind) -> list[float]:
    return [s.price for s in sw.swings if s.kind is kind]


def main() -> int:  # noqa: C901
    corpus = load()
    if not corpus:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    print("=" * 78)
    print(f"КОРПУС: наборов (кадры × символ) {len(corpus)}, "
          f"рядов {sum(len(v) for v in corpus.values())}")
    print("ДОПУСК: docs/audit/tolerance-stopvol.md, хеш снят ДО прогона")
    print("=" * 78)

    scans: dict[tuple[str, str], dict[str, tuple]] = {}
    swings: dict[tuple[str, str], dict[str, SwingSet]] = {}
    skipped: dict[str, int] = {}
    for key, per_tf in corpus.items():
        scans[key] = {}
        swings[key] = {}
        for tf, bars in per_tf.items():
            sw = detect_swings(bars)
            if not isinstance(sw, SwingSet):
                skipped[tf] = skipped.get(tf, 0) + 1
                continue
            swings[key][tf] = sw
            scans[key][tf] = detect(bars, sw, tf).closed

    total_hosts = sum(len(c) for s in scans.values() for c in s.values())
    print(f"\nструктур закрыто: {total_hosts}; рядов отброшено NotReady: {sum(skipped.values())}")

    # ------------------------------------------------------------------ Т1, П1, П4
    ratios: list[float] = []
    ratios_null: list[float] = []
    sv_one = sv_all = 0
    narrowed_sv = 0
    by_tf_pairs: dict[str, int] = {}
    case_40 = 0

    for key, per_tf in corpus.items():
        for tf, hosts in scans[key].items():
            for host in hosts:
                for only_one in (True, False):
                    for y in younger_tfs(tf, only_one):
                        y_closed = scans[key].get(y)
                        y_bars = per_tf.get(y)
                        if not y_closed or not y_bars:
                            continue
                        items = classify(y_closed, y_bars, host, per_tf[tf], tf).items
                        if only_one:
                            sv_one += len(items)
                            h = height(host)
                            for sv in items:
                                if h > 0:
                                    ratios.append(height(sv.accumulation) / h)
                                    ratios_null.append(height(sv.accumulation) * 3 / h)
                                if sv.accumulation.upper.narrowed or \
                                        sv.accumulation.lower.narrowed:
                                    narrowed_sv += 1
                        else:
                            sv_all += len(items)
                            by_tf_pairs[f"{tf}<-{y}"] = by_tf_pairs.get(f"{tf}<-{y}", 0) + len(items)
                            if tf == "4h" and y == "15m":
                                case_40 += len(items)

    print("\n" + "=" * 78)
    print("Т1. НЕ ОГРАНИЧЕН ЛИ РАЗМЕР СТОПОВОГО ОБЪЁМА")
    print("=" * 78)
    if not ratios:
        print("  ⚠ стоповых объёмов НЕ НАЙДЕНО ВОВСЕ — Т1 не выполняется.")
    else:
        big = sum(1 for r in ratios if r >= 1.0)
        big_null = sum(1 for r in ratios_null if r >= 1.0)
        pct = big / len(ratios) * 100
        pct_null = big_null / len(ratios_null) * 100
        print(f"  стоповых объёмов (ТФ−1): {len(ratios)}")
        print(f"  отношение высот стоповый/хозяин: min {min(ratios):.3f}  "
              f"медиана {statistics.median(ratios):.3f}  max {max(ratios):.3f}")
        print(f"  доля с отношением ≥ 1.0: {big} ({pct:.1f}%)")
        print(f"  НУЛЬ, высоты × 3: {big_null} ({pct_null:.1f}%)")
        if pct_null <= pct:
            print("  ⚠⚠ НУЛЬ НЕ ВЫРОС — счётчик не работает, числа Т1 НЕДЕЙСТВИТЕЛЬНЫ.")
        else:
            print(f"  ✅ нуль вырос с {pct:.1f}% до {pct_null:.1f}% — счётчик работает")
            verdict = ("ограничение НЕ НУЖНО" if pct <= T1_NEGLIGIBLE
                       else "НАХОДКА, идёт владельцу" if pct > T1_OBSERVATION
                       else "наблюдение без вывода")
            print(f"  ПОРОГ (ДО прогона): 0 не нужно · до {T1_OBSERVATION}% без вывода · "
                  f">{T1_OBSERVATION}% находка")
            print(f"  ВЕРДИКТ Т1: {pct:.1f}% → {verdict}")

    print("\n" + "=" * 78)
    print("П1. ЦЕНА ПЕРЕХОДА С ТФ−1 НА ВСЕ МЛАДШИЕ ТФ")
    print("=" * 78)
    print(f"  стоповых при ТФ−1:        {sv_one}")
    print(f"  стоповых при всех младших: {sv_all}")
    gain = (sv_all - sv_one) / max(sv_one, 1) * 100
    print(f"  прибавка: {gain:+.1f}% при пороге заметности {P1_NOTICEABLE}%")
    print(f"  ⚠ ЧАСТНЫЙ СЛУЧАЙ СТР. 40 (стоповый 15м под структурой 4ч): {case_40}")
    if case_40 == 0:
        print("     ⚠⚠ НЕ ПОЯВИЛСЯ — реализация неверна независимо от процентов.")
    if gain < P1_NOTICEABLE:
        print("     ⚠ прибавка ниже порога заметности — искать ошибку реализации.")
    print("  разбивка пар ТФ (хозяин <- младший):")
    for k in sorted(by_tf_pairs, key=lambda x: -by_tf_pairs[x])[:8]:
        print(f"      {k:>10}: {by_tf_pairs[k]}")

    print("\n" + "=" * 78)
    print("П4. ЦЕНА ПЕРЕНОСА ПРИЗНАКА СУЖЕНИЯ")
    print("=" * 78)
    print(f"  стоповых объёмов в сужении: {narrowed_sv} из {sv_one} "
          f"({narrowed_sv / max(sv_one, 1) * 100:.1f}%)")
    if narrowed_sv == 0:
        print("  ⚠ НОЛЬ — либо сужение на младших ТФ не встречается, либо перенос неверен.")

    # ------------------------------------------------------------------ П3
    print("\n" + "=" * 78)
    print("П3. ЦЕНА ЯКОРЯ «ЛОЙ ТОГО ЖЕ ТФ ИЛИ ТФ−1» (стр. 18)")
    print("=" * 78)
    sides = 0
    with_swing_anchor = 0
    for key, per_tf in corpus.items():
        for tf, hosts in scans[key].items():
            own = swings[key].get(tf)
            y = younger_tfs(tf, only_one=True)
            younger_sw = swings[key].get(y[0]) if y else None
            for host in hosts:
                for down in (True, False):
                    sides += 1
                    boundary = host.lower.edge if down else host.upper.edge
                    if down:
                        lo, hi = boundary * (1 - BAND_HI_PCT / 100), boundary * (1 - BAND_LO_PCT / 100)
                    else:
                        lo, hi = boundary * (1 + BAND_LO_PCT / 100), boundary * (1 + BAND_HI_PCT / 100)
                    kind = SwingKind.LOW if down else SwingKind.HIGH
                    found = False
                    for sw in (own, younger_sw):
                        if sw is None:
                            continue
                        if any(lo <= p <= hi for p in swing_prices(sw, kind)):
                            found = True
                            break
                    if found:
                        with_swing_anchor += 1
    print(f"  сторон уровней всего: {sides}")
    print(f"  у скольких в полосе {BAND_LO_PCT}-{BAND_HI_PCT}% есть свинг того же ТФ или ТФ−1: "
          f"{with_swing_anchor} ({with_swing_anchor / max(sides, 1) * 100:.1f}%)")
    if with_swing_anchor == 0:
        print("  ⚠⚠ НОЛЬ — ошибка реализации: свинги в полосе обязаны находиться хоть иногда.")

    # ------------------------------------------------------------------ П2
    print("\n" + "=" * 78)
    print("П2. СТОПОВЫЙ ОБЪЁМ КАК ГРАНИЦА НАКОПЛЕНИЯ (стр. 39) — С КОНТРОЛЕМ")
    print("=" * 78)

    def share(mode: str, seed: int = 0) -> tuple[int, int]:
        hit = tot = 0
        rng = random.Random(row_seed(mode, seed))
        for key, per_tf in corpus.items():
            for tf, hosts in scans[key].items():
                y = younger_tfs(tf, only_one=False)
                cand: list[tuple[float, int]] = []
                for yt in y:
                    y_closed = scans[key].get(yt)
                    y_bars = per_tf.get(yt)
                    if not y_closed or not y_bars:
                        continue
                    for a in y_closed:
                        cand.append((a.lower.edge, y_bars[a.last_index].open_ms))
                        cand.append((a.upper.edge, y_bars[a.last_index].open_ms))
                if not cand:
                    continue
                prices = [p for p, _ in cand]
                lo_p, hi_p = min(prices), max(prices)
                for host in hosts:
                    host_start = per_tf[tf][host.first_index].open_ms
                    for zone in (host.lower, host.upper):
                        tot += 1
                        for i, (p, t) in enumerate(cand):
                            if t >= host_start:      # условие 2: ЗАКРЫТ РАНЬШЕ структуры
                                continue
                            if mode == "shift":
                                p = p * SHIFT
                            elif mode == "grid":
                                p = lo_p + (hi_p - lo_p) * (i / max(len(cand) - 1, 1))
                            elif mode == "noise":
                                p = rng.uniform(lo_p, hi_p)
                            if zone.lo <= p <= zone.hi:   # условие 3: между первыми двумя
                                hit += 1
                                break
        return hit, tot

    real_hit, total_zones = share("real")
    print(f"  зон границ всего: {total_zones}")
    print(f"  настоящие уровни стоповых: {real_hit} ({real_hit / max(total_zones, 1) * 100:.1f}%)")
    nulls: dict[str, float] = {}
    for mode, label in (("shift", f"сдвиг +{(SHIFT - 1) * 100:.1f}%"),
                        ("grid", "равномерная решётка")):
        h, t = share(mode)
        nulls[label] = h / max(t, 1) * 100
        print(f"  НУЛЬ, {label:22}: {h} ({nulls[label]:.1f}%)")
    noise = [share("noise", s)[0] / max(total_zones, 1) * 100 for s in NULL_SEEDS]
    nulls["случайные цены"] = statistics.median(noise)
    print(f"  НУЛЬ, {'случайные цены':22}: медиана {nulls['случайные цены']:.1f}% "
          f"по {len(NULL_SEEDS)} затравкам")

    real_pct = real_hit / max(total_zones, 1) * 100
    worst = max(nulls.values())
    print(f"\n  ПОРОГ (записан ДО прогона): настоящие обязаны превзойти худший нуль "
          f"в {P2_MIN_RATIO} раза")
    print(f"  настоящие {real_pct:.1f}% против худшего нуля {worst:.1f}% — "
          f"отношение {real_pct / max(worst, 1e-9):.2f}")
    if real_pct <= worst:
        print("  ⚠⚠ КРИТЕРИЙ СНОВА НЕ РАЗЛИЧАЕТ — правка ОТМЕНЯЕТСЯ вторично.")
    elif real_pct >= worst * P2_MIN_RATIO:
        print("  ✅ ВЕРДИКТ П2: критерий различает, правка остаётся")
    else:
        print("  ВЕРДИКТ П2: наблюдение без вывода, идёт владельцу")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
