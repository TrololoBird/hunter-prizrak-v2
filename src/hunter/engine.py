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
    `swings.detect`/`range.detect`, и `levels.build_all` звал их же внутри.

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
from .bars import TIMEFRAME_MS
from .geometry import TF_ORDER, Setup
from .levels import Level, LevelStatus, MappedLevel, Unbuilt
from .models import Bar, NotReady, TradeWindows
from .pereprior import Pereprior, PPSide
from .priority import Agreement, CounterLevel, Priority
from .swings import SwingSet, Trend, TrendDirection
from .trading_range import OpenStructure, RangeScan, TradingRange
from .trading_range import detect as detect_ranges

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
    scan: RangeScan
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
    """Пары "истинный + ранний ПП" рядом — стр. 51: «закуп лучше делить на 2 части».

    ⚠ СМЫСЛ СУЖЕН 2026-08-23: здесь ДЕЙСТВУЮЩИЕ пары (`pereprior.current_splits`), не
    больше одной на сторону, а не все пары за историю ряда. Разбор и цена — в докстроке
    `current_splits`; коротко: карточка печатала это поле целиком и выдавала 394 строки
    на один символ, меряя «текущее» иначе, чем соседнее поле `perepriors`.
    """

    channels: tuple[figures.Channel, ...] = ()
    """Флаги (стр. 56) и клинья (стр. 60) этого ряда."""

    channel_notes: tuple[str, ...] = ()
    """По одной строке на каждый коридор из `channels`, в том же порядке: ранний ПП,
    на подтверждение которого стр. 56 разрешает вход, либо НАЗВАННАЯ причина, почему
    его нет (§4.3)."""

    pennants: tuple[figures.Pennant, ...] = ()
    """Вымпелы/треугольники ЗАКРЫТЫХ структур (стр. 57, 58)."""

    pennants_no_trend: int = 0
    """Сужающихся закрытых структур, не ставших вымпелом: тренда нет НИ СВОЕГО, НИ СТАРШЕГО.

    Считается отдельно от "структура не сужается" (это просто не вымпел) и печатается в
    карточке: отказ по стр. 57 «Торгуем по тренду» молча исчезать не должен (§4.3). До
    привлечения старшего ТФ (стр. 47) он задевал 584 сужения из 1362 (42.9%), после —
    24. Замер 2026-08-22, 10 символов × 6 ТФ, загрузка БОЕВЫМ ОКНОМ (см. предупреждение
    в докстроке `figures.pennant`: по складу целиком те же числа получаются другими).
    """

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


