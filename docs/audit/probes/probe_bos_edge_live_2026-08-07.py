"""ЗОНД-КОНТРОЛЬ: живёт ли правка «ближний край зоны» в БОЕВОМ коде.

Повтор на кадрах показал, что набор переприоров в карточке не изменился — изменился только
бар теста. Это могло значить две вещи, и они разные:
  (а) правка края не действует вовсе (ошибка правки);
  (б) правка действует, но её не видно, потому что `detect` возвращает только ПОСЛЕДНИЙ
      переприор каждой стороны, а лишние сломы лежат раньше по истории.

Зонд разводит (а) и (б) подсаженным рядом, построенным так, чтобы ДАЛЬНИЙ край слома не
дал, а БЛИЖНИЙ дал. Если боевой `detect` находит на нём переприор — правка живёт.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_bos_edge_live_2026-08-07.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.breach import BreachKind, Direction, first_breach  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.pereprior import detect  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402

STEP = 300_000


def bar(i: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(open_ms=i * STEP, open=o, high=h, low=low, close=c, volume=1.0)


def build() -> list[Bar]:
    """Ряд с лоем-фракталом, у которого широкая нижняя тень, и последующим сломом.

    Свеча лоя: low=90, тело 95…96 — зона тени 90…95. Дальний край 90, ближний 95.
    После неё цена уходит телами в 92…94: это НИЖЕ ближнего края (95), но ВЫШЕ дальнего
    (90). При дальнем крае слома нет, при ближнем — есть.
    """
    b: list[Bar] = []
    # рост: формируем хай
    for i, p in enumerate([100, 101, 102, 103, 104]):
        b.append(bar(i, p, p + 1.0, p - 1.0, p + 0.5))
    # хай на баре 5
    b.append(bar(5, 105.0, 110.0, 104.0, 105.5))
    for i, p in enumerate([104, 103], start=6):
        b.append(bar(i, p, p + 1.0, p - 1.0, p - 0.5))
    # лой на баре 8: широкая нижняя тень
    b.append(bar(8, 96.0, 96.5, 90.0, 95.0))
    for i, p in enumerate([99, 101], start=9):
        b.append(bar(i, p, p + 1.0, p - 1.0, p + 0.5))
    # второй хай ВЫШЕ первого (обновление) на баре 11
    b.append(bar(11, 108.0, 112.0, 107.0, 111.0))
    for i, p in enumerate([106, 102], start=12):
        b.append(bar(i, p, p + 1.0, p - 1.0, p - 0.5))
    # слом: два полных тела между 92 и 94 — ниже ближнего края 95, выше дальнего 90
    b.append(bar(14, 94.0, 94.5, 92.5, 93.0))
    b.append(bar(15, 93.0, 93.5, 92.0, 92.5))
    # возврат в зону 90…95 закрытием
    b.append(bar(16, 93.0, 94.5, 92.5, 94.0))
    for i, p in enumerate([95, 96], start=17):
        b.append(bar(i, p, p + 1.0, p - 1.0, p + 0.5))
    return b


def main() -> int:
    bars = build()
    sw = detect_swings(bars)
    if not hasattr(sw, "swings"):
        print(f"свингов нет: {sw}")
        return 1
    print("=" * 78)
    print("КОНТРОЛЬ: живёт ли правка «ближний край зоны» в боевом коде")
    print("=" * 78)
    print(f"баров {len(bars)}, свингов {len(sw.swings)}")
    for s in sorted(sw.swings, key=lambda x: x.index):
        print(f"  свинг {s.kind.value:<4} бар {s.index:>2} цена {s.price}")
    pps = detect(bars, sw, "5m")
    print(f"\nпереприоров найдено: {len(pps)}")
    for pp in pps:
        print(f"  {pp.kind.value} в {pp.side.value}: слом {pp.broken_price}, "
              f"зона {pp.zone_lo}…{pp.zone_hi}, подтверждён на баре "
              f"{pp.confirmed_at_index}, тест {pp.tested_at_index}")
    print()
    # ОБРАТНАЯ СТОРОНА КОНТРОЛЯ: на том же ряду ДАЛЬНИЙ край слома давать НЕ должен.
    # Без неё контроль односторонний: «нашёлся переприор» само по себе не доказывает,
    # что дело в крае — он мог найтись по любой причине.
    far = first_breach(bars, 90.0, Direction.BELOW, "5m", from_index=9)
    near = first_breach(bars, 95.0, Direction.BELOW, "5m", from_index=9)
    print(f"обратная сторона: пробой от ДАЛЬНЕГО края 90 → "
          f"{far.kind.value if far else 'событий нет'}")
    print(f"                  пробой от БЛИЖНЕГО края 95 → "
          f"{near.kind.value if near else 'событий нет'}")
    two_sided = (far is None or far.kind is not BreachKind.BREAKOUT) and (
        near is not None and near.kind is BreachKind.BREAKOUT)
    print(f"                  {'✓ края разводят исход' if two_sided else '✗ КРАЯ НЕ РАЗВОДЯТ — контроль не доказывает ничего'}")

    short = [p for p in pps if p.side.value == "short"]
    if short and two_sided:
        print("\n✓ ПРАВКА ЖИВЁТ: на ряду, где тела не доходят до дальнего края зоны (90),")
        print("  но заходят за ближний (95), боевой detect находит переприор в шорт,")
        print("  а от дальнего края пробоя нет вовсе.")
        return 0
    print("✗ ПЕРЕПРИОРА НЕТ — либо правка не действует, либо ряд построен неверно.")
    print("  Разобрать до публикации: ноль здесь ничего не доказывает.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
