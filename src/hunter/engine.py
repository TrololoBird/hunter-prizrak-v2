"""ЕДИНСТВЕННЫЙ расчёт сигнала: кадры → решение. FOUNDATION.md §8 этапы 2-5.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются. Время сюда не
входит вовсе — решение целиком определяется поданными кадрами.

⚠ Зачем модуль появился 2026-08-06. Расчёта как места НЕ БЫЛО: он был размазан между
`card.render` и `run.record`, и каждый считал своё. Замер разбором AST плюс прогон на
сохранённых кадрах:

  * тройка `priority.resolve` → `priority.agreement` → `geometry.build_setup` считалась
    ДВАЖДЫ — в `card.py:153` и в `emit.py:77`, общего кода нет;
  * фильтр эмиссии стоял только в одном из двух. На кадрах прогона `a1`: карточка
    печатала полную геометрию (стоп, лестница, цели, РР) для **94 уровней**, леджер
    эмитировал **33**. Состояния: активных 33, флипнутых 31, отработанных 30;
  * из 31 флипнутого уровня геометрия печаталась для ИСХОДНОЙ стороны, тогда как стр. 43
    говорит «Уровень лонг/шорт менятся для нас на противоположный». То есть карточка
    показывала стоп и цели сделки, которую курс велит брать в другую сторону;
  * свинги и структуры считались по два-три раза на ТФ: `card.render` звал
    `swings.detect`/`accumulation.detect`, и `levels.build_all` звал их же внутри.

Отсюда устройство: **`decide` считает всё один раз, а карточка и леджер — два потребителя
одного результата.** Карточка ничего не пересчитывает; леджер ничего не пересчитывает.

Геометрия строится ТОЛЬКО там, где есть эмиссия. Уровень, который система не торгует,
получает названную причину (`Decision.hold`), а не стоп с целями: печатать геометрию
сделки, которая не будет взята, — это печатать сигнал, которого нет.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from . import emit, geometry, levels, pereprior, priority, swings
from .accumulation import AccumulationScan
from .accumulation import detect as detect_accumulations
from .bars import TIMEFRAME_MS
from .geometry import Setup
from .levels import Level, LevelStatus, MappedLevel, Unbuilt
from .models import Bar, NotReady, TradeWindows
from .pereprior import Pereprior, PPSide
from .priority import Agreement, Priority
from .swings import SwingSet, Trend

# ⚠ Типы импортируются ИМЕНАМИ, а не через модуль. Поля моделей ниже называются `swings`
# и `priority` — по смыслу, — и внутри тела класса эти имена перекрывают одноимённые
# модули: `trend: swings.Trend` разбирается как обращение к ПОЛЮ. Поймано mypy сразу
# («Name "swings.Trend" is not defined»), но глазами читается как исправное.


class SeriesRead(BaseModel):
    """Разбор ОДНОГО ряда: свинги, структуры, тренд. Считается по разу на ТФ.

    До 2026-08-06 этот разбор жил в трёх местах сразу — в карточке, в `levels.build_all` и
    в `run.record`, — и каждое звало `swings.detect` заново по тем же барам.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    swings: SwingSet
    scan: AccumulationScan
    trend: Trend
    perepriors: tuple[Pereprior, ...] = ()
    """Переприоры этого ряда. §2.5.

    ⚠ Заведено 2026-08-07, находка А-02. До этого `pereprior.detect` не вызывался из
    расчёта ВООБЩЕ — только при печати карточки. Следствие было такое: уровень, которого
    цена коснулась, переводился в `EntryRule.CONFIRMATION` — стр. 25: «можно рассматривать
    вход от 2 или 3 касания только по факту слома структуры на младшем ТФ», — а
    наступление слома система проверить не могла. Оператор получал инструкцию,
    исполнимость которой машина не отслеживала.
    """