class Detections:
    """Свинги и структуры, посчитанные ОДИН раз на цикл и отданные обоим потребителям.

    ⚠⚠ ЗАВЕДЕНО 2026-08-21 ПО ЗАМЕРУ ЖИВОЙ СЛУЖБЫ. Цикл расчёта считал одно и то же
    ДВАЖДЫ, и это видно по стадиям приёмки: `backfill 673563 мс, decide 1323297 мс`.
    Внутри добора `profile_windows` вызывает `swings.detect` и `trading_range.detect`
    по каждому ТФ, чтобы узнать окна структур; следом `decide` → `read_series` считает
    ТО ЖЕ САМОЕ по тем же барам. Профиль BTC: 8.5 с на символ в доборе плюс ~10 с в
    расчёте, то есть около 460 с на цикл вселенной — вчетверо больше, чем весь шаг
    записи в леджер.

    ⚠ ОТПЕЧАТОК РЯДА ПРОВЕРЯЕТСЯ, А НЕ ПОДРАЗУМЕВАЕТСЯ. Ключ несёт символ, ТФ, длину
    ряда и его края. Не совпало — считаем заново, а не отдаём чужое. Это то же условие,
    что у `TradingRange.box`, и оно здесь важнее: между добором и расчётом лежит
    закачка, и ряд между ними в принципе может измениться. Тогда память промахнётся и
    пересчитает — то есть худшее, что может случиться, это потерянная экономия, а не
    подменённый ответ.

    ⚠ ЖИВЁТ ОДИН ЦИКЛ. Объект создаётся в `service.cycle` и умирает вместе с ним;
    глобального кэша здесь нет сознательно — он копил бы ряды всех прошедших циклов и
    стал бы утечкой того же класса, из-за которой заведён `keep_bars`.
    """

    __slots__ = ("_memo", "hits", "misses")

    def __init__(self) -> None:
        self._memo: dict[
            tuple[str, str, int, int, int],
            tuple[SwingSet | NotReady, RangeScan | None],
        ] = {}
        self.hits = 0
        self.misses = 0

    def get(self, symbol: str, timeframe: str,
            bars: list[Bar]) -> tuple[SwingSet | NotReady, RangeScan | None]:
        """Свинги и структуры ряда. `NotReady` кэшируется наравне с ответом.

        Отказ — такой же результат прохода, как и успех, и пересчитывать его на втором
        потребителе значит платить за то же самое второй раз. `RangeScan` при отказе
        свингов равен `None`: структур без свингов не бывает.
        """
        key = (symbol, timeframe, len(bars),
               bars[0].open_ms if bars else 0, bars[-1].open_ms if bars else 0)
        got = self._memo.get(key)
        if got is None:
            self.misses += 1
            sw = swings.detect(bars)
            scan = None if isinstance(sw, NotReady) else detect_ranges(bars, sw, timeframe)
            got = self._memo[key] = (sw, scan)
        else:
            self.hits += 1
        return got


def read_series(
    series: dict[str, list[Bar]], timeframes: tuple[str, ...],
    *, symbol: str = "", detections: Detections | None = None,
) -> tuple[dict[str, SeriesRead], tuple[Unbuilt, ...]]:
    """Свинги, структуры и тренд по каждому ТФ. Один проход, один результат.

    ТФ без кадров и ТФ со слишком коротким рядом возвращаются НАЗВАННЫМИ (§4.3), а не
    молча выпадают: карточка обязана напечатать причину, а не просто не показать строку.

    `detections` — разбор, уже сделанный ДОБОРОМ в этом же цикле (см. `Detections`).
    Умолчание `None` оставлено намеренно: без него каждый вызов из зондов и старого кода
    требовал бы сначала собрать переносчик, а поведение при этом ТО ЖЕ — просто разбор
    считается здесь. Отличается только цена.
    """
    reads: dict[str, SeriesRead] = {}
    bad: list[Unbuilt] = []
    for tf in timeframes:
        bars = series.get(tf)
        if not bars:
            bad.append(Unbuilt(timeframe=tf, index=None, reason="кадров нет"))
            continue
        got: SwingSet | NotReady
        maybe_scan: RangeScan | None
        if detections is None:
            got = swings.detect(bars)
            maybe_scan = None if isinstance(got, NotReady) else detect_ranges(bars, got, tf)
        else:
            got, maybe_scan = detections.get(symbol, tf, bars)
        if isinstance(got, NotReady):
            bad.append(Unbuilt(timeframe=tf, index=None, reason=got.reason))
            continue
        if maybe_scan is None:
            # Недостижимо по построению `Detections.get`, но проверяется, а не
            # утверждается: `assert` вырезается ключом `-O`, а отказ обязан быть НАЗВАН
            # (§4.3), а не превратиться в `AttributeError` этажом ниже.
            bad.append(Unbuilt(timeframe=tf, index=None,
                               reason="свинги есть, а разбора структур нет — "
                                      "переносчик разбора отдал неполную пару"))
            continue
        sw, scan = got, maybe_scan
        every = pereprior.detect_all(bars, sw, tf)
        # `detect` — это «последний ПП каждой стороны» из того же прохода; берём его
        # отсюда, чтобы бары не сканировались дважды и чтобы два поля не разошлись.
        last_side = tuple(seq[-1] for side in (PPSide.SHORT, PPSide.LONG)
                          if (seq := [p for p in every if p.side is side]))
        chans = figures.detect_channels(sw)
        reads[tf] = SeriesRead(
            timeframe=tf, swings=sw, trend=swings.trend(sw), scan=scan,
            perepriors=last_side,
            all_perepriors=every,
            splits=pereprior.current_splits(every),
            channels=chans,
            channel_notes=tuple(_channel_note(c, every) for c in chans),
            multiple_bases=tuple(
                mb for acc in scan.closed
                if not isinstance(mb := figures.multiple_base(acc, every), NotReady)),
            head_shoulders=tuple(
                hs for pp in every
                if not isinstance(hs := figures.head_and_shoulders(pp, sw), NotReady)),
        )
    # Вымпелы — ВТОРЫМ проходом: их сторона может прийти со старшего ТФ (стр. 47), а в
    # первом проходе старшие ряды ещё не разобраны. См. `_fill_pennants`.
    _fill_pennants(reads, series)
    return reads, tuple(bad)


