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

from . import absorption, emit, figures, geometry, levels, pereprior, priority, swings
from .absorption import AbsorptionRead
from .accumulation import Accumulation, AccumulationScan, OpenStructure
from .accumulation import detect as detect_accumulations
from .bars import TIMEFRAME_MS
from .geometry import Setup
from .levels import Level, LevelStatus, MappedLevel, Unbuilt
from .models import Bar, NotReady, TradeWindows
from .pereprior import Pereprior, PPSide
from .priority import Agreement, CounterLevel, Priority
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

    all_perepriors: tuple[Pereprior, ...] = ()
    """ВСЕ переприоры ряда, а не последний на сторону. Нужны стр. 51 и фигурам стр. 56-62.

    `perepriors` выше — подмножество этого поля (последний ПП каждой стороны), поэтому
    второго прохода по бару нет: оба берутся из одного `pereprior.detect_all`.
    """

    splits: tuple[pereprior.SplitEntry, ...] = ()
    """Пары "истинный + ранний ПП" рядом — стр. 51: «закуп лучше делить на 2 части»."""

    channels: tuple[figures.Channel, ...] = ()
    """Флаги (стр. 56) и клинья (стр. 60) этого ряда."""

    channel_notes: tuple[str, ...] = ()
    """По одной строке на каждый коридор из `channels`, в том же порядке: ранний ПП,
    на подтверждение которого стр. 56 разрешает вход, либо НАЗВАННАЯ причина, почему
    его нет (§4.3)."""

    pennants: tuple[figures.Pennant, ...] = ()
    """Вымпелы/треугольники ЗАКРЫТЫХ структур (стр. 57, 58)."""

    open_pennant: figures.Pennant | None = None
    """Вымпел НЕЗАКРЫТОЙ структуры. Отдельным полем, потому что вход по стр. 57 берётся
    на 6 касании ВНУТРИ структуры — то есть до того, как она закроется."""

    open_pennant_missing: str = ""
    """Почему у незакрытой структуры вымпела нет. Пусто — либо вымпел есть, либо самой
    незакрытой структуры нет."""

    multiple_bases: tuple[figures.MultipleBase, ...] = ()
    """Двойные и тройные дно/вершина на закрытых накоплениях (стр. 62)."""

    head_shoulders: tuple[figures.HeadShoulders, ...] = ()
    """Голова и плечи — признак на переприорах этого ряда (стр. 61)."""


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

    pressed: str = ""
    """Конфигурации 2/5/7 стр. 28 — новое накопление, прижатое к зоне уровня.

    Заведено 2026-08-10 (реестр долга, строка 1). Пусто — прижатых структур нет; это
    штатный случай, а не отказ, поэтому пустота здесь законна. Найденное называется с
    правилом действия курса; эмиссию не порождает (как и слом на младшем ТФ)."""

    tf_trap: str = ""
    """Ловушка таймфрейма (стр. 40, 46, 47) — готовая строка `priority.counter_warning`.

    Пусто — встречных уровней старшего ТФ в коробке базы нет. Считается ТОЛЬКО у
    эмитируемых уровней, по тому же правилу, что и геометрия: предупреждение о сделке
    имеет смысл там, где сделка есть.
    """

    stop_volume: geometry.StopVolumeSetup | None = None
    """ОТДЕЛЬНАЯ сделка от уровня стопового объёма — стр. 35, 37, 39.

    Не эмитируется (новый тип сигнала меняет состав леджера — тот же выбор, что у
    `PPSetup`), но печатается: курс называет её самостоятельным трейдом.
    """

    stop_volume_missing: str = ""
    """Почему сделки от стопового объёма нет (§4.3). Пусто — она есть либо не искалась."""

    counters: tuple[CounterLevel, ...] = ()
    """Те же встречные уровни, что описывает `tf_trap`, но ЧИСЛАМИ, ближайший первым.

    Строка нужна карточке, числа — сообщению бота: предел Telegram 4096 знаков, и
    пересказывать там весь текст правила курса нечем. Пересобирать строку разбором
    `tf_trap` было бы двумя сущностями под одним именем — тем самым дефектом, из-за
    которого зона вылезала за границы у половины уровней.
    """

    @property
    def emitted(self) -> bool:
        return self.setup is not None