class Decision(BaseModel):
    """Что система решила по ОДНОМУ размеченному уровню. Одна запись — один уровень.

    `setup` есть тогда и только тогда, когда уровень эмитируется. Иначе `hold` называет
    причину словами со ссылкой на страницу курса (§4.3: отсутствие называется, а не
    подменяется значением).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    status: LevelStatus
    priority: Priority
    agreement: Agreement
    setup: Setup | None
    hold: str
    """Пусто — уровень эмитируется. Иначе причина, по которой сделки нет."""

    mtf_break: str = ""
    """Слом структуры на МЛАДШЕМ ТФ после касания уровня — условие входа по стр. 25/31.

    Пусто у уровней, которым это правило не адресовано (лимитки ещё разрешены). У
    остальных — либо описание найденного слома, либо названная причина, почему его нет.
    §4.3: отсутствие называется, а не подменяется пустотой.
    """

    @property
    def emitted(self) -> bool:
        return self.setup is not None


class SymbolDecision(BaseModel):
    """Полный расчёт по символу. То, что печатает карточка И что пишет леджер."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframes: tuple[str, ...]
    """Порядок ТФ — от младшего к старшему, задан ЗДЕСЬ.

    ⚠ Раньше порядок задавался вызывающим, и карточка зависела от того, как её позвали:
    живой прогон шёл по порядку вселенной, повтор — по именам файлов. Первый же повтор
    2026-08-04 дал «изменилось» у 2 символов из 2 при неизменном расчёте (§10.6).
    """

    reads: dict[str, SeriesRead]
    unreadable: tuple[Unbuilt, ...]
    """ТФ, не давшие разбора: кадров нет либо ряд короче требуемого. С причиной."""

    unbuilt: tuple[Unbuilt, ...]
    """Структуры, не давшие уровня. С причиной."""

    mapped: tuple[MappedLevel, ...]
    decisions: tuple[Decision, ...]

    @property
    def trends(self) -> dict[str, Trend]:
        return {tf: r.trend for tf, r in self.reads.items()}

    @property
    def emissions(self) -> tuple[emit.Emission, ...]:
        """Эмиссии — ПРОЕКЦИЯ решений, а не второй расчёт.

        В этом всё: пока эмиссии считались отдельной функцией (`emit.select`), они могли
        разойтись с карточкой и расходились. Здесь разойтись нечему — выбирается
        подмножество уже принятых решений.
        """
        return tuple(
            emit.Emission(level=d.level, setup=d.setup, agreement=d.agreement)
            for d in self.decisions if d.setup is not None
        )


def read_series(
    series: dict[str, list[Bar]], timeframes: tuple[str, ...]
) -> tuple[dict[str, SeriesRead], tuple[Unbuilt, ...]]:
    """Свинги, структуры и тренд по каждому ТФ. Один проход, один результат.

    ТФ без кадров и ТФ со слишком коротким рядом возвращаются НАЗВАННЫМИ (§4.3), а не
    молча выпадают: карточка обязана напечатать причину, а не просто не показать строку.
    """
    reads: dict[str, SeriesRead] = {}
    bad: list[Unbuilt] = []
    for tf in timeframes:
        bars = series.get(tf)
        if not bars:
            bad.append(Unbuilt(timeframe=tf, index=None, reason="кадров нет"))
            continue
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            bad.append(Unbuilt(timeframe=tf, index=None, reason=sw.reason))
            continue
        reads[tf] = SeriesRead(timeframe=tf, swings=sw, trend=swings.trend(sw),
                               scan=detect_accumulations(bars, sw, tf),
                               perepriors=pereprior.detect(bars, sw, tf))
    return reads, tuple(bad)


def foreign_borders(
    built: tuple[Level, ...], timeframes: tuple[str, ...]
) -> dict[str, tuple[tuple[float, int], ...]]:
    """Для каждого ТФ — цены уровней, посчитанных на ДРУГИХ ТФ (стр. 39, 46, 54).

    Курс называет чужой границей обе стороны сразу: уровень СТАРШЕГО ТФ держит
    младшую базу (стр. 46, 54), а ПОК стопового объёма МЛАДШЕГО ТФ держит старшую
    (стр. 39). Поэтому фильтр здесь один — «не свой ТФ», без направления.

    ⚠ Именно тут жил бы старый дефект «считали старшие ТФ позже младших». Его тут
    нет по ПОСТРОЕНИЮ, а не по внимательности: все цены берутся из ПРОХОДА 1, где
    чужих границ не было ни у кого, поэтому проход 2 не зависит от порядка ТФ вовсе.
    Переставь `timeframes` как угодно — ответ тот же. Проходов ровно два, до
    неподвижной точки никто не итерирует.

    Вместе с ценой отдаётся `created_at_ms` — момент, с которого уровень СУЩЕСТВУЕТ.
    Без него база могла бы опереться на уровень, родившийся позже неё (I-5), и это
    ровно тот дефект, ради которого поле в `Level` и завели.
    """
    out: dict[str, tuple[tuple[float, int], ...]] = {}
    for tf in timeframes:
        out[tf] = tuple(sorted(
            (float(lv.price), lv.created_at_ms)
            for lv in built if lv.timeframe != tf
        ))
    return out