def _pennant_of(
    structure: TradingRange | OpenStructure | None,
    swings: SwingSet,
    senior: priority.Priority | None = None,
) -> tuple[figures.Pennant | None, str]:
    """Вымпел структуры (стр. 57, 58) вместе с причиной, если его нет.

    Причина возвращается ОТДЕЛЬНОЙ строкой, а не подменяется пустотой: у незакрытой
    структуры «сужения нет» и «структуры нет» — разные ответы (§4.3).

    Свинги нужны для СТОРОНЫ: стр. 57 «Торгуем по тренду», и тренд берётся из них по
    экстремумам до начала структуры — см. `figures.pennant`. `senior` — приоритет
    старшего ТФ на тот же момент, когда свой ряд тренда не даёт (стр. 47).
    """
    if structure is None:
        return None, ""
    got = figures.pennant(structure, swings, senior)
    if isinstance(got, NotReady):
        return None, got.reason
    return got, ""


def _trend_as_of(read: SeriesRead, bars: list[Bar], upto_ms: int) -> Trend:
    """Тренд ряда НА МОМЕНТ `upto_ms` — по экстремумам, подтверждённым не позже него.

    Существует ровно ради причинности: `SeriesRead.trend` — это тренд СЕЙЧАС, на полном
    наборе свингов, и для решения, принимаемого сейчас, он верен. Но вымпелы печатаются и
    ПРОШЛЫЕ, а «тренд сейчас» для структуры, закрывшейся месяц назад, есть заглядывание
    вперёд — тот самый класс, который досье нашло у `smartmoneyconcepts` тремя
    недокументированными утечками.

    Рез идёт по `confirmed_at_index`, а не по `index`: фрактал становится известен на два
    бара позже своей вершины, и берётся именно момент ИЗВЕСТНОСТИ.
    """
    sw = read.swings
    # ⚠ СЧИТАЕТСЯ ДЛИНА, А НЕ СТРОИТСЯ НАБОР — та же правка, что в `figures.pennant`.
    # Условие есть `confirmed_at_index <= C` при постоянном C: бары упорядочены по
    # `open_ms`, поэтому «подтверждён не позже `upto_ms`» — это порог по индексу, а
    # не произвольная выборка. Значит отбор — префикс, и от него нужна длина.
    cut_len = sum(1 for s in sw.swings
                  if s.confirmed_at_index < len(bars)
                  and bars[s.confirmed_at_index].open_ms <= upto_ms)
    if cut_len < 2:
        return Trend(direction=TrendDirection.NONE, holds_for=0)
    return swings.trend_upto(sw, cut_len)


