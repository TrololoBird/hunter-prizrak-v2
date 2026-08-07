"""ГЕЙТ: канонические величины против независимого оракула. FOUNDATION.md §10.3.

§10.3 дословно: «Гейт считает ATR/RSI/MACD проектным кодом и эталонной библиотекой на
одном срезе, сравнивает, падает при расхождении выше 1e-6.»

Проектный код здесь — polars_talib (§10.2: «Индикаторы: не писать свои»).
Оракул — pandas-ta, вызываемый с talib=False.

⚠ `talib=False` ОБЯЗАТЕЛЕН. Замер 2026-08-03: с настройками по умолчанию pandas-ta
делегирует в TA-Lib, если тот установлен, и тогда гейт сверяет TA-Lib сам с собой —
зелёный при нулевом охвате. С talib=False реализации независимы и расходятся на
~1e-11 (ATR) и ~1e-8 (RSI), то есть порог 1e-6 различает «сошлось» и «разошлось».

pandas здесь — единственное место в проекте, где он допустим: §10.3 разрешает оракул
«только для гейтов, не для продакшена».

Срез — настоящие бары биржи (docs/audit/reference-slice/), а не выдуманные числа.
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import polars_talib as plta

TOLERANCE = 1e-6  # §10.3 дословно
SLICE = Path("docs/audit/reference-slice/BTCUSDT-1h-500.parquet")

sys.path.insert(0, "src")
from hunter import indicators  # noqa: E402

# ПРОГРЕВ: сколько баров нужно, чтобы две независимые реализации сошлись в 1e-6.
# Экспоненциальное сглаживание засевается по-разному, и разница затухает, а не
# исчезает сразу. Замер 2026-08-03 на срезе BTCUSDT 1h, 499 баров:
#   atr14  144 бара (10.3 × периода); на 20-м баре расхождение ещё 5.0e-3
#   rsi14  193 бара (13.8 × периода); на 20-м баре 6.5e-2
#   macd   111 баров (4.3 × медленного периода)
#   ema200 199 баров (1.0 × периода) — сходится сразу, 4.3e-15
# Протокол: docs/audit/indicator-warmup-2026-08-03.md
# Значение, посчитанное раньше прогрева, зависит от способа затравки, а не от рынка,
# и по §4.3 не должно выдаваться как факт.
WARMUP_BARS: dict[str, int] = {"atr14": 144, "rsi14": 193, "macd": 111, "ema200": 199}


def _project(df: pl.DataFrame) -> dict[str, list[float | None]]:
    """Величины так, как их считает проект — ЧЕРЕЗ `hunter.indicators`, а не через `plta`.

    ⚠ Правка аудита 2026-08-06, находка М-06 (она же Н-5 разбора 2026-08-04). До неё
    здесь стояли прямые вызовы `plta.*`, то есть гейт сравнивал `polars_talib` с
    `pandas-ta` — ДВЕ ЧУЖИЕ БИБЛИОТЕКИ ДРУГ С ДРУГОМ — и до обёртки проекта не
    дотягивался. §10.3 обещает другое: «Гейт считает ATR/RSI/MACD **проектным кодом** и
    эталонной библиотекой».

    Пробник, которым это найдено (evidence/E-020-gate-probes): период `rsi` в
    `src/hunter/indicators.py` меняется с 14 на 20 — все три индикаторных гейта
    возвращали 0. Контроль: тем же пробником `gates/purity.py` на подсаженном нарушении
    вернул 1, то есть пробник исправен.

    `macd` шёл через проект и раньше: `plta.macd` противоречит собственным EMA библиотеки
    (macd-talib-inconsistency-2026-08-03.md), поэтому проект собирает линию сам.
    """
    out = df.select(
        indicators.rsi(14).alias("rsi14"),
        indicators.macd_line().alias("macd"),
        indicators.ema(200).alias("ema200"),
    )
    got = {c: out[c].to_list() for c in out.columns}
    # ⚠ atr14 обёртки проекта НЕ ИМЕЕТ: `indicators.atr` удалена 2026-08-06 за отсутствием
    # потребителя (см. её некролог в src/hunter/indicators.py). Поэтому строка ниже —
    # честно БИБЛИОТЕКА против оракула, а не проект против оракула, и печать это называет.
    # Убрать её молча значило бы сузить охват гейта, не сказав об этом.
    got["atr14"] = (
        df.select(plta.atr(pl.col("high"), pl.col("low"), pl.col("close"),
                           timeperiod=14).alias("a"))["a"].to_list()
    )
    return got


LIBRARY_ONLY: frozenset[str] = frozenset({"atr14"})
"""Величины, у которых обёртки проекта нет: сравнение идёт библиотека против оракула.

