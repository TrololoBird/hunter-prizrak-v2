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

from hunter.bars import TIMEFRAME_MS
from hunter.breach import BreachKind, Direction, first_breach
from hunter.emit import first_bar_after
from hunter.figures import pennant
from hunter.geometry import TARGET_STRUCTURE_FRAME_BARS, build_setup, build_targets
from hunter.levels import (
    Level,
    LevelSide,
    LevelState,
    LevelStatus,
    MappedLevel,
    StopAnchorSource,
    status,
    stop_anchor,
)
from hunter.models import Bar, NotReady
from hunter.outcome import OutcomeKind, resolve
from hunter.priority import Priority
from hunter.swings import ZIGZAG_DEPTH, TrendDirection
from hunter.swings import detect as detect_swings
from hunter.trading_range import MIN_BOUNDARY_POINTS, BoundaryZone
from hunter.trading_range import detect as detect_ranges
from hunter.volume_profile import TV_ROWS

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
        # Второе скопление объёма: у синтетического уровня профиля НЕТ вовсе,
        # поэтому ноль — это «вне зоны непустых строк не найдено», а не
        # «проверено и не нашлось». Пробникам величина безразлична.
        outside_peak_share=0.0,
        # Высота строки профиля: боевой режим — `TV_ROWS` строк на размах коробки,
        # поэтому берётся ТОЙ ЖЕ формулой, а не выдуманным числом. Пробнику она
        # безразлична (правила курса о разрешении профиля не говорят), но подставлять
        # константу значило бы завести здесь вторую сущность под тем же именем.
        row_height=Decimal(str((hi - lo) / TV_ROWS)),
        boundary_lo=Decimal(str(lo)), boundary_hi=Decimal(str(hi)),
        # Прокола у синтетической базы нет — расширенный край СОВПАДАЕТ с границей,
        # ровно как `BoundaryZone.extended_edge` при `puncture is None`.
        stop_edge_lo=Decimal(str(lo)), stop_edge_hi=Decimal(str(hi)),
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


def c_flipped_level_changes_side() -> tuple[object, object]:
    """Стр. 43 дословно: «Уровень лонг/шорт менятся для нас на противоположный».

    ⚠ Случай добавлен 2026-08-24, потому что правило было НАПИСАНО и НЕ ИСПОЛНЯЛОСЬ.
    `Level.flipped()` существовал с давних пор и не вызывался НИ ОТКУДА: единственное
    упоминание стояло в докстроке `EntryRule.RETEST_FLIPPED`, которая УТВЕРЖДАЛА, что
    уровень «уже другой стороны». На свежих данных 2026-08-24 в этом состоянии было
    56 уровней из 132 (42%), и каждый предъявлялся владельцу СТАРОЙ стороной, тогда
    как соседняя строка той же карточки писала «вход по ретесту в ДРУГУЮ сторону».

    Три плеча, и все нужны:
      * пробитый — сторона перевёрнута;
      * активный — сторона НЕ трогается (иначе прибор переворачивает всё подряд);
      * сторона РОЖДЕНИЯ (`level.side`) остаётся прежней и у пробитого: по ней
        считается всё «на момент», и подмена задним числом сменила бы историю.
    """
    b = bars((100, 101, 99, 100), (97, 97.5, 96, 96.5), (96, 96.5, 95, 95.5))
    lvl = level(98.0, LevelSide.LONG)
    broken = MappedLevel(level=lvl, status=status(lvl, b))
    calm = mapped(level(98.0, LevelSide.LONG))
    return (
        (broken.status.state.value, broken.current.side.value, broken.level.side.value,
         calm.current.side.value),
        ("flipped", "short", "long", "long"),
    )


def c_late_return_no_bodies_is_puncture() -> tuple[object, object]:
    """Поздний возврат БЕЗ единого тела за уровнем — прокол.

    Курс случай не разбирает ни текстом, ни рисунком; по §0.1 молчание заполняет
    классика (Булковски, Эдвардс-Маги: пробоя без закрытий не было), и рисунок стр. 6
    несёт то же необходимое условие ("цена не уходит за уровень целыми свечами", М-15).
    Решение владельца 2026-08-09; разбор — docs/audit/classics-tolerance-2026-08-09.md.
    До этого ожидание случая было UNRESOLVED («курс вердикта не даёт») — оно было
    честным при запрете второго голоса, но владелец второй голос разрешил."""
    b = bars((100, 101, 99, 100),
             (99, 99.5, 97, 98.5),     # ушла тенью, тело не за уровнем
             (98.4, 98.6, 97.5, 98.2),
             (98.2, 98.4, 97.6, 98.1),
             (98.1, 99.5, 98.05, 99))  # вернулась на 4-й — позже RETURN_BARS
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.PUNCTURE


