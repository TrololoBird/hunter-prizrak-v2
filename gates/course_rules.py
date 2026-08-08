"""ГЕЙТ: правила курса на рядах с ИЗВЕСТНЫМ ответом. FOUNDATION.md §7.5, §2.

Почему этот гейт вообще появился (2026-08-04, внешний разбор, находка Э-3).

Пятнадцать прежних гейтов проверяли ФОРМУ: импорты, аннотации, наличие цитат, наличие
путей, вхождение имени файла в CI. Поведение расчёта не проверял ни один, кроме оракулов
индикаторов. Отсюда весь класс дефектов, который разбор нашёл исполнением кода, а не
чтением: смещение отбора в леджер, тождественно нулевой счётчик, недостижимая ветка,
цели из будущего. Никакой обход синтаксического дерева не увидит, что `emit.select`
исключает прокол, а `outcome.resolve` его засчитывает.

§7.5 гласит: «Тесты не восстанавливаются. Верификация — гейты в CI и живые прогоны».
Этот файл §7.5 не нарушает и переписывать его не требует: гейт бывает и поведенческим —
ровно так уже устроены `indicator_oracle` и `formula_reference`, единственные две
проверки, которые за всю историю проекта поймали числовую ошибку.

Устройство. Каждый случай — короткий ряд баров, у которого ответ известен ИЗ КУРСА, а не
из прогона кода. Ряд строится руками, ответ выписан рядом со страницей источника. Гейт
падает по коду возврата и печатает, сколько случаев проверено: «нарушений 0» без числа
проверенного неотличимо от непроведённой проверки.

⚠ Ожидание НЕ снимается с текущего поведения. Если случай начнёт падать — сначала
читается страница курса, и только потом решается, что неверно: код или ожидание.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from decimal import Decimal

from hunter.accumulation import MIN_BOUNDARY_POINTS
from hunter.accumulation import detect as detect_accumulations
from hunter.bars import TIMEFRAME_MS
from hunter.breach import BreachKind, Direction, first_breach
from hunter.emit import first_bar_after
from hunter.geometry import build_targets
from hunter.levels import (
    Level,
    LevelSide,
    LevelState,
    LevelStatus,
    MappedLevel,
    status,
)
from hunter.models import Bar
from hunter.outcome import OutcomeKind, resolve
from hunter.swings import detect as detect_swings

TF = "1h"
STEP = TIMEFRAME_MS[TF]
T0 = 1_700_000_000_000 // STEP * STEP


def bars(*ohlc: tuple[float, float, float, float], start: int = 0, gap_at: int = -1) -> list[Bar]:
    """Ряд из четвёрок (open, high, low, close) на сетке ТФ.

    `gap_at` — с какого индекса выбросить один бар из СЕТКИ ВРЕМЕНИ, не убирая его из
    списка. Так строится ряд, в котором соседние по списку бары не соседние по времени:
    это и есть Р-2, «две свечи» как индексы, а не как время.
    """
    out: list[Bar] = []
    shift = 0
    for i, (o, h, low, c) in enumerate(ohlc):
        if i == gap_at:
            shift += STEP * 5
        out.append(Bar(open_ms=T0 + (start + i) * STEP + shift,
                       open=o, high=h, low=low, close=c, volume=1.0))
    return out


def level(price: float, side: LevelSide = LevelSide.LONG, *, created: int = 0,
          born_ms: int | None = None, lo: float = 90.0, hi: float = 110.0,
          narrowed: int = 0, ladder: bool = False) -> Level:
    """Уровень с минимальным правдоподобным окружением. Цена — единственное, что важно.

    Форма базы (`narrowed`, `ladder`) по умолчанию обычная — горизонтальная коробка на
    своих точках. Умолчание стоит ЗДЕСЬ, а не в модели: у `Level` эти поля обязательны,
    чтобы продюсер не мог забыть их проставить, и это правило поймало данный пробник.
    """
    return Level(
        symbol="X/USDT:USDT", timeframe=TF, side=side, price=Decimal(str(price)),
        zone_lo=Decimal(str(price - 1)), zone_hi=Decimal(str(price + 1)),
        created_at_index=created,
        created_at_ms=T0 + (created + 1) * STEP if born_ms is None else born_ms,
        structure_first_index=0, structure_last_index=max(created - 1, 0),
        structure_from_ms=T0, structure_to_ms=T0 + max(created, 1) * STEP,
        structure_volume=100.0,
        boundary_lo=Decimal(str(lo)), boundary_hi=Decimal(str(hi)),
        boundary_narrowed=narrowed,
        boundary_ladder=ladder,
    )


def mapped(lvl: Level, st: LevelState = LevelState.ACTIVE,
           resolved_at_ms: int | None = None) -> MappedLevel:
    return MappedLevel(
        level=lvl,
        status=LevelStatus(state=st, event=None, limit_orders_allowed=st is LevelState.ACTIVE,
                           entry_rule=status(lvl, []).entry_rule, resolved_at_ms=resolved_at_ms),
    )


# --- случаи -------------------------------------------------------------------
# (имя, страница курса, что должно получиться, чем считается)

def c_puncture() -> tuple[object, object]:
    """Стр. 6: «цена прошла за уровень и вернула обратно той же или следующей 1-2
    свечами… является одним из вариантов ОТРАБОТКИ уровня»."""
    b = bars((100, 101, 99, 100),      # 0 — уровень уже существует
             (100, 101, 97, 99),       # 1 — ушла за 98 тенью
             (99, 101, 98.5, 100.5))   # 2 — вернулась СЛЕДУЮЩЕЙ свечой
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.PUNCTURE


def c_breakout() -> tuple[object, object]:
    """Стр. 55: подтверждение — «2-3 полных тел свечей ЭТОГО ТФ» за уровнем; стр. 43:
    после пробоя «Уровень лонг/шорт менятся для нас на противоположный»."""
    b = bars((100, 101, 99, 100),
             (97, 97.5, 96, 96.5),     # тело целиком под 98
             (96, 96.5, 95, 95.5))     # второе тело — подтверждение
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.BREAKOUT


def c_no_event() -> tuple[object, object]:
    """Стр. 23/25: пока цена за уровень не заходила, уровень жив и торгуется лимитками."""
    b = bars((100, 101, 99, 100), (100, 102, 99.5, 101), (101, 103, 100, 102))
    return status(level(98.0), b).state, LevelState.ACTIVE


def c_puncture_is_worked_off() -> tuple[object, object]:
    """Стр. 25: отработанный на первое касание уровень «мы этот уровень удаляем»."""
    b = bars((100, 101, 99, 100), (100, 101, 97, 99), (99, 101, 98.5, 100.5))
    return status(level(98.0), b).state, LevelState.WORKED_OFF


def c_breakout_is_flipped() -> tuple[object, object]:
    """Стр. 43: пробитый уровень становится противоположным, лимитки по нему сняты."""
    b = bars((100, 101, 99, 100), (97, 97.5, 96, 96.5), (96, 96.5, 95, 95.5))
    return status(level(98.0), b).state, LevelState.FLIPPED


def c_unresolved_stays_active() -> tuple[object, object]:
    """Стр. 55 различает прокол и пробой; ВОЗВРАТ ПОЗЖЕ курс не разбирает вовсе.
    Значит вердикта нет, а уровень остаётся торгуемым — выдумать третий исход нельзя."""
    b = bars((100, 101, 99, 100),
             (99, 99.5, 97, 98.5),     # ушла тенью, тело не за уровнем
             (98.4, 98.6, 97.5, 98.2),
             (98.2, 98.4, 97.6, 98.1),
             (98.1, 99.5, 98.05, 99))  # вернулась на 4-й — позже RETURN_BARS
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.UNRESOLVED


def c_structure_needs_exit() -> tuple[object, object]:
    """Стр. 23: «пока цена не вышла из структуры — у нас нет уровня».

    Ряд колеблется внутри диапазона и из него не выходит: закрытых структур обязано
    быть НОЛЬ, а незакрытый хвост — виден (§4.3, а не молчание).
    """
    seq: list[tuple[float, float, float, float]] = []
    for i in range(24):
        if i % 4 == 1:
            seq.append((100, 108, 99, 104))     # локальный хай
        elif i % 4 == 3:
            seq.append((104, 105, 92, 100))     # локальный лой
        else:
            seq.append((100, 103, 97, 101))
    sc = detect_accumulations(seq_bars := bars(*seq), detect_swings(seq_bars), TF)  # type: ignore[arg-type]
    return (len(sc.closed), sc.open_tail is not None), (0, True)


def _four_point_structure() -> list[Bar]:
    """Ряд с ровно ЧЕТЫРЬМЯ точками границ и выходом вверх (2 хая + 2 лоя, стр. 22)."""
    seq = [(100.0, 103.0, 97.0, 101.0)] * 2
    seq += [(100.0, 108.0, 99.0, 104.0)]      # хай 1
    seq += [(100.0, 103.0, 97.0, 101.0)] * 2
    seq += [(104.0, 105.0, 92.0, 100.0)]      # лой 1
    seq += [(100.0, 103.0, 97.0, 101.0)] * 2
    seq += [(100.0, 107.0, 99.0, 104.0)]      # хай 2
    seq += [(100.0, 103.0, 97.0, 101.0)] * 2
    seq += [(104.0, 105.0, 93.0, 100.0)]      # лой 2
    seq += [(100.0, 103.0, 97.0, 101.0)] * 3
    seq += [(109.0, 112.0, 108.5, 111.0)] * 3  # выход вверх тремя полными телами
    return bars(*seq)


def c_min_points_threshold_bites() -> tuple[object, object]:
    """Стр. 22 «4 и более точек» и стр. 23 «(4+6+ точки границ)» — курс называет ОБА числа.

    Проверка комплектности при умолчании 4 НЕДОСТИЖИМА по построению: цикл не доходит до
    выхода, пока у каждой стороны меньше двух точек, значит на строке проверки сумма
    всегда ≥ 4. Именно так её и нашёл разбор (Р-1): комментарий описывал поведение,
    которого не бывает, а счётчик распадов по этой причине не мог вырасти.

    Поэтому здесь проверяется САМО ПРАВИЛО, а не умолчание: порог берётся на единицу выше
    того, сколько точек в ряду реально нашлось, и структура обязана быть отвергнута.
    Ожидаемый ответ (ноль структур, распад засчитан) задан правилом курса, а не снят с
    текущего поведения — из кода взято только число точек ряда.
    """
    b = _four_point_structure()
    sw = detect_swings(b)
    base = detect_accumulations(b, sw, TF)  # type: ignore[arg-type]
    if not base.closed:
        return "структур не найдено вовсе", "одна структура при умолчании"
    points = base.closed[0].points
    strict = detect_accumulations(b, sw, TF, min_points=points + 1)  # type: ignore[arg-type]
    return (
        (len(base.closed), points >= MIN_BOUNDARY_POINTS, len(strict.closed), strict.resets > 0),
        (1, True, 0, True),
    )


def c_outcome_target() -> tuple[object, object]:
    """Стр. 9: «Р или R — один Риск». Цель на 2R обязана дать ровно +2.0, не «около»."""
    b = bars((100, 100.5, 99.5, 100),  # 0
             (100, 100.2, 99.9, 100),  # 1 — вход 100 задет
             (100, 120, 99.9, 119))    # 2 — цель 120 при стопе 90 это ровно 2R
    res = resolve(side=LevelSide.LONG, entry=Decimal("100"), stop=Decimal("90"),
                  target=Decimal("120"), bars=b, from_index=1)
    return (res.kind, res.r), (OutcomeKind.TARGET, 2.0)


def c_outcome_stop() -> tuple[object, object]:
    """Стоп по определению единицы измерения равен ровно −1R (стр. 9)."""
    b = bars((100, 100.5, 99.5, 100),
             (100, 100.2, 99.9, 100),
             (100, 100.1, 89, 90))
    res = resolve(side=LevelSide.LONG, entry=Decimal("100"), stop=Decimal("90"),
                  target=Decimal("120"), bars=b, from_index=1)
    return (res.kind, res.r), (OutcomeKind.STOP, -1.0)


def c_outcome_ambiguous() -> tuple[object, object]:
    """§4.3: по бару OHLC неизвестно, что случилось раньше — стоп или цель.
    Один бар накрыл оба уровня → исход НЕ назначается и R остаётся неизвестным."""
    b = bars((100, 100.5, 99.5, 100),
             (100, 121, 89, 100))
    res = resolve(side=LevelSide.LONG, entry=Decimal("100"), stop=Decimal("90"),
                  target=Decimal("120"), bars=b, from_index=1)
    return (res.kind, res.r), (OutcomeKind.AMBIGUOUS, None)


def c_outcome_not_filled() -> tuple[object, object]:
    """Стр. 30: вход лимиткой на ПОК. Цена, не дошедшая до входа, — это НЕ убыток."""
    b = bars((105, 106, 104, 105), (105, 107, 104.5, 106))
    res = resolve(side=LevelSide.LONG, entry=Decimal("100"), stop=Decimal("90"),
                  target=Decimal("120"), bars=b, from_index=0)
    return (res.kind, res.r), (OutcomeKind.NOT_FILLED, None)


def c_target_not_from_future() -> tuple[object, object]:
    """Стр. 23: уровня не существует, пока цена не вышла из структуры.

    Значит целью не может быть уровень, появившийся ПОЗЖЕ сигнала. Это Н-1 разбора:
    замер дал 54% основных целей, взятых из будущего.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    future = level(120.0, LevelSide.SHORT, created=50, born_ms=src.created_at_ms + STEP)
    past = level(118.0, LevelSide.SHORT, created=1, born_ms=src.created_at_ms - STEP)
    got = build_targets(src, (mapped(future), mapped(past)))
    return tuple(float(t.price) for t in got), (118.0,)