def _senior_priority(
    tf: str,
    structure: TradingRange | OpenStructure,
    bars: list[Bar],
    reads: dict[str, SeriesRead],
    series: dict[str, list[Bar]],
    higher: list[str],
) -> priority.Priority | None:
    """Приоритет старшего ТФ (стр. 47) НА МОМЕНТ начала структуры.

    `None` — старших рядов нет вовсе; тогда вымпел остаётся при своём ТФ и отказывает,
    если тот молчит. Это не то же, что «приоритета нет»: `priority.resolve` вернул бы
    `NO_PRIORITY`, и отличать нечем было бы (§4.3).
    """
    if not higher or not (0 <= structure.first_index < len(bars)):
        return None
    at = bars[structure.first_index].open_ms
    # ⚠ Тренды считаются СВЕРХУ ВНИЗ и до первого определившегося, а не все разом. Ответ
    # от этого НЕ меняется: `resolve` сама идёт `reversed(higher)` и берёт первый
    # определившийся, значит ряды ниже найденного она бы и не спросила. Меняется цена —
    # `_trend_as_of` линеен по числу экстремумов ряда, а младший из старших (15м для 5м)
    # их имеет тысячи. Замер 2026-08-22 на 10 символах × 6 ТФ: 2 м 20 с → 59 с, и все
    # выходные числа совпали до единицы — это и есть доказательство, что ответ тот же.
    trends: dict[str, Trend] = {}
    for s in reversed(higher):
        trends[s] = _trend_as_of(reads[s], series[s], at)
        if trends[s].direction is not TrendDirection.NONE:
            break
    return priority.resolve(trends, tf)


def _fill_pennants(
    reads: dict[str, SeriesRead], series: dict[str, list[Bar]]
) -> None:
    """Вымпелы ВТОРЫМ проходом: сторона может прийти со СТАРШЕГО ТФ (стр. 47).

    Первым проходом это невозможно по построению — ТФ разбираются по возрастанию, и на
    5м старшие ряды ещё не прочитаны. Отдельный проход дешевле, чем разбор в два круга:
    старший ТФ спрашивается ТОЛЬКО у сужающихся структур, чей собственный ряд тренда не
    дал, а таких по замеру 2026-08-22 около 43% сужений и порядка процента всех структур.
    """
    for tf, read in list(reads.items()):
        if tf not in TF_ORDER:
            continue
        bars = series.get(tf) or []
        higher = [s for s in TF_ORDER[TF_ORDER.index(tf) + 1:]
                  if s in reads and series.get(s)]
        closed = [
            _pennant_of(acc, read.swings,
                        _senior_priority(tf, acc, bars, reads, series, higher))
            for acc in read.scan.closed
        ]
        tail = read.scan.open_tail
        open_pen, open_missing = _pennant_of(
            tail, read.swings,
            None if tail is None
            else _senior_priority(tf, tail, bars, reads, series, higher))
        reads[tf] = read.model_copy(update={
            "pennants": tuple(pen for pen, _ in closed if pen is not None),
            "pennants_no_trend": sum(1 for pen, reason in closed
                                     if pen is None and reason == figures.NO_TREND_REASON),
            "open_pennant": open_pen,
            "open_pennant_missing": open_missing,
        })


def _channel_note(channel: figures.Channel, pps: tuple[Pereprior, ...]) -> str:
    """Вход во флаг по стр. 56 — «подтверждение раннего ПереПриора» — либо причина."""
    got = figures.channel_early_pp(channel, pps)
    if isinstance(got, NotReady):
        return got.reason
    return (f"ранний ПП в {got.side.value} подтверждён на баре {got.confirmed_at_index}, "
            f"зона {got.zone_lo:.8g}…{got.zone_hi:.8g} (стр. 56)")