def c_late_return_with_body_stays_unresolved() -> tuple[object, object]:
    """Поздний возврат, где тело за уровнем БЫЛО (но одно, меньше подтверждения), —
    по-прежнему без вердикта: здесь расколота и классика (Булковски счёл бы первый
    close пробоем, курс требует 2-3 тел — стр. 55), и рисунок стр. 6 не помогает."""
    b = bars((100, 101, 99, 100),
             (99, 99.5, 97, 98.5),
             (97.9, 98.4, 97.2, 97.5),  # ← полное тело ЗА уровнем (одно): open и close < 98
             (98.2, 98.4, 97.6, 98.1),
             (98.1, 99.5, 98.05, 99))   # вернулась на 4-й — позже RETURN_BARS
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.UNRESOLVED


def c_verdict_comes_from_later_approach() -> tuple[object, object]:
    """Заход БЕЗ вердикта уровень не закрывает — вердикт даёт следующий заход.

    Курс правила «решает первый ЗАХОД независимо от вердикта» не даёт: «актуальн» и
    «удал» стоят в нём по одному разу (оба стр. 25) и говорят об ОТРАБОТКЕ, а повторные
    вердикты по одному уровню нарисованы на стр. 25, 28, 31, 43, 45 (касания 1/2/3).

    ⚠ СЛУЧАЙ ДОБАВЛЕН 2026-08-19 и сторожит правку того же дня. До неё `status` брал
    первое СОБЫТИЕ (`first_breach`) и при `UNRESOLVED` держал уровень активным навсегда —
    отсюда наслоение встречных зон (406 пар на кадрах ONDO). Прежние случаи
    `c_puncture_is_worked_off` / `c_breakout_is_flipped` зелены при ОБЕИХ редакциях: у них
    вердикт даёт первый же заход. Этот случай — единственный, который краснеет при откате
    `first_verdict` → `first_breach`: на этих барах первое событие `UNRESOLVED` (то есть
    старый код вернул бы ACTIVE), а вердикт приносит второй заход.
    """
    b = bars((100, 101, 99, 100),
             (99, 99.5, 97, 98.5),       # заход тенью
             (97.9, 98.4, 97.2, 97.5),   # одно тело за уровнем — на пробой не хватает
             (98.2, 98.4, 97.6, 98.1),
             (98.1, 99.5, 98.05, 99),    # возврат позже RETURN_BARS → вердикта нет
             (99, 99.6, 98.6, 99.2),     # цена целиком над уровнем
             (99, 99.2, 97.5, 98.9),     # ВТОРОЙ заход тенью
             (98.9, 99.5, 98.5, 99.3))   # вернулась следующей свечой → прокол
    return status(level(98.0), b).state, LevelState.WORKED_OFF


def c_structure_needs_exit() -> tuple[object, object]:
    """Стр. 23: «пока цена не вышла из структуры — у нас нет уровня».

    Ряд колеблется внутри диапазона и из него не выходит: закрытых структур обязано
    быть НОЛЬ, а незакрытый хвост — виден (§4.3, а не молчание).

    ⚠ ШАГ КОЛЕБАНИЯ ВЗЯТ ИЗ `ZIGZAG_DEPTH` 2026-08-20 — по той же причине, что и в
    `_pair_structure`, и с тем же неизменным ожиданием. Прежний период в четыре бара
    ставил точки в двух барах друг от друга; зигзагу с окном 5 они экстремумами не
    являются, и хвост оказывался пустым — прибор молчал не потому, что структуры нет,
    а потому, что не видел ни одной точки.
    """
    seq: list[tuple[float, float, float, float]] = []
    fill = [(100.0, 103.0, 97.0, 101.0)] * ZIGZAG_DEPTH
    for _ in range(4):
        seq += fill
        seq.append((100.0, 108.0, 99.0, 104.0))    # локальный хай
        seq += fill
        seq.append((104.0, 105.0, 92.0, 100.0))    # локальный лой
    sc = detect_ranges(seq_bars := bars(*seq), detect_swings(seq_bars), TF)  # type: ignore[arg-type]
    return (len(sc.closed), sc.open_tail is not None), (0, True)