class PPSignal(BaseModel):
    """Сделка от переприора — второй тип сигнала (стр. 50; реестр долга, строка 3).

    ⚠ Заведено 2026-08-10. Считается ЗДЕСЬ, а не в карточке: до этого карточка звала
    `geometry.build_pp_setup` сама — второй расчёт вне `decide`, ровно та форма, из-за
    которой в 2026-08-06 удаляли `emit.select`. В леджер идут только сигналы с целью
    (`setup.target`): исход сделки без цели не измерим, и записывать его значило бы
    завести строку, у которой не может быть исхода.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    pp: Pereprior
    setup: geometry.PPSetup
    structure_note: str = ""
    """Подтверждение структурой (стр. 53): описание коробки либо пусто."""

    absorption: AbsorptionRead | None = None
    """Уторговка за зоной ПП — сырой доп-фактор БЕЗ вердикта (absorption-2026-08-17.md).
    Порог «слом снят» в источниках не назван и здесь не выдуман: до замера порога
    печатается величина со знаменателем."""

    absorption_missing: str = ""
    """Причина, по которой уторговка не измерена (§4.3). Пусто, когда измерена."""

    @property
    def emitted(self) -> bool:
        return self.setup.target is not None


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
    pp_signals: tuple[PPSignal, ...] = ()

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

    @property
    def tf_balance(self) -> emit.TFBalance:
        """Сколько отложек по каждому ТФ и по каждой корзине стр. 48. Со знаменателем.

        Проекция эмиссий, а не второй расчёт: складывается ровно то, что уже решено.
        Отсева по этому счёту нет — стр. 48 требует баланса, но числа не называет.
        """
        return emit.tf_balance(self.emissions)


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
        scan = detect_accumulations(bars, sw, tf)
        every = pereprior.detect_all(bars, sw, tf)
        # `detect` — это «последний ПП каждой стороны» из того же прохода; берём его
        # отсюда, чтобы бары не сканировались дважды и чтобы два поля не разошлись.
        last_side = tuple(seq[-1] for side in (PPSide.SHORT, PPSide.LONG)
                          if (seq := [p for p in every if p.side is side]))
        chans = figures.detect_channels(sw)
        open_pennant, open_missing = _pennant_of(scan.open_tail)
        reads[tf] = SeriesRead(
            timeframe=tf, swings=sw, trend=swings.trend(sw), scan=scan,
            perepriors=last_side,
            all_perepriors=every,
            splits=pereprior.split_entries(every),
            channels=chans,
            channel_notes=tuple(_channel_note(c, every) for c in chans),
            pennants=tuple(pen for acc in scan.closed
                           if (pen := _pennant_of(acc)[0]) is not None),
            open_pennant=open_pennant,
            open_pennant_missing=open_missing,
            multiple_bases=tuple(
                mb for acc in scan.closed
                if not isinstance(mb := figures.multiple_base(acc, every), NotReady)),
            head_shoulders=tuple(
                hs for pp in every
                if not isinstance(hs := figures.head_and_shoulders(pp, sw), NotReady)),
        )
    return reads, tuple(bad)


def _pennant_of(
    structure: Accumulation | OpenStructure | None,
) -> tuple[figures.Pennant | None, str]:
    """Вымпел структуры (стр. 57, 58) вместе с причиной, если его нет.

    Причина возвращается ОТДЕЛЬНОЙ строкой, а не подменяется пустотой: у незакрытой
    структуры «сужения нет» и «структуры нет» — разные ответы (§4.3).
    """
    if structure is None:
        return None, ""
    got = figures.pennant(structure)
    if isinstance(got, NotReady):
        return None, got.reason
    return got, ""


def _channel_note(channel: figures.Channel, pps: tuple[Pereprior, ...]) -> str:
    """Вход во флаг по стр. 56 — «подтверждение раннего ПереПриора» — либо причина."""
    got = figures.channel_early_pp(channel, pps)
    if isinstance(got, NotReady):
        return got.reason
    return (f"ранний ПП в {got.side.value} подтверждён на баре {got.confirmed_at_index}, "
            f"зона {got.zone_lo:.8g}…{got.zone_hi:.8g} (стр. 56)")


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
    frame_bars: int | None = None,
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
    # `frame_bars` — рамка кадра ответа для сборки по запросу (2026-08-18, п. 3 приказа
    # владельца); боевой прогон передаёт None и строит всё — смысл в `levels.build_all`.
    frozen, unbuilt = levels.build_all(symbol, series, trades, tfs, scans,
                                       {tf: r.swings for tf, r in reads.items()},
                                       frame_bars=frame_bars)
    # `scans` третьим аргументом — иначе `LevelStatus.playout` знает только картины 1,
    # 3, 4 стр. 28 и закреп без ретеста: картины 2, 5, 6 и 7 требуют разбора структур
    # того же ТФ, и без него страница читалась бы наполовину.
    mapped = levels.map_levels(frozen, series, scans)
    trends = {tf: r.trend for tf, r in reads.items()}

    decisions: list[Decision] = []
    for m in mapped:
        pr = priority.resolve(trends, m.level.timeframe)
        hold = emit.hold_reason(m.status)
        # Предупреждение о ловушке ТФ и сделка от стопового объёма считаются ТАМ ЖЕ, где
        # геометрия, — у эмитируемых уровней. Оба обходят весь пул, и считать их для
        # уровня, который система не торгует, значит платить квадратом за строку, которую
        # никто не прочтёт.
        sv: geometry.StopVolumeSetup | NotReady | None = (
            None if hold else geometry.build_stop_volume_setup(m.level, mapped))
        decisions.append(Decision(
            level=m.level,
            status=m.status,
            priority=pr,
            agreement=priority.agreement(m.level.side, pr),
            setup=None if hold else geometry.build_setup(m.level, mapped),
            hold=hold,
            mtf_break=_mtf_break(m, series, reads, tfs),
            pressed=_pressed_note(m, reads),
            tf_trap="" if hold else priority.counter_warning(m.level, mapped),
            counters=() if hold else priority.counter_levels(m.level, mapped),
            stop_volume=sv if isinstance(sv, geometry.StopVolumeSetup) else None,
            stop_volume_missing=sv.reason if isinstance(sv, NotReady) else "",
        ))
    pp_signals: list[PPSignal] = []
    for tf in tfs:
        r = reads.get(tf)
        if r is None:
            continue
        for pp in r.perepriors:
            opposite = next((o for o in r.perepriors if o.side is not pp.side), None)
            absorbed = absorption.measure(pp, series[tf], tf, trades)
            pp_signals.append(PPSignal(
                timeframe=tf, pp=pp,
                setup=geometry.build_pp_setup(pp, opposite),
                structure_note=_pp_structure_note(pp, r.scan),
                absorption=absorbed if not isinstance(absorbed, NotReady) else None,
                absorption_missing=absorbed.reason if isinstance(absorbed, NotReady) else "",
            ))

    return SymbolDecision(symbol=symbol, timeframes=tfs, reads=reads,
                          unreadable=unreadable, unbuilt=unbuilt,
                          pp_signals=tuple(pp_signals),
                          mapped=mapped, decisions=tuple(decisions))



def _pp_structure_note(pp: Pereprior, scan: AccumulationScan) -> str:
    """Подтверждение ПП структурой — стр. 53 (реестр, строка 5): закрытое накопление
    того же ТФ, сформированное ПОСЛЕ слома и опирающееся на зону ПП (лонг — над зоной,
    шорт — под). Пусто — не найдено."""
    for acc in scan.closed:
        if acc.first_index <= pp.confirmed_at_index:
            continue
        fits = (acc.lower.edge >= pp.zone_lo if pp.side is PPSide.LONG
                else acc.upper.edge <= pp.zone_hi)
        if fits:
            where = "над" if pp.side is PPSide.LONG else "под"
            return (f"подтверждён структурой {where} ПП (стр. 53): "
                    f"коробка {acc.lower.edge:.8g}…{acc.upper.edge:.8g}")
    return ""


_PRESSED_RULE = {
    levels.PressedKind.BELOW: "конфигурация 2, под уровнем — вход курс рисует от ПОК "
                              "нового накопления (стр. 28)",
    levels.PressedKind.ABOVE: "конфигурация 5, над уровнем (стр. 28)",
    levels.PressedKind.ASTRIDE: "конфигурация 7, \"пила\" на уровне — выйти в бу и "
                                "дождаться выхода (стр. 28)",
}


def _pressed_note(m: MappedLevel, reads: dict[str, SeriesRead]) -> str:
    """Конфигурации 2/5/7 стр. 28 у этого уровня — сгруппированы по виду для карточки."""
    r = reads.get(m.level.timeframe)
    if r is None:
        return ""
    found = levels.pressed_structures(m.level, r.scan)
    parts: list[str] = []
    for kind in (levels.PressedKind.BELOW, levels.PressedKind.ABOVE,
                 levels.PressedKind.ASTRIDE):
        boxes = [p for p in found if p.kind is kind]
        if boxes:
            coords = ", ".join(f"{p.box_lo:.8g}…{p.box_hi:.8g}" for p in boxes)
            parts.append(f"{_PRESSED_RULE[kind]}: {coords}")
    return "; ".join(parts)


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
    # ⚠ СВЕЖИЙ УРОВЕНЬ ТОЖЕ СЧИТАЕТСЯ (2026-08-19). Здесь стоял ранний выход «правило
    # адресовано не этому уровню: лимитки ещё разрешены» — и он терял ТРЕТИЙ случай
    # курса. Их три, а не два:
    #   1. лимитка БЕЗ подтверждения — свежий уровень (стр. 30);
    #   2. лимитка ЛИБО вход по слому, на выбор, — тот же свежий уровень: стр. 19
    #      «также можно смотреть слом структуры на мтф, и брать БОЛЕЕ БЕЗОПАСНУЮ
    #      позицию с хорошим соотношением РР». Не запрет лимитки, а альтернатива ей;
    #   3. ТОЛЬКО по слому — уровень уже забран с реакцией (стр. 25, 31) либо пробит
    #      и сменил сторону (стр. 43).
    # Прежний выход отдавал случай 2 в никуда: у свежего уровня слом не искался вовсе,
    # и сказать «здесь можно взять безопаснее» было нечем.
    tf = m.level.timeframe
    if tf not in timeframes:
        return "младший ТФ не определён"
    idx = timeframes.index(tf)
    if idx == 0:
        # ⚠ НЕ «рядов не собрано», а «младшего ТФ НЕТ» (2026-08-19). Прежняя строка
        # звучала как нехватка данных, которую можно добрать, — и я сам сначала так её
        # и прочитал, собравшись подать минутный ряд. Курс отвечает иначе: стр. 17
        # называет основные ТФ «5м/15м/час/4ч/1Д/1Н» и дополнительные
        # «2ч/6ч/12ч/10мин/30 мин», то есть НИЧЕГО младше пяти минут (10мин и 30мин
        # старше). Для 5м-уровня младшего ТФ не существует, значит правило стр. 25 и 31
        # к нему НЕПРИМЕНИМО, а не «не проверено»: со второго касания такой уровень
        # курсом не торгуется вовсе.
        return ("5м — младший ТФ курса (стр. 17), слом смотреть негде: "
                "со второго касания уровень не торгуется")
    younger = timeframes[idx - 1]
    y_bars = series.get(younger)
    y_read = reads.get(younger)
    own = series.get(tf)
    if not y_bars or y_read is None or not own:
        return f"ряд {younger} не разобран — слом проверить негде"
    touch = levels.first_test_index(m.level, own)
    if touch is None:
        # Цена к уровню не приходила: слом младшего ТФ у ЭТОГО уровня искать не от чего.
        return ""
    touch_ms = own[touch].open_ms + TIMEFRAME_MS[tf]
    want = PPSide.LONG if m.level.side is levels.LevelSide.LONG else PPSide.SHORT
    y_step = TIMEFRAME_MS[younger]
    # ⚠ ВСЕ ПП ОКНА, А НЕ ПОСЛЕДНИЙ НА СТОРОНУ (2026-08-19). Здесь стояло
    # `y_read.perepriors` — выдача `pereprior.detect`, то есть ОДИН последний ПП каждой
    # стороны за весь ряд. Вопрос «был ли слом после касания» сводился к «оказался ли
    # последний ПП ряда позже касания», а он почти всегда у конца ряда: замер по кадрам
    # дал 2953 ответа «подтверждён» из 2953 адресованных — прибор не мог сказать «нет».
    # `all_perepriors` (`detect_all`) отдаёт все, и вопрос становится настоящим.
    #
    # Второе условие — БЛИЗОСТЬ К УРОВНЮ. Слом структуры на младшем ТФ подтверждает вход
    # ИМЕННО ОТ ЭТОГО уровня (стр. 25, 31); ПП, случившийся в другой части графика,
    # к нему отношения не имеет. Критерий геометрический, без выдуманного порога: зона
    # ПП пересекается с зоной уровня.
    zl, zh = float(m.level.zone_lo), float(m.level.zone_hi)
    for pp in y_read.all_perepriors:
        if pp.side is not want:
            continue
        at = y_bars[pp.confirmed_at_index].open_ms + y_step
        if at < touch_ms:
            continue
        if pp.zone_hi < zl or pp.zone_lo > zh:
            continue
        tested = pp.tested_at_index is not None
        why = ("вход безопаснее лимитки, стоп короче (стр. 19)"
               if m.status.limit_orders_allowed else "вход подтверждён (стр. 25, 31)")
        return (f"слом на {younger} подтверждён, зона ПП "
                f"{pp.zone_lo:.8g}…{pp.zone_hi:.8g}, "
                f"{'ТЕСТ БЫЛ — ' + why if tested else 'теста ещё не было'}")
    return f"слома на {younger} у этого уровня после касания не было"
