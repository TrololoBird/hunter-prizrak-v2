"""Накопление §2.1 и выход из него §2.2. Источник — мини-курс, стр. 13, 18, 22, 23, 55.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Разбор источника с цитатами: docs/audit/course-reading-2026-08-03.md

Механика, взятая дословно:
  стр. 13  флэт — «локальные хаи/лои повторяются и не меняются» → точка границы это фрактал
  стр. 18  «границы базы (хай и лой) чаще всего определяются первыми 2-мя точками»
  стр. 18  при проколе за границу «стоп всегда ставится за этот прокол» — это про СТОП;
           саму границу прокол не двигает, и это видно на схемах: стр. 13 (проколы у точек
           3, 7, 10, 11 получили обычные номера, коробка осталась на прежней цене), стр. 19,
           36, 40. Заморозка зоны — требование КУРСА, а не наше умолчание.
  стр. 18, 21, 23, 35, 38, 41 — РИСУНОК: точки границ строго чередуют сторону.
           На стр. 21 в каждой коробке ровно четыре точки: 1 и 3 у одной границы, 2 и 4 у
           противоположной, то есть во времени сторона меняется на каждой точке. Словами
           это правило в курсе не записано нигде — оно живёт только в геометрии схем.
  стр. 22  «4 и более точек» в рамках своего ТФ
  стр. 23  «пока цена не вышла из структуры — у нас нет уровня»
  стр. 55  подтверждение = «2-3 полных тел свечей ЭТОГО ТФ» за уровнем;
           возврат той же или следующей свечой — прокол БЕЗ подтверждения

Свободных порогов здесь нет: ширина границы приходит из первых двух точек, а не из
константы. Обоснование — Р-6 в разборе источника.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .bars import steps_between
from .breach import CONFIRM_BODIES, Direction, body_beyond
from .models import Bar
from .swings import SwingKind, SwingSet


class BorderSource(StrEnum):
    """Откуда взялась цена границы. Разделено 2026-08-08 и вот почему.

    ⚠ ЗНАЧЕНИЯ FOREIGN здесь БОЛЬШЕ НЕТ, и это откат, а не упущение. Граница с
    другого ТФ (стр. 39, 46, 54) была внесена 2026-08-08 и в тот же день снята:
    критерий «чужая цена попала между первыми двумя точками» не прошёл контроль
    CLAUDE.md «тот же замер на заведомо неверных данных». Доля границ, взятых у
    чужого ТФ: BTC 30% на настоящих уровнях против 25% на равномерной решётке и 19%
    на случайных ценах; ETH 22% против 23% НА СДВИНУТЫХ — то есть на заведомо
    неверных ценах вышло БОЛЬШЕ, чем на настоящих. Прибор не различал уровень и
    случайную цену, потому что анкеров подавалось 665-675 на символ и в полосу
    всегда что-нибудь попадало.

    Требование курса при этом в силе: чужой уровень границей БЫВАЕТ. Неверным было
    моё правило «любой анкер внутри полосы» — курс такого условия не даёт. Вернуть
    можно только с избирательным критерием, прошедшим тот же контроль.
    Разбор: docs/audit/accumulation-projects-2026-08-07.md
    """

    OWN = "own"
    """Свои свинги: внутренний край первых двух точек стороны (стр. 18)."""

    LADDER = "ladder"
    """Ребро предыдущей структуры ЭТОГО ряда — лесенка (стр. 40)."""


MIN_BOUNDARY_POINTS = 4
"""Стр. 22: «4 и более точек»; стр. 23: «(4+6+ точки границ)»; стр. 24: «(4+
точки границ)». Курс называет только это число."""


class BoundaryZone(BaseModel):
    """Граница базы: ОДНА ЦЕНА `edge` плюс допуск приёма точек `lo…hi` (стр. 18).

    ⚠ Так стало 2026-08-08. Раньше границей считалась вся полоса, и наружу отдавался её
    ДАЛЬНИЙ край. Курс рисует границу базы одной тонкой линией — стр. 13, 18, 21, 23, 35,
    39; полоса в курсе есть у ПОК (стр. 30) и у уровня ПП (стр. 55), но не у ребра базы.
    На стр. 39 низ коробки выглядит двумя линиями, однако вторая — это ПОК стопового
    объёма, унаследованный как граница, а не толщина ребра.

    Какая именно цена. Стр. 13 определяет флэт так, что локальные хаи и лои ПОВТОРЯЮТСЯ:
    уровень, на котором обе первые точки стороны побывали, — это их ВНУТРЕННИЙ край
    (для верха меньший из двух хаёв, для низа больший из двух лоёв). Проверяется по стр. 13:
    нижняя линия проходит по точке 2, а точка 4 лежит НИЖЕ неё, то есть прокол; если бы
    границей был дальний край, точка 4 лежала бы на линии.

    Отвергнутое прочтение — граница это ПЕРВАЯ точка стороны. На стр. 13 оно даёт тот же
    ответ и потому не опровергнуто; выбран внутренний край, потому что текст стр. 18 говорит
    о ДВУХ точках, а не об одной. Если владелец решит иначе, менять здесь одну строку.

    Заморозка — не оптимизация, а требование стр. 13: «локальные хаи/лои повторяются и
    НЕ МЕНЯЮТСЯ». Первая редакция расширяла зону каждым новым экстремумом, и замер
    2026-08-04 показал, к чему это ведёт: на тренде BTC 15м накопилось 39 верхних точек
    против 6 нижних, «граница» расползлась на 1.82%, структура не закрывалась 316 баров
    подряд, а `detect` об этом молчал. Протокол: docs/audit/stuck-structure-2026-08-04.md
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge: float
    """САМА граница — одна цена. За неё считается выход и за неё меряется прокол."""

    source: BorderSource = BorderSource.OWN
    """Откуда взялась цена границы: свои точки, лесенка или чужой ТФ.

    На схеме стр. 40 верхнее ребро прежней коробки служит нижним ребром следующей, и
    обе подписаны «Структура 4ч тф» — то есть лесенка складывается внутри одного ряда.
    Курс называет и другие чужие границы: ПОК прежнего стопового объёма (стр. 39) и
    уровень старшего ТФ (стр. 46, 54). Они были внесены и ОТКАЧЕНЫ 2026-08-08 —
    причина в докстроке BorderSource.
    """

    narrowed: int = 0
    """Сколько раз граница сдвинулась ВНУТРЬ — признак накопления в сужении (стр. 34).

    Стр. 34 называет базой и такую форму: сужение, поджатие и чёткие границы стоят там
    в одном ряду. На стр. 37 у базы в сужении границы нарисованы двумя НАКЛОННЫМИ
    сходящимися линиями, по три касания каждая, и выход из клина торгуется как обычный
    выход из накопления.

    Ноль означает «граница ни разу не двигалась» — обычная горизонтальная база."""

    lo: float
    hi: float
    point_indices: tuple[int, ...] = Field(min_length=2)

    puncture: float | None = None
    """Самый дальний заход ЗА зону без подтверждения выхода.

    Стр. 18: «если на 3++ точках были проколы за границы — стоп всегда ставится за этот
    прокол». `None` означает «проколов не было», а не «неизвестно»: продюсер здесь один
    и он всегда отрабатывает.
    """

    @property
    def width_pct(self) -> float:
        """Ширина ДОПУСКА приёма точек в процентах. Это не граница — граница это `edge`.

        Курс задаёт критерий «чётко видно структуру» визуально; механического порога
        он не даёт, поэтому число сообщается наружу для калибровки на корпусе (этап 3),
        а не превращается в выдуманную отсечку.
        """
        mid = (self.lo + self.hi) / 2
        return 0.0 if mid == 0 else (self.hi - self.lo) / mid * 100