def c_target_not_retired() -> tuple[object, object]:
    """Стр. 25 («мы этот уровень удаляем») и стр. 43 (уровень стал противоположным).

    Снятый уровень целью не является. Это Н-2: замер дал 83% целей в таких состояниях.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    dead = mapped(level(115.0, LevelSide.SHORT, created=1, born_ms=T0),
                  LevelState.WORKED_OFF, resolved_at_ms=T0 + STEP)
    alive = mapped(level(118.0, LevelSide.SHORT, created=1, born_ms=T0))
    got = build_targets(src, (dead, alive))
    return tuple(float(t.price) for t in got), (118.0,)


def c_target_retired_later_still_counts() -> tuple[object, object]:
    """Обратная сторона того же правила, и она важнее: фильтровать по СЕГОДНЯШНЕМУ
    состоянию значит заменить одно заглядывание вперёд другим.

    Уровень, снятый ПОЗЖЕ сигнала, в момент сигнала был законной целью.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    later = mapped(level(118.0, LevelSide.SHORT, created=1, born_ms=T0),
                   LevelState.WORKED_OFF, resolved_at_ms=src.created_at_ms + STEP * 10)
    got = build_targets(src, (later,))
    return tuple(float(t.price) for t in got), (118.0,)


def c_outcome_only_future_bars() -> tuple[object, object]:
    """Б-3: исход считается только по барам, ЗАКРЫВШИМСЯ после записи сигнала.

    Ряд из шести баров; сигнал записан на закрытии третьего. Индекс, с которого можно
    считать исход, обязан быть четвёртым (3), а не первым после появления уровня.
    """
    b = bars(*[(100, 101, 99, 100)] * 6)
    since = b[2].open_ms + STEP          # момент закрытия бара с индексом 2
    return first_bar_after(b, TF, since, not_before=1), 3