def decide(
    symbol: str,
    series: dict[str, list[Bar]],
    trades: TradeWindows | None,
    timeframes: tuple[str, ...],
) -> SymbolDecision:
    """Кадры → решение. Единственная точка, где считается сигнал.

    Порядок шагов здесь и есть «конвейер», и он теперь записан в одном месте:

        ряды → свинги → структуры → уровни (ПОК) → судьба уровня → приоритет → геометрия

    ⚠ Второй проход за чужой границей (стр. 39, 46, 54) был внесён и ОТКАЧЕН
    2026-08-08: критерий не прошёл контроль на заведомо неверных анкерах. Причина
    записана в `accumulation.BorderSource`. Проход снова ОДИН.

    Каждый шаг зовётся РОВНО ОДИН раз. Геометрия — только для эмитируемых.
    """
    tfs = tuple(sorted(timeframes, key=lambda t: TIMEFRAME_MS.get(t, 0)))
    reads, unreadable = read_series(series, tfs)
    scans = {tf: r.scan for tf, r in reads.items()}
    frozen, unbuilt = levels.build_all(symbol, series, trades, tfs, scans,
                                       {tf: r.swings for tf, r in reads.items()})
    mapped = levels.map_levels(frozen, series)
    trends = {tf: r.trend for tf, r in reads.items()}

    decisions: list[Decision] = []
    for m in mapped:
        pr = priority.resolve(trends, m.level.timeframe)
        hold = emit.hold_reason(m.status)
        decisions.append(Decision(
            level=m.level,
            status=m.status,
            priority=pr,
            agreement=priority.agreement(m.level.side, pr),
            setup=None if hold else geometry.build_setup(m.level, mapped),
            hold=hold,
            mtf_break=_mtf_break(m, series, reads, tfs),
        ))
    return SymbolDecision(symbol=symbol, timeframes=tfs, reads=reads,
                          unreadable=unreadable, unbuilt=unbuilt,
                          mapped=mapped, decisions=tuple(decisions))



def _mtf_break(
    m: MappedLevel,
    series: dict[str, list[Bar]],
    reads: dict[str, SeriesRead],
    timeframes: tuple[str, ...],
) -> str:
    """Случился ли слом структуры на МЛАДШЕМ ТФ ПОСЛЕ касания уровня (стр. 25, 31).

    ⚠ Правка аудита 2026-08-07, находка А-02. §2.5 вычислялся и в решение не входил.

    Стр. 25: «можно рассматривать вход от 2 или 3 касания только по факту слома структуры
    на младшем ТФ». Стр. 31: «Позицию от уровня смотрим только по факту слома структуры на
    более мелких ТФ». Это условие ВХОДА, и без него `EntryRule.CONFIRMATION` — инструкция,
    исполнимость которой не проверяется.

    Здесь она проверяется и НАЗЫВАЕТСЯ, но эмиссию НЕ порождает: вход по слому — отдельный
    путь сигнала, а его введение меняет состав эмиссии и потому решение владельца, а не
    правка аудита. Сейчас оператор получает ответ на вопрос «слом уже был?» вместо
    молчания.

    Сторона слома должна совпадать со стороной сделки: от лонгового уровня входят вверх,
    значит нужен ПП в лонг.
    """
    if m.status.limit_orders_allowed:
        return ""  # правило адресовано не этому уровню: лимитки ещё разрешены
    tf = m.level.timeframe
    if tf not in timeframes:
        return "младший ТФ не определён"
    idx = timeframes.index(tf)
    if idx == 0:
        return "младше этого ТФ рядов не собрано — слом проверить негде"
    younger = timeframes[idx - 1]
    y_bars = series.get(younger)
    y_read = reads.get(younger)
    own = series.get(tf)
    if not y_bars or y_read is None or not own:
        return f"ряд {younger} не разобран — слом проверить негде"
    touch = levels.first_test_index(m.level, own)
    if touch is None:
        return "касания ещё не было"
    touch_ms = own[touch].open_ms + TIMEFRAME_MS[tf]
    want = PPSide.LONG if m.level.side is levels.LevelSide.LONG else PPSide.SHORT
    y_step = TIMEFRAME_MS[younger]
    for pp in y_read.perepriors:
        if pp.side is not want:
            continue
        at = y_bars[pp.confirmed_at_index].open_ms + y_step
        if at >= touch_ms:
            return (f"слом на {younger} подтверждён, зона ПП "
                    f"{pp.zone_lo:.8g}…{pp.zone_hi:.8g}, "
                    f"{'тест был' if pp.tested_at_index is not None else 'теста ещё не было'}")
    return f"слома на {younger} после касания не было"
