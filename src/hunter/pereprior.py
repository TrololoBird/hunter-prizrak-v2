"""Переприор — слом структуры §2.5. Источник — мини-курс, стр. 49, 50, 51, 55.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Разбор источника с цитатами: docs/audit/course-reading-2026-08-03.md

  стр. 49  «слом тенденции / структуры тренда, смена приоритета, иными словами
           это разворот цены в противоположное направление»
  стр. 50  ИСТИННЫЙ. «ПП в Шорт – цена ломает последний лой, из которого был последний
           хай, и подтверждает его тестом снизу»; ПП в Лонг там же и зеркально.
  стр. 51  РАННИЙ. «Ранний ПП в Шорт — цена формирует хай, затем лой — затем НЕ обновляет
           хай и пробивает последний лой.»
  стр. 55  «Уровнем ПП является ВСЯ ЗОНА ТЕНИ свечи, образовавшей ХАЙ/ЛОЙ, а не только
           шпиль»; подтверждение — то же, что у любого уровня: 2-3 полных тела.
  стр. 55  «Если в лонг тренде цена не обновила хай — ЭТО НЕ ЯВЛЯЕТСЯ сломом тренда…
           Это может лишь являться доп фактором» → одного необновления мало, нужен слом.

Своего механизма подтверждения здесь НЕТ: стр. 55 говорит, что он один для всех уровней,
и он живёт в `breach.py`. Прокол сломанного уровня переприором не является — стр. 55:
«это просто прокол БЕЗ подтверждения, не берём позицию в таком случае».
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .breach import CONFIRM_BODIES, BreachKind, Direction, first_breach
from .models import Bar
from .swings import Swing, SwingKind, SwingSet


class PPKind(StrEnum):
    TRUE = "true"
    """Истинный (стр. 50): сломан лой, ИЗ КОТОРОГО был обновлённый хай."""

    EARLY = "early"
    """Ранний (стр. 51): хай НЕ обновлён, сломан предыдущий лой."""


class PPSide(StrEnum):
    SHORT = "short"
    LONG = "long"


class Pereprior(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PPKind
    side: PPSide

    broken_index: int
    """Бар сломанного экстремума."""

    broken_price: float

    zone_lo: float
    zone_hi: float
    """Уровень ПП — ВСЯ зона тени свечи экстремума (стр. 55), а не одна цена.

    У свечи без тени с нужной стороны зона вырождается в точку (`zone_lo == zone_hi`).
    Это не ошибка и не подставленное значение: тени действительно не было.
    """

    confirmed_at_index: int
    """Бар, на котором слом подтверждён телами (стр. 55)."""

    tested_at_index: int | None
    """Бар возврата в зону ПП — точка входа по стр. 50/51. None — теста ещё не было."""

    @property
    def zone_degenerate(self) -> bool:
        return self.zone_lo == self.zone_hi


def _shadow_zone(bar: Bar, kind: SwingKind) -> tuple[float, float]:
    """Зона тени свечи, образовавшей экстремум (стр. 55)."""
    if kind is SwingKind.LOW:
        return bar.low, min(bar.open, bar.close)
    return max(bar.open, bar.close), bar.high


def _test_index(bars: list[Bar], lo: float, hi: float, from_index: int) -> int | None:
    """Возврат в зону ПП — по ЗАКРЫТИЮ внутри зоны, а не по касанию тенью.

    ⚠ Так стало 2026-08-07, решение владельца по обзору чужих реализаций
    (docs/audit/bos-projects-2026-08-07.md, гипотеза Д2). Прежняя редакция считала тестом
    ПЕРЕСЕЧЕНИЕ диапазона бара с зоной, то есть тень, задевшая зону, уже была входом.
    Замер: у 452 переприоров из 710 (63.7%) это РАЗНЫЕ бары либо теста по закрытию нет
    вовсе. Стр. 50 требует «подтверждает его тестом снизу», но чем подтверждается сам
    тест, курс не говорит — это было умолчание, и оно стоило дорого: этот бар и есть
    точка входа.

    Шесть чужих проектов из десяти требуют закрытия за уровнем, а не касания.
    """
    for i in range(from_index, len(bars)):
        if lo <= bars[i].close <= hi:
            return i
    return None


def _detect_side(
    bars: list[Bar], swings: SwingSet, side: PPSide, timeframe: str, confirm_bodies: int
) -> Pereprior | None:
    """Один ПП нужной стороны — самый поздний из подтверждённых."""
    broken_kind = SwingKind.LOW if side is PPSide.SHORT else SwingKind.HIGH
    other_kind = SwingKind.HIGH if side is PPSide.SHORT else SwingKind.LOW
    direction = Direction.BELOW if side is PPSide.SHORT else Direction.ABOVE

    # ⚠ ПОРЯДОК ВНУТРИ ОДНОГО БАРА — ФИКЦИЯ, и здесь это названо (2026-08-17). Сортировка
    # устойчива, поэтому у двух свингов с ОДНИМ `index` (внешний бар: и хай, и лой) порядок
    # задаётся порядком двух `if` в `swings.detect` — сначала HIGH, потом LOW. Из OHLC
    # «что было раньше» не следует, значит этот порядок детерминирован, но произволен.
    #
    # На отбор ниже он не влияет: ломаемый кандидат берётся по СТРОГОМУ `<`, то есть
    # свинги того же бара отсеиваются раньше, чем их взаимный порядок начал бы значить
    # (обоснование — в комментарии у `candidates`). Названо, чтобы следующая правка,
    # ослабляющая неравенство, знала, что наследует произвольный порядок.
    ordered = sorted(swings.swings, key=lambda s: s.index)
    others = [s for s in ordered if s.kind is other_kind]
    if len(others) < 2:
        return None

    # ⚠ ДВА УКАЗАТЕЛЯ ВМЕСТО ФИЛЬТРА НА КАЖДОМ ШАГЕ — правка 2026-08-17 по живому
    # стеку (py-spy: decide жил здесь минутами на 5м-ряде в 51 тыс. баров). Прежний
    # код на КАЖДОМ `last_other` собирал `[s for s in ordered if s.kind is broken_kind
    # and s.index < last_other.index]` — O(свингов²), сотни миллионов итераций на
    # символ. `ordered` отсортирован по index, поэтому «последний broken строго до
    # last_other» ищется одним проходом: указатель `bi` только движется вперёд.
    # Семантика тождественна (тот же candidates[-1]); дифф повтора пуст.
    brokens = [s for s in ordered if s.kind is broken_kind]

    found: Pereprior | None = None
    bi = 0
    for i in range(1, len(others)):
        prev_other, last_other = others[i - 1], others[i]
        while bi < len(brokens) and brokens[bi].index < last_other.index:
            bi += 1
        last_broken = brokens[bi - 1] if bi > 0 else None

        # Стр. 50 против стр. 51: обновился ли хай (для шорта) / лой (для лонга).
        # ⚠ Считается по состоянию НА ТОТ МОМЕНТ, а не по последним двум экстремумам
        # ряда. Замер 2026-08-04 показал, к чему ведёт второе: после слома цена падает и
        # рисует новые, более НИЗКИЕ хаи, последний хай перестаёт быть обновлённым, и
        # каждый истинный ПП механически переклассифицируется в ранний. Истинных было
        # 0 из 32 конфигураций — не свойство рынка, а свойство порядка вычислений.
        updated = (last_other.price > prev_other.price if side is PPSide.SHORT
                   else last_other.price < prev_other.price)

        # Ломаемый экстремум — последний ПЕРЕД этим хаем: стр. 50 «лой, из которого был
        # последний хай», то есть тот, откуда начался рост. Условия про лой между двумя
        # хаями курс не ставит, и оно отбрасывало 8 конфигураций из 32: фракталы не
        # чередуются строго, между соседними хаями лоя может не быть вовсе.
        #
        # ⚠ НЕРАВЕНСТВО СТРОГОЕ, И ЭТО ВЫБОР, А НЕ НЕДОСМОТР (назван 2026-08-17 по
        # требованию владельца закрыть вопрос источником либо докстрокой).
        #
        # Внешний бар даёт хай и лой ОДНОВРЕМЕННО — ~3% свингов, на 1Н 5.52%
        # (перемер со свидетелем: docs/audit/evidence/probe-samebar-2026-08-17.txt). Такой лой стоит
        # на том же индексе, что хай, и под `<` в кандидаты не попадает: берётся более
        # ранний. Замер последствия (4 символа × 4 ТФ): затронуто 55 хаёв из 1025 (5.37%),
        # во всех 55 берётся более ранний лой, расхождение зон тени медиана 0.83% цены,
        # максимум 27.39%, больше процента в 24 случаях из 55.
        #
        # ПОЧЕМУ ВСЁ РАВНО СТРОГОЕ. Стр. 50 требует лой, ИЗ КОТОРОГО вырос хай, то есть
        # событие, случившееся РАНЬШЕ. У внешнего бара порядок хая и лоя внутри свечи из
        # OHLC НЕИЗВЕСТЕН: четыре числа не говорят, что было первым. Включить такой лой
        # значило бы предположить, что он был раньше хая, — предположение, которого в
        # данных нет.
        #
        # Это ТОТ ЖЕ принцип, что уже применён в `outcome.resolve` и записан в докстроке
        # `outcome.py`: «по бару OHLC неизвестно, что случилось раньше — стоп или цель…
        # Выбрать „обычно сначала стоп“ значило бы придумать правило, которого нет ни в
        # данных, ни в курсе». Там неоднозначность помечается, здесь — не берётся
        # недоказуемый кандидат; в обоих случаях выдумка отвергается.
        #
        # ⚠ И ЭТОТ ЖЕ ДОВОД НАЙДЕН У КЛАССИКА, дословно (ёлочки не ставятся: они
        # зарезервированы за курсом, книжные цитаты — в английских кавычках).
        # Jeremy du Plessis, "The Definitive Guide to Point and Figure", Harriman House,
        # page 2-46 — о том, почему в P&F один день не может дать и X, и O:
        #     "it is insurmountable if you are using end-of-day data, for the simple reason
        #      that you do not know whether the high or low occurred first."
        # Он разрешает случай правилом приоритета (page 2-47: "the low takes precedence…
        # you plot that O and ignore the high"), причём приоритет сильнее разворота. Мы
        # разрешаем иначе — не берём недоказуемого кандидата, — но по ТОМУ ЖЕ основанию:
        # порядок внутри дня из данных не следует.
        #
        # ⚠ Различие с `swings.detect` намеренное и не противоречие. Там один бар даёт ОБА
        # фрактала, и это прямая цитата Билла Уильямса (page 68: "The same bar can be part
        # of both an up and a down fractal"), потому что фрактал не спрашивает о порядке
        # внутри бара. Здесь порядок спрашивается по существу — стр. 50 требует лой, ИЗ
        # КОТОРОГО вырос хай, — и потому ответ другой.
        #
        # ⚠ ЦЕНА ВЫБОРА НАЗВАНА, а не спрятана: у 5.37% хаёв зона ПП строится по соседней
        # свече, и в 24 случаях из 55 это больше процента цены. Лечится только данными
        # мельче бара (тиками либо минутками внутри бара), которых у этого расчёта нет.
        if last_broken is None:
            continue
        broken: Swing = last_broken

        # Конфигурация «последний хай + его лой» действует, пока не появился следующий
        # хай: после него «последним» становится уже он, и слом относился бы к другой
        # паре. Поэтому окно поиска слома ограничено сверху.
        end = others[i + 1].confirmed_at_index if i + 1 < len(others) else len(bars)
        start = max(broken.confirmed_at_index + 1, last_other.confirmed_at_index)
        if start >= end:
            continue

        lo, hi = _shadow_zone(bars[broken.index], broken_kind)
        # БЛИЖНИЙ край зоны: для шорта верх зоны тени (граница тела), для лонга — низ.
        #
        # ⚠ Так стало 2026-08-07, решение владельца по обзору
        # (docs/audit/bos-projects-2026-08-07.md, гипотеза Д1). Прежняя редакция брала
        # ДАЛЬНИЙ край, то есть сам экстремум — самое строгое чтение. Стр. 55 говорит, что
        # уровнем ПП является ВСЯ ЗОНА ТЕНИ, но с какого её края считать пробой, не
        # говорит: это было умолчание. Замер: дальний край даёт 710 подтверждённых
        # переприоров, ближний — 1021, разница 43.8%. Нуль (край, взятый случайно внутри
        # зоны) дал 844 и изменил число у 18 рядов из 18 — зависимость от положения края
        # монотонна, разница реальна, а не артефакт.
        edge = hi if side is PPSide.SHORT else lo
        ev = first_breach(bars, edge, direction, timeframe, from_index=start,
                          to_index=end, confirm_bodies=confirm_bodies)
        if ev is None or ev.kind is not BreachKind.BREAKOUT or ev.resolved_index is None:
            # Прокол подтверждением не является (стр. 55) — переприора нет.
            continue

        found = Pereprior(
            kind=PPKind.TRUE if updated else PPKind.EARLY, side=side,
            broken_index=broken.index, broken_price=broken.price,
            zone_lo=lo, zone_hi=hi,
            confirmed_at_index=ev.resolved_index,
            tested_at_index=_test_index(bars, lo, hi, ev.resolved_index + 1),
        )
    return found


def detect(
    bars: list[Bar], swings: SwingSet, timeframe: str, *,
    confirm_bodies: int = CONFIRM_BODIES,
) -> tuple[Pereprior, ...]:
    """Последний подтверждённый переприор каждой стороны.

    Не «все за историю»: ПП — это смена приоритета (стр. 49), и актуальна последняя.
    Обе стороны возвращаются, если обе нашлись, — выбирать между ними по одному лишь
    порядку было бы правилом, которого курс не даёт.
    """
    out = [pp for side in (PPSide.SHORT, PPSide.LONG)
           if (pp := _detect_side(bars, swings, side, timeframe, confirm_bodies)) is not None]
    return tuple(out)


class PPFactor(BaseModel):
    """Необновлённый экстремум. Стр. 55: это НЕ слом, а доп-фактор.

    Отдельный тип, чтобы его нельзя было спутать с `Pereprior`: курс прямо
    предупреждает, что «цена не обновила хай» ошибочно принимают за слом тренда.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    side: PPSide
    last_price: float
    previous_price: float
    at_index: int
    watch_zone_lo: float
    watch_zone_hi: float
    """Зона, на которую стр. 55 советует «выставить оповещение»."""


def failed_update(bars: list[Bar], swings: SwingSet) -> tuple[PPFactor, ...]:
    """Экстремум не обновлён — повод следить, но не сигнал (стр. 55)."""
    out: list[PPFactor] = []
    for side in (PPSide.SHORT, PPSide.LONG):
        kind = SwingKind.HIGH if side is PPSide.SHORT else SwingKind.LOW
        seq = sorted(swings.of(kind), key=lambda s: s.index)
        if len(seq) < 2:
            continue
        last, prev = seq[-1], seq[-2]
        failed = last.price < prev.price if side is PPSide.SHORT else last.price > prev.price
        if not failed:
            continue
        opposite = SwingKind.LOW if side is PPSide.SHORT else SwingKind.HIGH
        marks = [s for s in sorted(swings.of(opposite), key=lambda s: s.index)
                 if s.index < last.index]
        if not marks:
            continue
        lo, hi = _shadow_zone(bars[marks[-1].index], opposite)
        out.append(PPFactor(side=side, last_price=last.price, previous_price=prev.price,
                            at_index=last.index, watch_zone_lo=lo, watch_zone_hi=hi))
    return tuple(out)


