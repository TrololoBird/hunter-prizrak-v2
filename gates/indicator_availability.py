"""ГЕЙТ: с какого бара величина ВООБЩЕ определена. FOUNDATION.md §4.3.

Держит числа в hunter/admission.py::DEFINED_FROM_BARS честными — это «с какого бара
библиотека вообще выдаёт значение». Отдельный вопрос — с какого бара значение
КАНОНИЧНО (не зависит от затравки); его держит gates/formula_reference.py. Если библиотека сменит
конвенцию затравки, требование к истории поедет — и гейт это покажет, а не система
начнёт молча считать направление по несуществующим числам.

Охват печатается числом.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import polars as pl
import polars_talib as plta

sys.path.insert(0, "src")
from hunter import indicators
from hunter.admission import DEFINED_FROM_BARS

SLICE = Path("docs/audit/reference-slice/BTCUSDT-1h-500.parquet")


def first_defined(values: list[float | None]) -> int | None:
    for i, v in enumerate(values):
        # `not math.isnan(v)` вместо идиомы `v == v` — см. пояснение в
        # indicator_oracle.py: обе верны, но вторая даёт вечное предупреждение CodeQL.
        if v is not None and not math.isnan(v):
            return i
    return None


def main() -> int:
    if not SLICE.exists():
        print(f"ПРОВАЛ: нет среза {SLICE} — проверка не состоялась")
        return 1
    df = pl.read_parquet(SLICE)
    computed = {
        "adx14": plta.adx(pl.col("high"), pl.col("low"), pl.col("close"), timeperiod=14),
        "atr14": plta.atr(pl.col("high"), pl.col("low"), pl.col("close"), timeperiod=14),
        # ⚠ Правка аудита 2026-08-06 (М-06 = Н-5): `rsi14` и `ema200` брались прямо из
        # `plta`, минуя обёртку проекта, и подмена периода в `hunter/indicators.py`
        # проезжала мимо гейта (evidence/E-020-gate-probes).
        "rsi14": indicators.rsi(),
        # MACD — проектное определение (ema12 - ema26), а не plta.macd():
        # тот противоречит своим же EMA, см. docs/audit/macd-talib-inconsistency-*.md
        "macd": indicators.macd_line(),
        "ema200": indicators.ema(200),
        # ⚠ Полосы Боллинджера добавлены 2026-08-07: их зовёт `card.py`, и
        # доступность их не проверял никто (замечание QA, 09-qa.md раздел «б»).
        "bb_upper": indicators.bbands_upper(),
        "bb_lower": indicators.bbands_lower(),
    }
    out = df.select(**{k: v.alias(k) for k, v in computed.items()})

    print(f"гейт «с какого бара величина определена»: срез {SLICE.name}, "
          f"баров {df.height}, величин {len(computed)}")
    failed = 0
    for name in sorted(computed):
        idx = first_defined(out[name].to_list())
        declared = DEFINED_FROM_BARS.get(name)
        if idx is None:
            print(f"  ПРОВАЛ {name}: ни одного значения на срезе")
            failed += 1
            continue
        need = idx + 1
        ok = declared == need
        print(f"  {name:8} первое значение на баре {idx:4}  → нужно {need:4} баров; "
              f"объявлено {declared}  {'сходится' if ok else 'РАСХОДИТСЯ'}")
        if not ok:
            failed += 1
    if not computed:
        print("ПРОВАЛ: не проверено ни одной величины")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