Печатается рядом с результатом. Первая редакция этой правки давала ключу отдельное имя, и
он молча выпал из сверки — «величин 3» вместо 4 при зелёном коде возврата. Ровно тот
дефект, который эта же правка и чинит: сужение охвата, выглядящее как успех."""


def _oracle(df: pl.DataFrame) -> dict[str, list[float | None]]:
    """Те же величины независимой реализацией (§10.3)."""
    import pandas as pd  # noqa: TID251 — §10.3 разрешает оракул только в гейтах
    import pandas_ta  # noqa: F401 — регистрирует акцессор .ta

    pdf = pd.DataFrame({
        "high": df["high"].to_list(),
        "low": df["low"].to_list(),
        "close": df["close"].to_list(),
    })
    macd = pdf.ta.macd(fast=12, slow=26, signal=9, talib=False)
    return {
        "atr14": pdf.ta.atr(length=14, talib=False, mamode="rma").tolist(),
        "rsi14": pdf.ta.rsi(length=14, talib=False).tolist(),
        "macd": macd.iloc[:, 0].tolist(),
        "ema200": pdf.ta.ema(length=200, talib=False).tolist(),
    }


def compare(
    a: list[float | None], b: list[float | None], warmup: int
) -> tuple[int, float, int]:
    """Возвращает (сравнено точек, худшее относительное расхождение, сколько за порогом).

    Точки до прогрева пропускаются: там расходятся затравки, а не расчёт.
    """
    compared = 0
    worst = 0.0
    over = 0
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if i < warmup:
            continue
        if x is None or y is None or x != x or y != y:  # None и NaN
            continue
        compared += 1
        denom = max(abs(x), abs(y), 1e-9)
        rel = abs(x - y) / denom
        worst = max(worst, rel)
        if rel > TOLERANCE:
            over += 1
    return compared, worst, over


def main() -> int:
    if not SLICE.exists():
        print(f"ПРОВАЛ: нет эталонного среза {SLICE} — проверка не состоялась")
        return 1
    df = pl.read_parquet(SLICE)
    proj = _project(df)
    orac = _oracle(df)

    names = sorted(set(proj) & set(orac))
    if not names:
        print("ПРОВАЛ: не нашлось ни одной общей величины — проверка не состоялась")
        return 1

    failed = 0
    print(f"гейт-оракул: срез {SLICE.name}, баров {df.height}, величин {len(names)}, "
          f"порог {TOLERANCE:g}")
    for n in names:
        warm = WARMUP_BARS.get(n)
        if warm is None:
            print(f"  ПРОВАЛ {n}: прогрев не замерен — сверять нечем")
            failed += 1
            continue
        compared, worst, over = compare(proj[n], orac[n], warm)
        status = "СОШЛОСЬ" if over == 0 else f"РАЗОШЛОСЬ на {over}"
        arm = " ⚠ обёртки проекта нет: библиотека против оракула" if n in LIBRARY_ONLY else ""
        print(f"  {n:8} прогрев {warm:4}  сравнено {compared:4}  "
              f"худшее расхождение {worst:.3e}  {status}{arm}")
        if compared == 0:
            print(f"  ПРОВАЛ {n}: сравнено 0 точек — величина не проверена")
            failed += 1
        if over:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