def _pair_structure(hi1: float, hi2: float, lo1: float, lo2: float,
                    hi3: float | None = None, lo3: float | None = None) -> list[Bar]:
    """Ряд с точками границ в порядке хай-лой-хай-лой(-хай-лой) и выходом вверх.

    Цены точек — аргументы, чтобы одну и ту же геометрию предъявлять в разном порядке.
    Пятая и шестая точки необязательны: они нужны случаю про прокол, которому по стр. 18
    требуется ТРЕТЬЯ точка стороны — первые две задают границу и проколом быть не могут.

    ⚠ ЗАПОЛНИТЕЛЬ РАЗДВИНУТ С ДВУХ БАРОВ ДО `ZIGZAG_DEPTH` 2026-08-20, И ОЖИДАНИЯ СЛУЧАЕВ
    ПРИ ЭТОМ НЕ ТРОНУТЫ. Владелец сменил примитив разметки с фрактала на зигзаг, а у
    зигзага экстремум обязан быть крайним в окне `ZIGZAG_DEPTH` баров с КАЖДОЙ стороны.
    При двух барах между точками ни одна из них экстремумом не является, и прибор видел
    «структур не найдено вовсе» — то есть падал не на правиле курса, а на РАЗРЕШЕНИИ
    синтетики. Правила стр. 18, 22 и 23 здесь те же, что были; изменилась только длина
    ряда, на котором их предъявляют.

    Заполнитель берётся ИЗ КОНСТАНТЫ, а не числом: при следующей правке `ZIGZAG_DEPTH`
    ряд раздвинется сам, и случай не начнёт молча проверять пустоту.
    """
    fill = [(100.0, 103.0, 97.0, 101.0)] * ZIGZAG_DEPTH
    seq = list(fill)
    seq += [(100.0, hi1, 99.0, 104.0)]        # хай 1  — сквозная точка 1
    seq += fill
    seq += [(104.0, 105.0, lo1, 100.0)]       # лой 1  — сквозная точка 2
    seq += fill
    seq += [(100.0, hi2, 99.0, 104.0)]        # хай 2  — сквозная точка 3
    seq += fill
    seq += [(104.0, 105.0, lo2, 100.0)]       # лой 2  — сквозная точка 4
    seq += fill
    if hi3 is not None:
        seq += [(100.0, hi3, 99.0, 104.0)]    # хай 3  — сквозная точка 5
        seq += fill
    if lo3 is not None:
        seq += [(104.0, 105.0, lo3, 100.0)]   # лой 3  — сквозная точка 6
        seq += fill
    seq += [(100.0, 103.0, 97.0, 101.0)]
    seq += [(109.0, 112.0, 108.5, 111.0)] * 3  # выход вверх тремя полными телами
    return bars(*seq)


def _four_point_structure() -> list[Bar]:
    """Ряд с ровно ЧЕТЫРЬМЯ точками границ и выходом вверх (2 хая + 2 лоя, стр. 22)."""
    return _pair_structure(108.0, 107.0, 92.0, 93.0)


def c_puncture_needs_third_point() -> tuple[object, object]:
    """Стр. 18: «Но если на 3++ точках были проколы за границы - стоп всегда ставится за
    этот прокол».

    ⚠ ОЖИДАНИЕ ПЕРЕСМОТРЕНО 2026-08-11, и вот по какому основанию. Прежняя редакция
    случая ожидала, что проколом становится ВТОРАЯ точка пары, и обосновывала это так:
    «граница стороны — внутренний край её первых двух точек, значит внешняя из пары
    лежит за границей ВСЕГДА». Но это утверждение — следствие нашего решения 2026-08-08,
    а не текст курса. Стр. 18 говорит иначе: «Границы базы (ХАЙ и ЛОЙ) чаще всего
    определяются первыми 2-мя точками», то есть хай базы — максимум её верхних точек.
    При такой границе первые две точки лежат НА ней и проколом быть не могут по
    построению; прокол требует ТРЕТЬЕЙ точки — ровно как и написано «на 3++ точках».

    Порядок прихода пары при этом перестаёт что-либо решать (максимум не зависит от
    порядка), поэтому случай проверяет то, что теперь и есть правило:

      * ряд с третьей верхней точкой ЗА границей (109 при границе 108) — прокол есть;
      * ряд с третьей точкой ВНУТРИ (106) — прокола нет.

    Второй ряд и есть контроль: прибор, отвечающий одинаково на оба, проверку не проходит.
    """
    with_punct = _pair_structure(107.0, 108.0, 93.0, 92.0, hi3=109.0, lo3=91.0)
    without = _pair_structure(107.0, 108.0, 93.0, 92.0, hi3=106.0, lo3=94.0)
    got: list[object] = []
    for b in (with_punct, without):
        sc = detect_ranges(b, detect_swings(b), TF)  # type: ignore[arg-type]
        if not sc.closed:
            got.append("структуры нет")
            continue
        acc = sc.closed[0]
        got.append((acc.upper.edge, acc.lower.edge, acc.upper.puncture, acc.lower.puncture))
    return tuple(got), (
        (108.0, 92.0, 109.0, 91.0),   # третья точка за границей — прокол идёт в стоп
        (108.0, 92.0, None, None),    # третья точка внутри — прокола нет
    )


