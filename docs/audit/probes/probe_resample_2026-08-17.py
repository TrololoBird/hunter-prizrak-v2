"""Зонд: свечи, СОБРАННЫЕ из 1m, против нативных свечей биржи — по всем ТФ анализа.

Вопрос владельца 2026-08-17: можно ли получать все нужные ТФ другим способом
(jesse-модель: качать только 1m, старшие строить локально).

Правило сборки, при котором совпадение ТОЧНОЕ: минуты с volume == 0 НЕ участвуют в
OHLC — пустая минута несёт фантомную цену (перенесённый close предыдущей), за которой
нет ни одной сделки, а нативный бар строится по сделкам и фантома не видит (механизм
найден на ASTR 2026-02-18 09:40, см. transport-engine-survey-2026-08-17.md §3).

Контроль (прибор способен ответить иначе): сборка БЕЗ правила на тех же данных даёт
8 981 расхождение на 5м; с правилом — ноль на 2 127 895 корзинах всех ТФ.

Сравниваются только ПОЛНЫЕ корзины (все минуты корзины есть в хранилище); объём — с
относительным допуском 1e-9 (сумма float), OHLC — строго.
"""

import collections
import pathlib

import polars as pl

from hunter.bars import tf_ms

ROOT = pathlib.Path("data/bars/binanceusdm")


def main() -> None:
    tot: collections.Counter[str] = collections.Counter()
    bad: collections.Counter[str] = collections.Counter()
    vol: collections.Counter[str] = collections.Counter()
    for f in sorted(ROOT.glob("*-1m-b1.parquet")):
        mk = f.stem.split("-")[0]
        m1 = pl.read_parquet(f).sort("open_ms")
        for tf in ["5m", "15m", "1h", "4h", "1d", "1w"]:
            p = ROOT / f"{mk}-{tf}-b1.parquet"
            if not p.exists():
                continue
            native = pl.read_parquet(p).sort("open_ms")
            step = tf_ms(tf)
            # сетка недели сдвинута от эпохи — якорь берём у нативного ряда
            anchor = int(native["open_ms"][0]) % step
            withb = m1.with_columns(
                ((pl.col("open_ms") - anchor) // step * step + anchor).alias("bucket"))
            need = step // 60_000
            full = (withb.group_by("bucket").agg(pl.len().alias("mins"))
                    .filter(pl.col("mins") == need))
            res = (withb.filter(pl.col("volume") > 0)
                   .group_by("bucket", maintain_order=True)
                   .agg(pl.col("open").first(), pl.col("high").max(),
                        pl.col("low").min(), pl.col("close").last(),
                        pl.col("volume").sum())
                   .join(full, on="bucket"))
            j = res.join(native, left_on="bucket", right_on="open_ms", suffix="_n")
            if j.height == 0:
                continue
            tot[tf] += j.height
            bad[tf] += j.filter(
                (pl.col("open") != pl.col("open_n"))
                | (pl.col("high") != pl.col("high_n"))
                | (pl.col("low") != pl.col("low_n"))
                | (pl.col("close") != pl.col("close_n"))).height
            vol[tf] += j.filter(
                ((pl.col("volume") - pl.col("volume_n")).abs()
                 / pl.col("volume_n").clip(1e-12)) > 1e-9).height
    for tf in ["5m", "15m", "1h", "4h", "1d", "1w"]:
        print(f"{tf:3} полных корзин {tot[tf]:>7} | OHLC расходится {bad[tf]:>3} "
              f"| объём >1e-9 отн. {vol[tf]:>3}")


if __name__ == "__main__":
    main()
