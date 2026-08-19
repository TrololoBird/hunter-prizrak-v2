"""ГЕЙТ: сборка ТФ из 1m держит правило фантомной минуты. FOUNDATION.md §10.3.

Решение владельца 2026-08-17 (jesse-модель): ТФ анализа строятся из минуток локально.
Совпадение с нативными барами биржи доказано зондом на 2 127 895 корзинах
(зонд удалён 2026-08-19 по приказу владельца); живых данных в CI нет, поэтому гейт
держит САМО ПРАВИЛО на детерминированной синтетике, подсаженным нарушением:

  * ПОДСАЖЕННЫЙ ФАНТОМ: минута с volume=0 несёт цену ВЫШЕ всех торговавших. Если бы
    правило «v=0 вне OHLC» сломалось, high корзины взял бы фантом — гейт обязан это
    ловить контролем «наивная сборка ОТЛИЧАЕТСЯ от правильной ровно на фантом»;
  * ПУСТАЯ КОРЗИНА (одни фантомы) обязана ПРОПУСКАТЬСЯ, а не выдумывать цену (§4.3);
  * ЯКОРЬ НЕДЕЛИ: корзины 1w обязаны лежать на сетке понедельника (сдвиг 4 суток от
    эпохи), а не на сетке эпохи — ошибка якоря сдвинула бы ВСЕ недельные бары.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from hunter.bars import grid_anchor_ms, resample_from_1m, tf_ms
from hunter.models import Bar

DAY = 86_400_000
MIN = 60_000
# понедельник 00:00 UTC (2024-01-01) — начало ряда лежит на сетке всех ТФ
T0 = 1_704_067_200_000
assert (T0 - grid_anchor_ms("1w")) % tf_ms("1w") == 0


def synth() -> list[Bar]:
    """Детерминированные 3 недели минуток: цена пилой 100..109, объём 1.0;
    каждая 7-я минута — ФАНТОМ (v=0) с завышенной ценой 999; вторые сутки
    третьей недели — сплошь фантомы (пустая корзина 1d)."""
    out: list[Bar] = []
    empty_day_lo = T0 + 15 * DAY
    empty_day_hi = empty_day_lo + DAY
    for k in range(3 * 7 * 24 * 60):
        ms = T0 + k * MIN
        phantom = (k % 7 == 6) or (empty_day_lo <= ms < empty_day_hi)
        if phantom:
            out.append(Bar(open_ms=ms, open=999.0, high=999.0, low=999.0,
                           close=999.0, volume=0.0))
        else:
            p = 100.0 + (k % 10)
            out.append(Bar(open_ms=ms, open=p, high=p + 0.5, low=p - 0.5,
                           close=p + 0.25, volume=1.0))
    return out


def main() -> int:
    m1 = synth()
    bad: list[str] = []

    for tf in ("5m", "1h", "1d", "1w"):
        res = resample_from_1m(m1, tf)
        anchor = grid_anchor_ms(tf)
        step = tf_ms(tf)
        off_grid = [b for b in res if (b.open_ms - anchor) % step]
        if off_grid:
            bad.append(f"{tf}: {len(off_grid)} корзин мимо сетки (якорь)")
        phantom_leak = [b for b in res if b.high >= 999.0]
        if phantom_leak:
            bad.append(f"{tf}: фантомная цена просочилась в {len(phantom_leak)} корзин")

    d = resample_from_1m(m1, "1d")
    empty_day = T0 + 15 * DAY
    if any(b.open_ms == empty_day for b in d):
        bad.append("1d: пустая корзина ВЫДУМАНА вместо пропуска")
    # соседние сутки при этом на месте — пропуск точечный, а не дыра в полряда
    if not any(b.open_ms == empty_day - DAY for b in d) or \
       not any(b.open_ms == empty_day + DAY for b in d):
        bad.append("1d: пропали соседние с пустой корзиной сутки")

    # ПОЗИТИВНЫЙ КОНТРОЛЬ: наивная сборка (фантомы В OHLC) обязана отличаться —
    # иначе синтетика не способна поймать поломку правила. Первый фантом — минута
    # k=6, то есть ВТОРАЯ корзина 5м (k=5..9); первая корзина фантома не содержит.
    b5 = T0 + tf_ms("5m")
    naive_high = max(b.high for b in m1 if b5 <= b.open_ms < b5 + tf_ms("5m"))
    right = next(b for b in resample_from_1m(m1, "5m") if b.open_ms == b5)
    if not (naive_high >= 999.0 > right.high):
        print("ПРОВАЛ: подсаженный фантом не различим — синтетика ослепла")
        return 1

    # объём: фантомы весят 0, но СЧИТАЮТСЯ в сумме (правило только про OHLC)
    if abs(right.volume - 4.0) > 1e-12:  # 5 минут корзины, одна — фантом
        bad.append(f"5m: объём корзины с фантомом {right.volume}, ожидалось 4.0")

    n = sum(len(resample_from_1m(m1, tf)) for tf in ("5m", "1h", "1d", "1w"))
    print(f"синтетика: минуток {len(m1)}, корзин собрано {n}, нарушений {len(bad)}; "
          f"подсаженный фантом пойман")
    for b in bad:
        print(f"  ПРОВАЛ {b}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