def c_min_points_threshold_bites() -> tuple[object, object]:
    """Стр. 22 «4 и более точек» и стр. 23 «(4+6+ точки границ)» — курс называет ОБА числа.

    Проверка комплектности при умолчании 4 НЕДОСТИЖИМА по построению: цикл не доходит до
    выхода, пока у каждой стороны меньше двух точек, значит на строке проверки сумма
    всегда ≥ 4. Именно так её и нашёл разбор (Р-1): комментарий описывал поведение,
    которого не бывает, а счётчик отвергнутых начал по этой причине не мог вырасти.

    Поэтому здесь проверяется САМО ПРАВИЛО, а не умолчание: порог берётся на единицу выше
    того, сколько точек в ряду реально нашлось, и структура обязана быть отвергнута.
    Ожидаемый ответ (ноль структур, начало отвергнуто) задан правилом курса, а не снят с
    текущего поведения — из кода взято только число точек ряда.
    """
    b = _four_point_structure()
    sw = detect_swings(b)
    base = detect_ranges(b, sw, TF)  # type: ignore[arg-type]
    if not base.closed:
        return "структур не найдено вовсе", "одна структура при умолчании"
    points = base.closed[0].points
    strict = detect_ranges(b, sw, TF, min_points=points + 1)  # type: ignore[arg-type]
    return (
        (len(base.closed), points >= MIN_BOUNDARY_POINTS, len(strict.closed), strict.rejected > 0),
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


def c_flipped_level_is_target_of_new_side() -> tuple[object, object]:
    """Стр. 43 + стр. 24: пробитый уровень — уровень НОВОЙ стороны, и как встречный он ЦЕЛЬ.

    ⚠ Добавлен 2026-08-24 вместе с `levels.level_as_of` (разбор критической ошибки целей
    по требованию владельца). До этого дня пул целей выбрасывал пробитые уровни целиком,
    как отработанные, — читал стр. 43 («Уровень лонг/шорт менятся для нас на
    противоположный») как стр. 25 («мы этот уровень удаляем»). Пробитый вниз лонг-уровень
    — это сопротивление НАД ценой, то есть ровно «шорт уровень», который стр. 24 называет
    целью лонга.

    Три плеча:
      * ЛОНГ-уровень на 118, пробитый к as_of (стал шортом), — цель лонга от 100;
      * тот же уровень, но АКТИВНЫЙ (сторона всё ещё лонг), — НЕ цель: сторона своя;
      * тот же уровень, но ОТРАБОТАННЫЙ к as_of, — НЕ цель: стр. 25 удаляет, а не
        переворачивает.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    as_of = src.created_at_ms
    born = level(118.0, LevelSide.LONG, created=1, born_ms=T0)
    flipped = mapped(born, LevelState.FLIPPED, resolved_at_ms=as_of - STEP)
    active = mapped(born)
    worked = mapped(born, LevelState.WORKED_OFF, resolved_at_ms=as_of - STEP)
    return (
        (tuple(float(t.price) for t in build_targets(src, (flipped,))),
         tuple(float(t.price) for t in build_targets(src, (active,))),
         tuple(float(t.price) for t in build_targets(src, (worked,)))),
        ((118.0,), (), ()),
    )


def c_targets_as_of_decision() -> tuple[object, object]:
    """Дата отбора целей = МОМЕНТ РЕШЕНИЯ, а не рождение уровня (правка 2026-08-24).

    Сигнал уходит в леджер с датой записи; цели, отобранные по миру на момент рождения
    уровня (на живых данных — медиана 101 бар в прошлом), были бы второй сущностью под
    именем «момент сигнала». Два плеча:
      * цель, родившаяся ПОСЛЕ рождения уровня, но ДО решения, — законна (стр. 24
        живёт в мире, где уровни продолжают рождаться: «ближайшего нового
        сформированного уровня»);
      * цель, родившаяся ПОСЛЕ решения, — не существует и целью не является.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    later = level(118.0, LevelSide.SHORT, created=40,
                  born_ms=src.created_at_ms + 10 * STEP)
    as_of_yes = src.created_at_ms + 20 * STEP
    as_of_no = src.created_at_ms + 5 * STEP
    return (
        (tuple(float(t.price) for t in
               build_targets(src, (mapped(later),), as_of_ms=as_of_yes)),
         tuple(float(t.price) for t in
               build_targets(src, (mapped(later),), as_of_ms=as_of_no))),
        ((118.0,), ()),
    )


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


def c_target_outside_own_structure() -> tuple[object, object]:
    """Стр. 24: цель — ДРУГОЙ уровень, а не цена внутри своей же базы.

    ⚠ Случай добавлен 2026-08-24 по замечанию владельца о корректности расчёта целей.
    Замер того дня (Crypto.com, 23 символа, ТФ 15м/1ч/4ч по 300 баров): у 4 целей из 4,
    взятых из пула, цена цели лежала ВНУТРИ коробки уровня, от которого открывалась
    сделка. Пример: AAVE 1ч, структура входа 85.15…88.013, цель 87.983. Стоп при этом
    ставится за ВСЮ эту структуру — то есть система рисковала целой базой ради куска
    той же базы, и отсюда медиана РР 0.401 при курсовом «золотом стандарте 1к3».

    Прежний фильтр мерил ЗОНОЙ (узкой полосой вокруг ПОК), а стоп — коробкой: две
    разные мерки в одной сделке. Рисунок стр. 24 не оставляет выбора: уровень входа там
    маленькая коробка внизу, «ЦЕЛЬ уровень того же тф» — отдельная коробка много выше.

    Оба плеча обязательны:
      * уровень ВНУТРИ коробки входа (89…111 у базы 90…110) целью НЕ становится;
      * уровень СНАРУЖИ (118) — становится, иначе прибор просто запретил бы все цели.
    """
    src = level(100.0, LevelSide.LONG, created=5)   # база 90…110
    inside = mapped(level(105.0, LevelSide.SHORT, created=1, born_ms=T0))
    outside = mapped(level(118.0, LevelSide.SHORT, created=1, born_ms=T0))
    got = build_targets(src, (inside, outside))
    return tuple(float(t.price) for t in got), (118.0,)


def c_no_boundary_target_for_level_trade() -> tuple[object, object]:
    """Стр. 19 — это ТОРГОВЛЯ ВО ФЛЕТЕ, и её тейк по границе сделке от уровня НЕ цель.

    ⚠ Случай добавлен 2026-08-24 ПОСЛЕ СОБСТВЕННОЙ ОШИБКИ. В тот день я выдал границу
    своей базы целью обычной сделки, опершись на дословную цитату стр. 19 («По верхней
    границе – делаете тейк 50% - но не 100%»), и замер это подтверждал: сумма R по 132
    уровням −50.33 → −16.22. Числа улучшились, а сделка подменилась: рисунок стр. 19
    показывает ход ВНУТРИ коробки от границы к границе со входом У ГРАНИЦЫ, тогда как
    `build_targets` обслуживает сделку стр. 24/30 со входом на ПОК законченного
    накопления. Случай стоит здесь, чтобы правку не завели во второй раз.

    Пул ПУСТ — целей нет вовсе, и это законный ответ (§4.3), а не пробел.
    """
    got = build_targets(level(100.0, LevelSide.LONG, created=5), ())
    return tuple(float(t.price) for t in got), ()


def c_target_structure_in_frame() -> tuple[object, object]:
    """Чистка пула целей (решение владельца «делай 1+2» 2026-08-18, развилка 2
    раздела 6 протокола signals-trader-audit-2026-08-18): цель со структурой старше
    кадра 180 баров СВОЕГО ТФ на момент рождения сделки отсеивается; моложе — живёт.

    Оба плеча обязательны: прибор, отвечающий одинаково на свежую и протухшую
    структуру, не проверяет ничего. Третье плечо — исключение §4.3: уровень без
    записанного окна структуры (`to_ms == 0`, строки карты до появления поля) НЕ
    выбрасывается молча.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    as_of = src.created_at_ms
    frame = TARGET_STRUCTURE_FRAME_BARS * STEP
    stale = level(115.0, LevelSide.SHORT, created=1, born_ms=T0).model_copy(
        update={"structure_to_ms": as_of - frame - STEP})
    fresh = level(118.0, LevelSide.SHORT, created=1, born_ms=T0).model_copy(
        update={"structure_to_ms": as_of - STEP})
    no_window = level(121.0, LevelSide.SHORT, created=1, born_ms=T0).model_copy(
        update={"structure_to_ms": 0})
    got = build_targets(src, (mapped(stale), mapped(fresh), mapped(no_window)))
    return tuple(float(t.price) for t in got), (118.0, 121.0)


def c_stop_anchor_capped_by_base() -> tuple[object, object]:
    """Полоса якоря не дальше высоты базы (решение владельца «делай 1+2» 2026-08-18,
    развилка 1 раздела 6 протокола signals-trader-audit-2026-08-18).

    Три плеча, чтобы прибор не был заперт в одном ответе:
      * без `base_height` свинг на 4% от границы — якорь (прежнее поведение);
      * при базе высотой 1% цены та же полоса схлопывается (ближний край 2% ближе
        не двигается) — якоря НЕТ, стоп уходит на запас/прокол;
      * при базе высотой 10% цены свинг на 4% остаётся в полосе — якорь есть.
    """
    boundary = 100.0
    zone = BoundaryZone(edge=boundary, lo=99.5, hi=100.5, point_indices=(0, 1))
    swing = (96.0,)  # 4% ниже границы — внутри полосы 2-5%
    free = stop_anchor(LevelSide.LONG, boundary, zone, (), swing)
    narrow = stop_anchor(LevelSide.LONG, boundary, zone, (), swing, base_height=1.0)
    wide = stop_anchor(LevelSide.LONG, boundary, zone, (), swing, base_height=10.0)
    return (
        (None if free is None else float(free.price), narrow,
         None if wide is None else float(wide.price)),
        (96.0, None, 96.0),
    )


def c_stop_hidden_beyond_anchor() -> tuple[object, object]:
    """Стоп прячется ЗА якорь с запасом, а не ставится на его цену (приказ владельца
    «делай все согласно курсу» 2026-08-18).

    Курс: «СТОП прятать с запасом за структуру» (стр. 19, дословно; о том же стр. 33,
    36, 58); прежний стоп ровно на цене якоря источника не имел. Добавка — ближний
    край 1% от цены якоря, две рамки: потолок высотой базы и, для якоря НЕ-прокола,
    внешний край полосы 5% от границы (стр. 18). Поверх всего — ПОЛ: стоп не ближе
    курсового запаса 3% за границей (приказ владельца «стоп 3-5% за структуру
    ставится!» 2026-08-18). Шесть плеч, чтобы прибор не был заперт в одном ответе:
      * добавка целиком: якорь 88 в полосе, база широкая → 88 − 0.88 = 87.12
        (пол 90 − 2.7 = 87.3 БЛИЖЕ якорного стопа — не вмешивается);
      * потолок высоты: база 0.5, якорь 96, добавка 0.96 срезана до 0.5 → 95.5
        (пол 96.709 ближе — не вмешивается);
      * пол перебивает ближний якорь: та же база, якорь 98 → якорный стоп 97.5
        ближе трёх процентов за границей 99.7 → стоп на полу 99.7·0.97 = 96.709;
      * кап полосы: якорь 85.6 у края полосы (внешний край 85.5) → стоп 85.5;
      * кап режет добавку, но не якорь: свинг-якорь 85 ЗА полосой → стоп на якоре;
      * прокол полосе не подчинён: тот же якорь 85 проколом → 85 − 0.85 = 84.15.
    """
    full_add = build_setup(level(100.0, LevelSide.LONG),
                           structural_anchor=Decimal("88"))
    height_cap = build_setup(level(100.0, LevelSide.LONG, lo=99.7, hi=100.2),
                             structural_anchor=Decimal("96"))
    floor_wins = build_setup(level(100.0, LevelSide.LONG, lo=99.7, hi=100.2),
                             structural_anchor=Decimal("98"))
    band_cap = build_setup(level(100.0, LevelSide.LONG),
                           structural_anchor=Decimal("85.6"))
    swing_far = build_setup(level(100.0, LevelSide.LONG).model_copy(update={
        "stop_anchor": Decimal("85"),
        "stop_anchor_source": StopAnchorSource.SWING,
    }))
    puncture_far = build_setup(level(100.0, LevelSide.LONG).model_copy(update={
        "stop_anchor": Decimal("85"),
        "stop_anchor_source": StopAnchorSource.PUNCTURE,
    }))
    return (
        (float(full_add.stop), float(height_cap.stop), float(floor_wins.stop),
         float(band_cap.stop), float(swing_far.stop), float(puncture_far.stop)),
        (87.12, 95.5, 96.709, 85.5, 85.0, 84.15),
    )


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
    По курсу это уже не «следующая свеча», значит и не прокол-по-времени. Это Р-2
    разбора. Бар за уровнем — ПОЛНЫМ ТЕЛОМ, чтобы случай не пересекался с правилом
    2026-08-09 «поздний возврат без тел — прокол по классике»: с телом за уровнем
    поздний возврат остаётся без вердикта, и время здесь — единственное, что отличает
    его от прокола.
    """
    b = bars((100, 101, 99, 100),
             (97.9, 97.95, 97, 97.2),   # ушла за уровень ПОЛНЫМ телом
             (99, 101, 98.5, 100.5),    # ← между ним и предыдущим ДЫРА в 5 баров
             gap_at=2)
    ev = first_breach(b, 98.0, Direction.BELOW, TF, from_index=1)
    return (ev.kind if ev else None), BreachKind.UNRESOLVED


def c_pennant_stop_has_margin() -> tuple[object, object]:
    """Стр. 58: «Стоп всегда прячем за всю структуру с запасом 1-3%».

    что из чего следует: край структуры и сторона фигуры → цена стопа. Слово «всегда»
    безусловно, и стр. 58 — это страница самого треугольника, а не соседняя тема.

    ⚠ Случай ЗАВЕДЁН 2026-08-24 по дефекту, а не для полноты. `figures.pennant` отдавал
    наружу только `stop_anchor` — край структуры, — и запаса к нему не прибавлял НИКТО:
    докстрока модуля поручала это `geometry`, где слова «вымпел» нет ни разу. Карточка
    печатала край структуры под словом «стоп»: на живом прогоне 2026-08-24 так стояли
    все 48 вымпелов. Это третий случай одного класса — тот же дефект чинился у уровня
    2026-08-18 и у ПП 2026-08-19.

    Два плеча, чтобы прибор не был заперт в одном ответе, и они же ловят обращение
    правила: у ЛОНГА стоп ниже нижнего края (92 − 2.76 = 89.24), у ШОРТА выше верхнего
    (108 + 3.24 = 111.24). Пропажа запаса даёт 92 и 108, обмен сторон — 89.24 и 111.24
    местами; ни то, ни другое не совпадает с ожиданием.

    Тренд подаётся `senior`-приоритетом: своего у короткого ряда нет (стр. 47 разрешает
    брать его со старшего ТФ), а без тренда стр. 57 вымпела не даёт вовсе.
    """
    b = _pair_structure(112.0, 110.0, 88.0, 90.0, 108.0, 92.0)
    sw = detect_swings(b)
    st = detect_ranges(b, sw, TF).closed[0]  # type: ignore[arg-type]
    long_pen = pennant(st, sw,  # type: ignore[arg-type]
                       Priority(timeframe="1d", direction=TrendDirection.UP, holds_for=3))
    short_pen = pennant(st, sw,  # type: ignore[arg-type]
                        Priority(timeframe="1d", direction=TrendDirection.DOWN, holds_for=3))
    assert not isinstance(long_pen, NotReady) and not isinstance(short_pen, NotReady)
    return (
        (long_pen.stop_anchor, round(long_pen.stop_price, 6),
         short_pen.stop_anchor, round(short_pen.stop_price, 6)),
        (92.0, 89.24, 108.0, 111.24),
    )


def c_no_target_names_reason() -> tuple[object, object]:
    """Сделка без цели НАЗЫВАЕТ причину со знаменателем (§4.3).

    Заведено 2026-08-24 по слову владельца: «Сделок без цели быть не может. Это
    означает ошибка в расчетах, геометрии, структурах, поках, объемах или во всем
    вместе!» Карточка печатала «целей нет» без причины, и пустой пул уровней старших
    ТФ (отказы покрытия профиля) читался как свойство рынка.

    Три плеча, чтобы прибор не был заперт в одном ответе:
      * пул пуст → причина «в карте нет ни одного другого уровня»;
      * пул есть, но встречной стороны нет → причина называет живых и их сторону;
      * годная цель есть → целей 1, причина ПУСТА.
    """
    src = level(100.0, LevelSide.LONG, created=5)
    empty = build_setup(src)
    same_side = build_setup(src, (mapped(level(118.0, LevelSide.LONG, created=1,
                                               born_ms=T0)),))
    with_target = build_setup(src, (mapped(level(118.0, LevelSide.SHORT, created=1,
                                                 born_ms=T0)),))
    return (
        (len(empty.targets), empty.no_target_reason,
         len(same_side.targets), same_side.no_target_reason,
         len(with_target.targets), with_target.no_target_reason),
        (0, "в карте нет ни одного другого уровня",
         0, "живых уровней 1, встречной стороны среди них нет",
         1, ""),
    )


CASES: list[tuple[str, Callable[[], tuple[object, object]]]] = [
    ("прокол идёт в стоп с 3-й СКВОЗНОЙ точки (стр. 18)", c_puncture_needs_third_point),
    ("прокол — отработка уровня (стр. 6, 55)", c_puncture),
    ("пробой — два полных тела (стр. 55, 43)", c_breakout),
    ("цена за уровень не заходила — уровень жив (стр. 23)", c_no_event),
    ("прокол → уровень отработан (стр. 25)", c_puncture_is_worked_off),
    ("пробой → уровень флипнут (стр. 43)", c_breakout_is_flipped),
    ("флипнутый уровень СМЕНИЛ сторону (стр. 43)", c_flipped_level_changes_side),
    ("поздний возврат без тел — прокол по классике (стр. 6 + §0.1)",
     c_late_return_no_bodies_is_puncture),
    ("поздний возврат С телом — вердикта нет (стр. 55)",
     c_late_return_with_body_stays_unresolved),
    ("вердикт даёт ВТОРОЙ заход, первый его не дал (стр. 25, 28, 31, 43, 45)",
     c_verdict_comes_from_later_approach),
    ("нет выхода из структуры — нет уровня (стр. 23)", c_structure_needs_exit),
    ("порог точек границ срабатывает (стр. 22, 23)", c_min_points_threshold_bites),
    ("исход по цели = +2R (стр. 9)", c_outcome_target),
    ("исход по стопу = −1R (стр. 9)", c_outcome_stop),
    ("стоп и цель в одном баре — вердикта нет (§4.3)", c_outcome_ambiguous),
    ("цена не дошла до входа — это не убыток (стр. 30)", c_outcome_not_filled),
    ("цель — ДРУГОЙ уровень, не цена внутри своей базы (стр. 24)",
     c_target_outside_own_structure),
    ("тейк по границе — это флэт стр. 19, а не сделка от уровня",
     c_no_boundary_target_for_level_trade),
    ("пробитый уровень — цель НОВОЙ стороной (стр. 43, 24)",
     c_flipped_level_is_target_of_new_side),
    ("цели отбираются на момент РЕШЕНИЯ", c_targets_as_of_decision),
    ("цель не может появиться позже сигнала (стр. 23)", c_target_not_from_future),
    ("снятый уровень целью не является (стр. 25, 43)", c_target_not_retired),
    ("снятый ПОЗЖЕ сигнала — целью являлся (стр. 25)", c_target_retired_later_still_counts),
    ("исход только по будущим барам (§8 этап 7)", c_outcome_only_future_bars),
    ("цель со структурой старше кадра 180 баров — не цель (решение 2026-08-18)",
     c_target_structure_in_frame),
    ("якорь стопа не дальше высоты базы (решение 2026-08-18)",
     c_stop_anchor_capped_by_base),
    ("стоп прячется ЗА якорь с запасом, а не на его цене (стр. 18 + 33/58)",
     c_stop_hidden_beyond_anchor),
    ("«следующая свеча» — это время, а не индекс (стр. 55)", c_two_candles_are_time_not_index),
    ("стоп вымпела — за структуру С ЗАПАСОМ, а не на её краю (стр. 58)",
     c_pennant_stop_has_margin),
    ("сделка без цели называет ПРИЧИНУ со знаменателем (§4.3)",
     c_no_target_names_reason),
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