def decide(
    symbol: str,
    series: dict[str, list[Bar]],
    trades: TradeWindows | None,
    timeframes: tuple[str, ...],
    detections: Detections | None = None,
    horizon_days: int = 0,
) -> SymbolDecision:
    """Кадры → решение. Единственная точка, где считается сигнал.

    Порядок шагов здесь и есть «конвейер», и он теперь записан в одном месте:

        ряды → свинги → структуры → уровни (ПОК) → судьба уровня → приоритет → геометрия

    ⚠ Второй проход за чужой границей (стр. 39, 46, 54) был внесён и ОТКАЧЕН
    2026-08-08: критерий не прошёл контроль на заведомо неверных анкерах. Причина
    записана в `range.BorderSource`. Проход снова ОДИН.

    Каждый шаг зовётся РОВНО ОДИН раз. Геометрия — только для эмитируемых.
    """
    tfs = tuple(sorted(timeframes, key=lambda t: TIMEFRAME_MS.get(t, 0)))
    reads, unreadable = read_series(series, tfs, symbol=symbol, detections=detections)
    scans = {tf: r.scan for tf, r in reads.items()}
    # ⚠ `frame_bars` УДАЛЁН 2026-08-23: рамка кадра ответа не резала состав карты с
    # 2026-08-21, и параметр только передавался дальше, где его выбрасывали `del`-ом.
    frozen, unbuilt = levels.build_all(symbol, series, trades, tfs, scans,
                                       {tf: r.swings for tf, r in reads.items()},
                                       horizon_days=horizon_days)
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
            pressed=_pressed_note(m, reads, series),
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
                structure_note=_pp_structure_note(pp, r.scan, series[tf]),
                absorption=absorbed if not isinstance(absorbed, NotReady) else None,
                absorption_missing=absorbed.reason if isinstance(absorbed, NotReady) else "",
            ))

    return SymbolDecision(symbol=symbol, timeframes=tfs, reads=reads,
                          unreadable=unreadable, unbuilt=unbuilt,
                          pp_signals=tuple(pp_signals),
                          mapped=mapped, decisions=tuple(decisions))



def _pp_structure_note(pp: Pereprior, scan: RangeScan, bars: list[Bar]) -> str:
    """Подтверждение ПП структурой — стр. 53 (реестр, строка 5): закрытое накопление
    того же ТФ, сформированное ПОСЛЕ слома и опирающееся на зону ПП (лонг — над зоной,
    шорт — под). Пусто — не найдено.

    ⚠⚠ КОРОБКА ЗДЕСЬ ТЕПЕРЬ НАСТОЯЩАЯ (2026-08-23). Строка печатала слово «коробка» и
    подставляла в него ЛИНИИ ДЕТЕКТОРА (`acc.lower.edge`, `acc.upper.edge`), тогда как с
    2026-08-18 «коробка» в проекте — это ХАЙ…ЛОЙ свечей структуры, и владельцу под одним
    человеческим именем показывались две разные величины. Свечи выходят за линии
    детектора у 85.4% структур (замер 2026-08-20, BTC+ETH+SOL, 417 структур), медиана
    превышения 0.703%. Тем же вопросом решался и САМ ВЕРДИКТ «опирается на зону ПП»:
    сравнение шло с линией, а опора — это край свечей.
    """
    for acc in scan.closed:
        if acc.first_index <= pp.confirmed_at_index:
            continue
        box = acc.box(bars)
        if box is None:
            continue
        box_lo, box_hi = box
        fits = (box_lo >= pp.zone_lo if pp.side is PPSide.LONG
                else box_hi <= pp.zone_hi)
        if fits:
            where = "над" if pp.side is PPSide.LONG else "под"
            return (f"подтверждён структурой {where} ПП (стр. 53): "
                    f"коробка {box_lo:.8g}…{box_hi:.8g}")
    return ""


_PRESSED_RULE = {
    levels.PressedKind.BELOW: "конфигурация 2, под уровнем — вход курс рисует от ПОК "
                              "нового накопления (стр. 28)",
    levels.PressedKind.ABOVE: "конфигурация 5, над уровнем (стр. 28)",
    levels.PressedKind.ASTRIDE: "конфигурация 7, \"пила\" на уровне — выйти в бу и "
                                "дождаться выхода (стр. 28)",
}


def _pressed_note(m: MappedLevel, reads: dict[str, SeriesRead],
                  series: dict[str, list[Bar]]) -> str:
    """Конфигурации 2/5/7 стр. 28 у этого уровня — сгруппированы по виду для карточки."""
    r = reads.get(m.level.timeframe)
    if r is None:
        return ""
    bars = series.get(m.level.timeframe)
    if not bars:
        return ""
    found = levels.pressed_structures(m.level, r.scan, bars)
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