def c_two_candles_are_time_not_index() -> tuple[object, object]:
    """Стр. 55: «возвращается той же или следующей свечей» — это ПРО ВРЕМЯ.

    Ряд с дырой: бар возврата стоит следующим в списке, но отстоит на шесть часов.
    По курсу это уже не «следующая свеча», значит и не прокол. Это Р-2 разбора.
    """
    b = bars((100, 101, 99, 100),
             (100, 101, 97, 99),        # ушла за уровень
             (99, 101, 98.5, 100.5),    # ← между ним и предыдущим ДЫРА в 5 баров
             gap_at=2)
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.UNRESOLVED


CASES: list[tuple[str, Callable[[], tuple[object, object]]]] = [
    ("прокол — отработка уровня (стр. 6, 55)", c_puncture),
    ("пробой — два полных тела (стр. 55, 43)", c_breakout),
    ("цена за уровень не заходила — уровень жив (стр. 23)", c_no_event),
    ("прокол → уровень отработан (стр. 25)", c_puncture_is_worked_off),
    ("пробой → уровень флипнут (стр. 43)", c_breakout_is_flipped),
    ("поздний возврат — курс вердикта не даёт (стр. 55)", c_unresolved_stays_active),
    ("нет выхода из структуры — нет уровня (стр. 23)", c_structure_needs_exit),
    ("порог точек границ срабатывает (стр. 22, 23)", c_min_points_threshold_bites),
    ("исход по цели = +2R (стр. 9)", c_outcome_target),
    ("исход по стопу = −1R (стр. 9)", c_outcome_stop),
    ("стоп и цель в одном баре — вердикта нет (§4.3)", c_outcome_ambiguous),
    ("цена не дошла до входа — это не убыток (стр. 30)", c_outcome_not_filled),
    ("цель не может появиться позже сигнала (стр. 23)", c_target_not_from_future),
    ("снятый уровень целью не является (стр. 25, 43)", c_target_not_retired),
    ("снятый ПОЗЖЕ сигнала — целью являлся (стр. 25)", c_target_retired_later_still_counts),
    ("исход только по будущим барам (§8 этап 7)", c_outcome_only_future_bars),
    ("«следующая свеча» — это время, а не индекс (стр. 55)", c_two_candles_are_time_not_index),
]


def main() -> int:
    bad: list[str] = []
    for name, fn in CASES:
        try:
            got, want = fn()
        except Exception as e:  # падение случая — тоже провал гейта, а не пропуск
            bad.append(f"{name}: случай упал — {type(e).__name__} {e}")
            continue
        if got != want:
            bad.append(f"{name}: получено {got!r}, ожидалось {want!r}")

    print(f"гейт правил курса: случаев {len(CASES)}, расхождений {len(bad)}")
    for b in bad:
        print(f"  РАСХОЖДЕНИЕ {b}")
    if not CASES:
        print("ПРОВАЛ: случаев нет — проверка не состоялась")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
