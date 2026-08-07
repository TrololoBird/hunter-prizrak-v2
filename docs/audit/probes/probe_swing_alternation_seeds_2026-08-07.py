"""ЗОНД Г2, часть 2: нуль на НЕСКОЛЬКИХ затравках — один бросок ничего не доказывает.

Отдельный файл, а не правка `probe_swing_alternation_2026-08-07.py`: тот уже дал числа,
а `pyproject.toml` запрещает править зонд, давший опубликованное число.

ЗАЧЕМ. Первый зонд сравнил приём «оставить самый крайний из серии» с ОДНИМ случайным
выбором и получил 43 убранных уровня против 42. Критерий, записанный в
`docs/audit/tolerance-swings.md` ДО прогона, звучит так: «если правило smc не лучше
случайного выбора ПО ЧИСЛУ ИЗМЕНИВШИХСЯ УРОВНЕЙ, приём не работает». По одному броску
сказать «не лучше» нельзя: 42 могло выпасть случайно.

⚠ И ЕЩЁ: встроенный вердикт первого зонда сравнивал, совпадают ли ДИФФЫ ДОСЛОВНО, и
печатал «крайность несёт информацию». Это НЕ зарегистрированный критерий и вдобавок
критерий, который выполниться почти не может: случайный выбор берёт другие свинги, значит
тексты разойдутся всегда. Здесь считается то, что было зарегистрировано.

Команда воспроизведения:
    cp -r data/frames/a1 data/frames/swingbase   # если ещё нет
    uv run python docs/audit/probes/probe_swing_alternation_2026-08-07.py   # пишет базу
    uv run python docs/audit/probes/probe_swing_alternation_seeds_2026-08-07.py
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import replay, swings  # noqa: E402
from hunter.models import NotReady  # noqa: E402

import importlib.util  # noqa: E402

_p = Path(__file__).resolve().parent / "probe_swing_alternation_2026-08-07.py"
_spec = importlib.util.spec_from_file_location("probe_alt", _p)
assert _spec is not None and _spec.loader is not None
_alt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_alt)  # main() под __name__-guard, здесь не исполняется
thin = _alt.thin
"""Правило прореживания берётся ИЗ первого зонда, а не переписывается: иначе два зонда
могли бы разойтись, и разница легла бы на приём."""

RUN_ID = "swingbase"
SEEDS = (20260807, 1, 2, 3, 4)
"""Первая затравка — та же, что в первом зонде: его число обязано воспроизвестись."""

_original = swings.detect


def patch(mode: str | None, seed: int = 0) -> None:
    if mode is None:
        swings.detect = _original
        return
    rnd = random.Random(seed)

    def patched(bars):  # type: ignore[no-untyped-def]
        sw = _original(bars)
        return sw if isinstance(sw, NotReady) else thin(sw, mode, rnd)

    swings.detect = patched


def levels_removed() -> int | None:
    """Сколько строк уровней (ЛОНГ/ШОРТ) исчезло из карточки против базы."""
    res = replay.replay_run(RUN_ID)
    if isinstance(res, NotReady):
        return None
    n = 0
    for d in res.symbols:
        for ln in d.diff.splitlines():
            if ln.startswith("---"):
                continue
            if ln.startswith("-  ЛОНГ") or ln.startswith("-  ШОРТ"):
                n += 1
    return n


def main() -> int:
    if not (ROOT / "data" / "frames" / RUN_ID / "BTC_USDT_USDT" / "card.txt").exists():
        print(f"базы нет — сначала прогнать probe_swing_alternation_2026-08-07.py")
        return 1

    print("=" * 78)
    print("Г2, часть 2: приём против нуля на нескольких затравках")
    print("=" * 78)
    print("критерий из файла допуска: «не лучше случайного ПО ЧИСЛУ ИЗМЕНИВШИХСЯ УРОВНЕЙ»")
    print()

    patch(None)
    base = levels_removed()
    print(f"контроль: без изменения убрано уровней {base} "
          f"{'✓ база чиста' if base == 0 else '✗ БАЗА ГРЯЗНАЯ'}")
    if base != 0:
        return 1

    patch("extreme")
    ext = levels_removed()
    print(f"\nПРИЁМ «самый крайний из серии»: убрано уровней {ext}")

    print("\nНУЛЬ «случайный из серии», по затравкам:")
    nulls: list[int] = []
    for s in SEEDS:
        patch("random", s)
        v = levels_removed()
        if v is None:
            continue
        nulls.append(v)
        print(f"  затравка {s:<10} убрано уровней {v}")

    patch(None)
    print()
    print("=" * 78)
    if ext is None or not nulls:
        print("ЗАМЕР НЕ СОСТОЯЛСЯ")
        return 1
    lo, hi = min(nulls), max(nulls)
    med = statistics.median(nulls)
    print(f"приём {ext}; нуль: медиана {med}, разброс {lo}…{hi} по {len(nulls)} броскам")
    better = ext > hi
    print()
    print("ВЕРДИКТ ПО ЗАРЕГИСТРИРОВАННОМУ КРИТЕРИЮ:")
    if better:
        print(f"  ✓ приём ВЫШЕ всего разброса нуля ({ext} > {hi}) — крайность работает")
    else:
        print(f"  ✗ приём НЕ ЛУЧШЕ случайного выбора: {ext} лежит внутри разброса "
              f"{lo}…{hi}")
        print("    Прореживание меняет карточку; КРАЙНОСТЬ сверх прореживания — ничего.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