class StructureExit(BaseModel):
    """Полноценный выход из структуры (стр. 23) = подтверждение по стр. 55."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: Direction
    first_body_index: int
    confirmed_at_index: int
    """Бар, на котором набралось CONFIRM_BODIES тел подряд.

    Числа «сколько всего тел вышло» здесь нет умышленно: на момент подтверждения оно
    всегда равно CONFIRM_BODIES, а узнать продолжение можно только заглянув вперёд (I-5).
    Первая редакция несла такое поле, и живой прогон показал в нём 2 у всех 16 структур.
    """


class Accumulation(BaseModel):
    """Закрытая структура: границы известны, выход подтверждён.

    Незакрытых структур этот модуль не отдаёт — стр. 23: «пока цена не вышла из
    структуры, у нас нет уровня».
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    first_index: int
    last_index: int
    upper: BoundaryZone
    lower: BoundaryZone
    exit: StructureExit

    @property
    def points(self) -> int:
        return len(self.upper.point_indices) + len(self.lower.point_indices)

    @property
    def is_long(self) -> bool:
        """Лонговое накопление — из которого цена вышла вверх (стр. 22)."""
        return self.exit.direction is Direction.ABOVE


class OpenStructure(BaseModel):
    """Структура, из которой цена ещё не вышла. Уровня по стр. 23 у неё нет.

    Существует, чтобы её было ВИДНО (§4.3). Незакрытая структура — это не «ничего не
    нашли»: она может стоять сутками, и молчать о ней значит выдавать пустоту за покой.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_index: int
    bars_open: int
    upper: BoundaryZone
    lower: BoundaryZone

    @property
    def points(self) -> int:
        return len(self.upper.point_indices) + len(self.lower.point_indices)


class AccumulationScan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    closed: tuple[Accumulation, ...]
    open_tail: OpenStructure | None
    bars_scanned: int
    resets: int
    """Сколько раз структура распалась, не дав уровня (зоны сошлись либо точек не хватило)."""


def detect(
    bars: list[Bar],
    swings: SwingSet,
    timeframe: str,
    *,
    min_points: int = MIN_BOUNDARY_POINTS,
    confirm_bodies: int = CONFIRM_BODIES,
) -> AccumulationScan:
    """Накопления, из которых цена уже вышла, плюс незакрытый хвост.

    Проход строго слева направо по закрытым барам. Фрактал участвует только с того
    бара, на котором он стал известен (`confirmed_at_index`) — иначе структура
    опиралась бы на будущее (I-5).

    Граница может быть УНАСЛЕДОВАНА у предыдущей структуры ряда — «лесенка» стр. 40:
    ребро, через которое цена вышла, становится противоположным ребром следующей базы,
    если оно попадает между её первыми двумя точками этой стороны. Такая граница
    может оказаться и ДАЛЬШЕ собственного края — на стр. 40 рёбра соседних коробок
    совпадают, а не поджимаются друг к другу. Уехать за пределы первых двух точек она
    при этом не может: анкер ищется строго между ними.

    ⚠ Формулировка исправлена 2026-08-08. Раньше здесь стояло «граница НИКОГДА не
    расширяется», и это опровергалось кодом тремя строками ниже: унаследованное ребро
    почти всегда лежит НЕ БЛИЖЕ внутреннего края пары. Запрет на расширение относится
    только к ПРОКОЛУ (стр. 13, 18): прокол получает номер, но линию не двигает.

    Сужение разрешено стр. 34 и засчитывается, только когда сходятся ОБЕ стороны:
    ПРИШЕДШИЙ хай ниже последнего принятого хая ПРИ том, что лои растут (и наоборот).
    Одностороннее движение внутрь сужением не является — это обычное касание внутри
    коробки, которое курс на стр. 21 не нумерует вовсе.

    Точки границ ЧЕРЕДУЮТ сторону (стр. 21 и ещё двенадцать схем): после верхней точки
    принимается только нижняя и наоборот. Границы каждой стороны задаются ПЕРВЫМИ ДВУМЯ
    её точками — на стр. 21 это 1 и 3 у одной границы, 2 и 4 у противоположной, — и дальше
    не двигаются (стр. 18). Экстремум за зоной — прокол: он считается точкой границы и
    запоминается для стопа, но зону НЕ расширяет. Выход проверяется против замороженной
    границы.

    Пороги вынесены в аргументы НЕ для настройки: умолчания взяты из курса и меняться
    не должны. Они вынесены, чтобы чувствительность результата к ним можно было
    ЗАМЕРИТЬ — курс даёт «4+» и «2-3», то есть диапазоны, и без замера выбор внутри
    диапазона остаётся магическим числом.
    """
    by_confirm: dict[int, list[tuple[SwingKind, int, float]]] = {}
    for s in swings.swings:
        by_confirm.setdefault(s.confirmed_at_index, []).append((s.kind, s.index, s.price))

    found: list[Accumulation] = []
    hi_px: list[float] = []
    hi_idx: list[int] = []
    lo_px: list[float] = []
    lo_idx: list[int] = []
    hi_punct: float | None = None
    lo_punct: float | None = None
    run_dir: Direction | None = None
    run_from = 0
    resets = 0
    last_kind: SwingKind | None = None
    up_edge: float | None = None
    lo_edge: float | None = None
    up_narrowed = 0
    lo_narrowed = 0
    up_source = BorderSource.OWN
    lo_source = BorderSource.OWN
    ladder_up: float | None = None
    ladder_lo: float | None = None

    def reset() -> None:
        nonlocal run_dir, run_from, hi_punct, lo_punct, resets, last_kind
        nonlocal up_edge, lo_edge, up_narrowed, lo_narrowed
        nonlocal up_source, lo_source
        hi_px.clear()
        hi_idx.clear()
        lo_px.clear()
        lo_idx.clear()
        hi_punct = lo_punct = None
        run_dir, run_from = None, 0
        resets += 1
        last_kind = None
        up_edge = lo_edge = None
        up_narrowed = lo_narrowed = 0
        up_source = lo_source = BorderSource.OWN

    def upper_zone() -> tuple[float, float]:
        return min(hi_px[:2]), max(hi_px[:2])

    def lower_zone() -> tuple[float, float]:
        return min(lo_px[:2]), max(lo_px[:2])

    def converging(price: float, kind: SwingKind) -> bool:
        """Продолжает ли ПРИШЕДШАЯ точка сужение обеих сторон (стр. 34, 37).

        ⚠ Исправлено 2026-08-08. Прежняя редакция звалась без аргументов и смотрела
        на УЖЕ принятую пару `hi_px[-1] < hi_px[-2]`, то есть решала судьбу новой
        точки по двум старым. Из-за этого распознавание клина зависело от ПОРЯДКА
        прихода первых двух точек, а не от геометрии: те же цены хаёв [110,109,108,
        107] при лоях [90,91,92,93] давали сужений 4 и восемь точек, а [109,110,108,
        107] — ноль сужений и четыре точки, точки 5-8 выбрасывались целиком.
        """
        if len(hi_px) < 2 or len(lo_px) < 2:
            return False
        if kind is SwingKind.HIGH:
            return price < hi_px[-1] and lo_px[-1] > lo_px[-2]
        return price > lo_px[-1] and hi_px[-1] < hi_px[-2]

    for k in range(len(bars)):
        for kind, index, price in by_confirm.get(k, []):
            if kind is last_kind:
                # Точки границ ЧЕРЕДУЮТ сторону: стр. 21 нумерует 1 и 3 у одной границы,
                # 2 и 4 у противоположной, то есть во времени идёт верх-низ-верх-низ. Двух
                # точек подряд с одной стороны нет ни на одной схеме курса (стр. 13, 18, 19,
                # 21, 23, 24, 28, 35, 38, 39, 41, 49, 54 — тринадцать схем без исключения).
                #
                # ⚠ Правило взято с РИСУНКА: словами курс его не формулирует. Оно всё равно
                # обязательно — рисунок в этом курсе такой же источник, как абзац.
                # Обзор: docs/audit/accumulation-projects-2026-08-07.md
                continue
            if kind is SwingKind.HIGH:
                if len(hi_px) < 2:
                    hi_px.append(price)
                    hi_idx.append(index)
                    last_kind = kind
                    if len(hi_px) == 2:
                        up_edge = min(hi_px)
                        if (ladder_up is not None
                                and min(hi_px) <= ladder_up <= max(hi_px)):
                            # Лесенка (стр. 40): цена вышла ВНИЗ из прежней структуры,
                            # и её нижнее ребро стало верхним ребром этой.
                            up_edge = ladder_up
                            up_source = BorderSource.LADDER
                        # Та из первых двух точек, что лежит ЗА границей, — прокол
                        # (стр. 18: «стоп всегда ставится за этот прокол»). При
                        # границе-внутреннем-крае внешняя точка пары лежит за ней ПО
                        # ПОСТРОЕНИЮ, и ровно это нарисовано на стр. 13: нижняя линия
                        # идёт по точке 2, а точка 4 лежит ниже неё.
                        # ⚠ Добавлено 2026-08-08: раньше эта ветка уходила по
                        # `continue` мимо учёта прокола, и стоп вставал ближе, чем
                        # цена уже ходила внутри базы.
                        beyond = [p for p in hi_px[:2] if p > up_edge]
                        if beyond:
                            hi_punct = (max(beyond) if hi_punct is None
                                        else max(hi_punct, max(beyond)))
                    continue
                assert up_edge is not None
                if price < up_edge:
                    if not converging(price, kind):
                        continue  # касание внутри базы — курс его не нумерует (стр. 21)
                    # Обе стороны сошлись: база в сужении (стр. 34), и верхняя граница
                    # едет ВНИЗ вслед за точкой. Наружу граница не двигается никогда.
                    hi_px.append(price)
                    hi_idx.append(index)
                    last_kind = kind
                    up_edge = price
                    up_narrowed += 1
                    continue
                hi_px.append(price)
                hi_idx.append(index)
                last_kind = kind
                if price > up_edge:
                    # Прокол за границу (стр. 18): точка засчитана, граница НЕ сдвинута,
                    # глубина прокола запомнена — за неё ставится стоп.
                    hi_punct = price if hi_punct is None else max(hi_punct, price)
            else:
                if len(lo_px) < 2:
                    lo_px.append(price)
                    lo_idx.append(index)
                    last_kind = kind
                    if len(lo_px) == 2:
                        lo_edge = max(lo_px)
                        if (ladder_lo is not None
                                and min(lo_px) <= ladder_lo <= max(lo_px)):
                            # Лесенка (стр. 40): цена вышла ВВЕРХ из прежней структуры,
                            # и её верхнее ребро стало нижним ребром этой.
                            lo_edge = ladder_lo
                            lo_source = BorderSource.LADDER
                        # Прокол среди первых двух точек — см. верхнюю сторону.
                        beyond = [p for p in lo_px[:2] if p < lo_edge]
                        if beyond:
                            lo_punct = (min(beyond) if lo_punct is None
                                        else min(lo_punct, min(beyond)))
                    continue
                assert lo_edge is not None
                if price > lo_edge:
                    if not converging(price, kind):
                        continue
                    lo_px.append(price)
                    lo_idx.append(index)
                    last_kind = kind
                    lo_edge = price
                    lo_narrowed += 1
                    continue
                lo_px.append(price)
                lo_idx.append(index)
                last_kind = kind
                if price < lo_edge:
                    lo_punct = price if lo_punct is None else min(lo_punct, price)

        if len(hi_px) < 2 or len(lo_px) < 2:
            continue

        upper_lo, upper_hi = upper_zone()
        lower_lo, lower_hi = lower_zone()
        # ГРАНИЦА — одна цена: внутренний край первых двух точек стороны, сдвинутый
        # внутрь при сужении. Обоснование — в докстроке BoundaryZone.
        assert up_edge is not None and lo_edge is not None
        upper_edge, lower_edge = up_edge, lo_edge
        if upper_edge <= lower_edge:
            # Границы сошлись — горизонтального диапазона нет (стр. 13).
            reset()
            continue

        bar = bars[k]
        if body_beyond(bar, upper_edge, Direction.ABOVE):
            direction = Direction.ABOVE
        elif body_beyond(bar, lower_edge, Direction.BELOW):
            direction = Direction.BELOW
        else:
            # Возврат внутрь структуры обрывает серию тел: стр. 55 — «возвращается той
            # же или следующей свечей» это прокол БЕЗ подтверждения.
            run_dir = None
            continue

        # Серия тел рвётся не только сменой стороны, но и ДЫРОЙ В РЯДУ: стр. 55 говорит
        # «2-3 полных тел свечей ЭТОГО ТФ», то есть про подряд идущие свечи, а не про
        # подряд идущие элементы списка. При дыре в ряду это разные вещи (Р-2 разбора).
        broken = k > 0 and steps_between(bars[k - 1], bars[k], timeframe) != 1
        if direction is not run_dir or broken:
            run_dir, run_from = direction, k
        bodies = k - run_from + 1
        if bodies < confirm_bodies:
            continue
        if len(hi_px) + len(lo_px) < min_points:
            # Цена ушла раньше, чем структура набрала нужные точки — накопления не было.
            #
            # ⚠ При умолчании `min_points = 4` эта ветка НЕДОСТИЖИМА, и это не дефект, а
            # следствие предпосылки выше: пока у обеих сторон меньше двух точек, цикл до
            # проверки выхода не доходит вовсе, значит на этой строке сумма гарантированно
            # ≥ 4. Требование стр. 22 («4 и более точек») выполняется СТРУКТУРОЙ кода, а
            # не этой проверкой.
            #
            # Ветка тем не менее нужна: стр. 23 называет и другое число — «(4+6+ точки
            # границ)», — и при `min_points = 6` она срабатывает. Проверено случаем
            # «структура из 4 точек при пороге 6» в gates/course_rules.py: без него
            # строка была бы утверждением, которого никто не проверял. Именно в таком
            # виде её и нашёл внешний разбор (Р-1): комментарий описывал поведение,
            # которого не бывает.
            reset()
            continue

        found.append(
            Accumulation(
                timeframe=timeframe,
                first_index=min(hi_idx + lo_idx),
                last_index=k,
                upper=BoundaryZone(edge=upper_edge, lo=upper_lo, hi=upper_hi,
                                   narrowed=up_narrowed, source=up_source,
                                   point_indices=tuple(hi_idx), puncture=hi_punct),
                lower=BoundaryZone(edge=lower_edge, lo=lower_lo, hi=lower_hi,
                                   narrowed=lo_narrowed, source=lo_source,
                                   point_indices=tuple(lo_idx), puncture=lo_punct),
                exit=StructureExit(
                    direction=direction,
                    first_body_index=run_from,
                    confirmed_at_index=k,
                ),
            )
        )
        # Лесенка (стр. 40): ребро, через которое цена вышла, предлагается СЛЕДУЮЩЕЙ
        # структуре как её противоположное ребро. Причинности это не нарушает —
        # структура уже закрыта и лежит слева.
        if direction is Direction.ABOVE:
            ladder_lo = upper_edge
        else:
            ladder_up = lower_edge
        reset()
        resets -= 1  # эмиссия — не распад

    tail: OpenStructure | None = None
    if len(hi_px) >= 2 and len(lo_px) >= 2:
        ulo, uhi = upper_zone()
        llo, lhi = lower_zone()
        first = min(hi_idx + lo_idx)
        tail = OpenStructure(
            first_index=first,
            bars_open=len(bars) - first,
            upper=BoundaryZone(edge=up_edge if up_edge is not None else ulo,
                               lo=ulo, hi=uhi, narrowed=up_narrowed,
                               source=up_source,
                               point_indices=tuple(hi_idx), puncture=hi_punct),
            lower=BoundaryZone(edge=lo_edge if lo_edge is not None else lhi,
                               lo=llo, hi=lhi, narrowed=lo_narrowed,
                               source=lo_source,
                               point_indices=tuple(lo_idx), puncture=lo_punct),
        )

    return AccumulationScan(
        closed=tuple(found), open_tail=tail, bars_scanned=len(bars), resets=resets
    )
