"""Карточка символа — детерминированный текст всего расчёта §2. FOUNDATION.md §10.3.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются. Время сюда не
входит вовсе — карточка целиком определяется поданными кадрами.

Это ЕДИНИЦА ПОВТОРА. §10.3 требует: «сохранить сырые кадры, породившие карточку, и позже
воспроизвести из них ту же карточку побитово». §10.6 условие 2: «любое изменение расчёта
предъявляется как дифф повтора — на этих сохранённых данных карточка была такой, стала
такой. Вы видите два текста и разницу между ними».

Поэтому текст обязан быть:
  * детерминированным — никаких множеств, словарей в произвольном порядке, отметок времени;
  * полным — то, чего в карточке нет, повтором не проверяется;
  * читаемым владельцем, который не программист (§7.6).

Отсутствие данных печатается словами и с причиной (§4.3), а не пропускается.
"""

from __future__ import annotations

from decimal import Decimal

import polars as pl

from . import (
    admission,
    breach,
    engine,
    factors,
    geometry,
    indicators,
    pereprior,
    swings,
)
from .accumulation import BorderSource
from .bars import TIMEFRAME_MS, continuous_tail
from .models import Bar, NotReady

TF_LABEL = {"5m": "5м", "15m": "15м", "1h": "1ч", "4h": "4ч", "1d": "1Д", "1w": "1Н"}
SIDE_LABEL = {"long": "ЛОНГ", "short": "ШОРТ"}
TREND_LABEL = {"up": "восходящий", "down": "нисходящий", "none": "не определён"}
STATE_LABEL = {"active": "активен", "worked_off": "отработан", "flipped": "флипнут"}

DIV_LABEL = {
    "divergence": "дивергенция",
    "convergence": "конвергенция",
    # ⚠ Скрытые добавлены 2026-08-07, находка М-16: на стр. 65 схема подписана тремя
    # семействами, а текст описывает одно. Расширенных нет — им нужен допуск на
    # равенство экстремумов, которого курс не даёт (§0).
    "hidden_bearish": "скрытая медвежья дивергенция",
    "hidden_bullish": "скрытая бычья дивергенция",
    # Расширенные добавлены 2026-08-09: допуск равенства вершин получил референт из
    # классики (Булковски <3%, см. factors.EXTENDED_EQUAL_TOL_PCT).
    "extended_bearish": "расширенная медвежья дивергенция",
    "extended_bullish": "расширенная бычья дивергенция",
}

STOP_BASIS_LABEL = {
    "anchor": "за стоповым объёмом либо проколом (стр. 18)",
    "margin": "запас за структуру (стр. 33)",
}
ANCHOR_LABEL = {
    "puncture": "прокол за границу",
    "stop_volume": "стоповый объём",
    "swing": "лой/хай своего ТФ или ТФ−1",
}
"""Три вида якоря стопа, названные одной фразой стр. 18. Печатаются с 2026-08-09: до этого
оператор видел цену стопа и не мог сказать, откуда она взялась (§4.3)."""
ENTRY_LABEL = {
    "limit": "лимитки на ПОК и зону (стр. 30)",
    "confirmation": "только по слому структуры на младшем ТФ (стр. 31)",
    "retest_flipped": "по ретесту с обратной стороны, в другую сторону (стр. 43)",
}
"""Что с уровнем МОЖНО делать. Печатается вместо голого «лимитки нет».

«Лимитки нет» — это запрет без разрешения: оператор читает его как «уровень мёртв», а
курс в обоих случаях оставляет вход, просто другой (стр. 31 и 43). Формулировка выбрана
так, чтобы страница была видна прямо в карточке: владелец не читает код и обязан иметь
возможность проверить правило по источнику.
"""
AGREE_LABEL = {"by_trend": "по тренду",
               # Стр. 47 разрешает контр-тренд только при УЖЕ открытой позиции по тренду
               # (реестр долга, строка 6): у системы позиции нет (§1), поэтому
               # предусловие исполняется единственно возможным способом — словами,
               # которые видит оператор.
               #
               # ⚠ 2026-08-10, правка П5 обзора приоритета ТФ. До неё подпись называла
               # только стр. 47 и говорила про ОТКРЫТУЮ позицию. Стр. 11 добавляет к
               # хеджу два условия, которых здесь не было: позиция обязана быть уже
               # ПРИБЫЛЬНОЙ, а под убыточную хедж не открывается никогда. Оператор,
               # действующий строго по прежней подписи, мог открыть хедж под убыточную
               # сделку и нарушить курс, не отступив от напечатанного.
               #
               # Замер цены до внесения: подпись печатается у 41.0% структур (2313 из
               # 5644) — самая частая из трёх, чаще «по тренду» (37.8%).
               "against_trend": "ПРОТИВ тренда — курс допускает только хеджем при "
                                "открытой позиции по тренду (стр. 47), и только под "
                                "ПРИБЫЛЬНУЮ: стр. 11 — под убыточную сделку хедж не "
                                "открывается никогда",
               "no_priority": "приоритет не определён"}
PP_LABEL = {"true": "истинный", "early": "ранний"}


def _pct(base: Decimal, other: Decimal) -> str:
    """Расстояние в процентах от цены входа — то, чем курс задаёт запас (стр. 18, 33)."""
    if not base:
        return "—"
    return f"{abs(other - base) / base * 100:.2f}%"


def _num(x: float | Decimal, digits: int = 8) -> str:
    """Число без экспоненты и без хвостовых нулей — иначе дифф шумит на форматировании."""
    s = f"{float(x):.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def render(d: engine.SymbolDecision, series: dict[str, list[Bar]]) -> str:
    """Карточка символа: ПЕЧАТЬ готового решения. Ничего не считает.

    ⚠ Так стало 2026-08-06. Раньше карточка сама звала `swings.detect`,
    `accumulation.detect`, `levels.build_all`, `map_levels`, `priority.resolve`,
    `priority.agreement` и `geometry.build_setup` — то есть считала сигнал заново, вторым
    экземпляром рядом с тем, что уходил в леджер. Замер на кадрах прогона `a1`: карточка
    печатала геометрию для 94 уровней, леджер эмитировал 33, и из 31 флипнутого уровня
    геометрия строилась для ИСХОДНОЙ стороны вопреки стр. 43.

    Теперь конвейер один (`engine.decide`), а карточка — один из двух его потребителей.
    Геометрия печатается ТОЛЬКО там, где есть эмиссия; где её нет — печатается причина.

    Чистота сохранена: `SymbolDecision` — чистая функция от кадров, значит и карточка
    остаётся такой. Повтор (`replay`) строит решение из кадров заново, а не получает
    готовым, иначе проверка §10.6 перестала бы быть независимой.
    """
    timeframes = d.timeframes
    out: list[str] = [f"СИМВОЛ {d.symbol}"]

    for u in d.unreadable:
        out.append(f"  {TF_LABEL.get(u.timeframe, u.timeframe)}: {u.reason}")

    out.append("")
    out.append("ТРЕНД И СТРУКТУРА")
    for tf in timeframes:
        r = d.reads.get(tf)
        if r is None:
            continue
        sc, tr = r.scan, r.trend
        tail = ("нет" if sc.open_tail is None
                else f"открыта {sc.open_tail.bars_open} баров, точек {sc.open_tail.points}")
        # Форма найденных баз (§4.3). Курс различает базу с чёткими границами и базу
        # в сужении (стр. 34), а граница бывает не своя (стр. 40, 39, 46). В карточке
        # они выглядели одинаково, и оператор не мог их отличить.
        narrowing = sum(1 for a in sc.closed
                        if a.upper.narrowed or a.lower.narrowed)
        ladder = sum(1 for a in sc.closed if BorderSource.LADDER
                     in (a.upper.source, a.lower.source))
        out.append(f"  {TF_LABEL.get(tf, tf):>3}  баров {sc.bars_scanned}  "
                   f"структур {len(sc.closed)}  распадов {sc.resets}  "
                   f"в сужении {narrowing}  лесенка {ladder}  "
                   f"тренд {TREND_LABEL[tr.direction.value]} (держится на {tr.holds_for})  "
                   f"незакрытая структура: {tail}")

    out.append("")
    out.append("УРОВНИ (ПОК накоплений)")
    if not d.mapped:
        out.append("  уровней нет")
    for dec in d.decisions:
        lvl, st, pr, ag = dec.level, dec.status, dec.priority, dec.agreement
        out.append(
            f"  {SIDE_LABEL[lvl.side.value]} {TF_LABEL.get(lvl.timeframe, lvl.timeframe):>3}  "
            f"ПОК {_num(lvl.price)}  зона {_num(lvl.zone_lo)}…{_num(lvl.zone_hi)}  "
            f"объём {_num(lvl.structure_volume, 2)}  {STATE_LABEL[st.state.value]}"
            # Цена по ту сторону всей структуры — печатается РЯДОМ СО СТАТУСОМ, потому
            # что слово «активен» без этой пометки читается как ожидание цены, а цена
            # его уже прошла (стр. 43). Заведено 2026-08-18: владелец увидел на карте
            # наслоение встречных зон — у 25–35% активных уровней цена была не с той
            # стороны, и в тексте это ничем не отличалось от нормального уровня.
            + ("  цена ЗА структурой (стр. 43)" if st.price_beyond else "")
        )
        # Форма базы печатается ТОЛЬКО когда она не обычная: строка «горизонтальная,
        # свои границы» ничего не сообщала бы, а шум мешал бы увидеть остальное.
        form = ""
        if lvl.boundary_narrowed:
            form += f"  база в сужении (стр. 34), шагов {lvl.boundary_narrowed}"
        if lvl.boundary_ladder:
            form += "  граница от прежней структуры (стр. 40)"
        out.append(
            f"        границы {_num(lvl.boundary_lo)}…{_num(lvl.boundary_hi)}  "
            f"вход: {ENTRY_LABEL[st.entry_rule.value]}  "
            f"{AGREE_LABEL[ag.value]}"
            # ⚠ Глубина тренда печатается рядом со стороной (Р-6): `agreement` смотрит
            # только направление, поэтому тренд из двух экстремумов весит как из шести.
            # Число не влияет на решение — оно даёт оператору увидеть, на чём оно стоит.
            + (f" ({TF_LABEL.get(pr.timeframe, pr.timeframe)}, "
               f"держится на {pr.holds_for} экстремумах)" if pr.timeframe else "")
            + form
        )
        # ⚠ СИЛА ПО ОБЪЁМУ ПЕЧАТАЕТСЯ У КАЖДОГО УРОВНЯ (2026-08-19). До этой правки строка
        # композита стояла ВНУТРИ ветки «СДЕЛКИ НЕТ»: владелец видел силу по объёму ровно
        # у тех уровней, по которым система ничего не делает, и не видел ни у одного
        # торгуемого. Стр. 22 говорит: «Сила уровня определяется ТФ и объемом» — то есть
        # величина относится к уровню, а не к отсутствию сделки. С этой же правки доля
        # участвует в ОТБОРЕ уровней карты (`tgbot.live_unique`), а предъявляться обязано
        # то, на чём стоит решение.
        share, dens = lvl.vrvp_share, lvl.vrvp_density
        if share is not None:
            # ⚠ Формат по величине, а не один и тот же: `.1f` печатал «0.0%» у ненулевой
            # зоны, когда доля меньше 0.05%. После правки окна композита (2026-08-19)
            # таких строк осталось 4 из 9517, и все четыре — ложный ноль ПЕЧАТИ при зоне
            # 14834…2.1e7, а не пустой срез.
            shown = f"{share:.1f}" if share >= 0.1 else f"{share:.2g}"
            # ⚠ ПЛОТНОСТЬ ПЕРВОЙ, доля — следом и как справка. Сила по стр. 22 — это
            # плотность: доля объёма растёт вместе с шириной зоны (корреляция +0.716 на
            # 9715 уровнях), и уровень с половиной композита оказывался просто самой
            # широкой полосой. Именно плотность решает отбор в боте, значит её и читает
            # владелец первой. Доля остаётся, потому что отвечает на другой законный
            # вопрос — «сколько объёма вообще лежит в зоне».
            # ⚠ имя НЕ `d`: `d` — это параметр функции (решение символа). Затенение
            # его строкой роняло всю карточку на первом же уровне с посчитанной силой
            # (`AttributeError: 'str' object has no attribute 'reads'`), и поймал это
            # не гейт, а дифф повтора §10.6 — 5 символов из 5 не пересобрались.
            dens_txt = f"×{dens:.1f} к среднему" if dens is not None else "не посчитана"
            out.append(f"        сила по композиту (замена VRVP, стр. 22): плотность "
                       f"{dens_txt}; зона {lvl.vrvp_zone_qty:.6g} — {shown}% композита; "
                       f"{lvl.vrvp_note}")
        elif lvl.vrvp_note:
            out.append(f"        {lvl.vrvp_note}")
        s = dec.setup
        if s is None:
            # ⚠ Геометрии НЕТ, и это не пропуск. Стоп, лестница и цели существуют только
            # у сделки, которая будет взята; печатать их для уровня, который система не
            # торгует, значит печатать сигнал, которого нет. Причина названа (§4.3).
            out.append(f"        СДЕЛКИ НЕТ: {dec.hold}")
            if dec.pressed:
                out.append(f"        накопление у уровня: {dec.pressed}")
            if dec.mtf_break:
                # §2.5 подключён к решению 2026-08-07 (А-02): условие входа по стр. 25/31
                # теперь ПРОВЕРЯЕТСЯ, а не только называется.
                out.append(f"        слом на младшем ТФ: {dec.mtf_break}")
            if st.event is not None:
                out.append(f"        событие: {_event(st.event)}")
            continue
        # ОДИН стоп и его основание. Прежде печатались три цены — безопасный ближний,
        # безопасный дальний и рисковый, — и выбор оставался оператору. Выставить можно
        # один; меню вместо сигнала делало РР неопределённым (стр. 18, 33).
        # ⚠ Чем оказался якорь, печатается с 2026-08-09. До этого оператор видел цену
        # стопа и не мог сказать, откуда она: прокол, стоповый объём или свинг — все три
        # названы одной фразой стр. 18, а различить их было нельзя. §4.3.
        anchor_note = ""
        if lvl.stop_anchor_source is not None:
            anchor_note = f"  якорь: {ANCHOR_LABEL[lvl.stop_anchor_source.value]}"
            if lvl.stop_anchor_narrowed:
                anchor_note += " в сужении (стр. 34)"
        out.append(
            f"        стоп {_num(s.stop)} — {STOP_BASIS_LABEL[s.stop_basis.value]}  "
            f"({_pct(s.entry, s.stop)} от входа)  "
            f"{'закуп дробим' if s.split_orders else 'один ордер'}{anchor_note}"
        )
        if len(s.ladder) > 1:
            # Стр. 32: «лучше закуп делать на все уровни, что бы ваша средняя твх была
            # максимально безопасная». Средняя названа при равных долях — долей курс не
            # задаёт, и подпись об этом говорит прямо.
            out.append(
                f"        лестница {' · '.join(_num(x) for x in s.ladder)}  "
                f"средняя при равных долях {_num(s.average_entry_equal_shares)}"
            )
        if s.targets:
            for t in s.targets:
                role = "цель" if t.role is geometry.TargetRole.PRIMARY else "промежуточная"
                out.append(f"        {role} {_num(t.price)} "
                           f"({TF_LABEL.get(t.timeframe, t.timeframe)}, "
                           f"{t.distance_pct:.2f}%)")
        else:
            out.append("        целей нет")
        # РР по ВЫСТАВЛЯЕМОМУ стопу — единственное число, которое можно проверить.
        # Прежде печаталось два РР под два непоставленных стопа.
        rr = s.rr()
        golden = "" if rr is None or rr >= geometry.GOLDEN_RR else "  НИЖЕ стандарта"
        out.append(f"        РР {_rr(rr)} до первой цели "
                   f"(стандарт курса 1к{_num(geometry.GOLDEN_RR, 0)}){golden}")
        if st.event is not None:
            out.append(f"        событие: {_event(st.event)}")

    if d.reads:
        out.append("")
        out.append("СВОДКА ПО ТФ: структур → уровней (§4.3)")
        # ⚠ СВОДКА ПЕЧАТАЕТСЯ ВСЕГДА, а не только когда есть непостроенные. Правка аудита
        # 2026-08-06 (находка М-02), ИСПРАВЛЕННАЯ 2026-08-07 по замечанию QA.
        #
        # Первая редакция стояла внутри `if d.unbuilt:` — и исчезала целиком ровно тогда,
        # когда непостроенных нет. Это ровно тот дефект, против которого сводка и
        # заводилась: строка «всё построено» и ОТСУТСТВИЕ строки неотличимы, а второе
        # означает ещё и «а сколько всего структур было — неизвестно». Плюс ТФ с НУЛЁМ
        # структур выпадал из сводки молча, то есть «на 1Н структур не нашли вовсе» и
        # «на 1Н структур не искали» выглядели одинаково.
        #
        # Отказы печатались только поштучно, и каждый был честен: «окно выходит за
        # собранное», «сделок не собрано». Но отказ, повторённый сто раз на ОДНОМ
        # таймфрейме, — уже не данные, а перекос: замер R-01 на 305 структурах дал
        # 91.7% построенных на 15м против 7.8% на 1Д и 0.0% на 1Н, то есть карта
        # состояла ровно из тех ТФ, которые курс считает слабее (стр. 48: «Чем старше
        # ТФ — тем выше винрейт»). Поштучные строки этого не показывают: их надо
        # сложить, а глазами их складывает не всякий и не всегда.
        #
        # Правило владельца от 2026-08-04 (CLAUDE.md): «у отказов обязана быть СВОДКА
        # по измерению, вдоль которого возможен перекос». Разбор:
        # docs/audit/backfill-window-2026-08-04.md, docs/audit/04-05-measurements.md
        built_per_tf: dict[str, int] = {}
        for m in d.mapped:
            built_per_tf[m.level.timeframe] = built_per_tf.get(m.level.timeframe, 0) + 1
        by_state: dict[str, int] = {}
        for mm in d.mapped:
            k = mm.status.state.value
            by_state[k] = by_state.get(k, 0) + 1
        emitted = sum(1 for x in d.decisions if x.emitted)
        # ⚠ ВОРОНКА заведена 2026-08-07 по находке М-27: замер конформанса показал, что
        # система строит 65 и 69 уровней на символ против 2–4, названных приёмкой §8.
        # Само по себе число построенных уровней ничего не говорит оператору, пока рядом
        # не сказано, сколько из них ТОРГУЕТСЯ. Это не правка расчёта, а его предъявление.
        out.append(f"  всего уровней {len(d.mapped)}  →  торгуется лимитками {emitted}"
                   + (f"  (по состояниям: {by_state})" if by_state else ""))
        for tf in timeframes:
            r = d.reads.get(tf)
            if r is None:
                # ⚠ Ряд не разобран вовсе — это НЕ «ноль структур». Причина названа выше,
                # в разделе «РЯД НЕ РАЗОБРАН»; здесь строка нужна, чтобы ТФ не пропал.
                out.append(f"    {TF_LABEL.get(tf, tf):>3}  ряд не разобран")
                continue
            total = len(r.scan.closed)
            got = built_per_tf.get(tf, 0)
            if not total:
                # НОЛЬ СТРУКТУР — это результат, а не отсутствие результата. Молчать о нём
                # значит выдавать «не нашли» за «не искали».
                out.append(f"    {TF_LABEL.get(tf, tf):>3}  структур не найдено "
                           f"(баров {r.swings.bars_scanned}, свингов {len(r.swings.swings)})")
                continue
            out.append(f"    {TF_LABEL.get(tf, tf):>3}  {got} из {total}"
                       f"  ({got / total * 100:.0f}%)")

    if d.unbuilt:
        out.append("")
        out.append("УРОВЕНЬ НЕ ПОСТРОЕН — причины поштучно (§4.3)")
        for u in d.unbuilt:
            out.append(
                f"  {TF_LABEL.get(u.timeframe, u.timeframe)}"
                + (f" структура {u.index}" if u.index is not None else "")
                + f": {u.reason}"
            )

    out.append("")
    out.append("ПЕРЕПРИОР")
    any_pp = False
    for tf in timeframes:
        r = d.reads.get(tf)
        if r is None:
            continue
        bars, sw = series[tf], r.swings
        # ⚠ ПП, подтверждение структурой и сделка от ПП считаются в `engine.decide`
        # (PPSignal) — здесь только печать. До 2026-08-10 карточка звала
        # `pereprior.detect` и `build_pp_setup` сама: второй расчёт вне decide, та же
        # форма, из-за которой в 2026-08-06 удаляли `emit.select`.
        for sig in (s for s in d.pp_signals if s.timeframe == tf):
            any_pp = True
            pp, ps = sig.pp, sig.setup
            width = (pp.zone_hi - pp.zone_lo) / pp.zone_lo * 100 if pp.zone_lo else 0.0
            test = ("теста не было" if pp.tested_at_index is None
                    else f"тест через {pp.tested_at_index - pp.confirmed_at_index} баров")
            out.append(f"  {TF_LABEL.get(tf, tf):>3}  {PP_LABEL[pp.kind.value]} в "
                       f"{SIDE_LABEL[pp.side.value]}  слом {_num(pp.broken_price)}  "
                       f"зона тени {_num(pp.zone_lo)}…{_num(pp.zone_hi)} ({width:.3f}%)  "
                       f"{test}")
            if sig.structure_note:
                out.append(f"        {sig.structure_note}")
            if ps.target is not None and ps.rr is not None:
                tgt = f"цель {_num(ps.target)}  РР {ps.rr:.2f} — эмитируется в леджер"
            elif next((o for o in r.perepriors if o.side is not pp.side), None) is None:
                tgt = "цели нет — противоположного ПП нет (маршрут ПП→ПП, стр. 49)"
            else:
                tgt = "цели нет — противоположный ПП позади входа, не по направлению"
            out.append(f"        сделка от ПП (стр. 50): вход {_num(ps.entry)}  "
                       f"стоп {_num(ps.stop)}  {tgt}")
            # Уторговка за зоной — сырой доп-фактор (absorption-2026-08-17.md):
            # порог «слом снят» в источниках не назван, вердикт не печатается.
            beyond_word = "выше" if pp.side.value == "short" else "ниже"
            if sig.absorption is not None:
                ab = sig.absorption
                out.append(f"        уторговка {beyond_word} зоны: объём {ab.qty_beyond:g} "
                           f"из {ab.qty_window:g} за {ab.bars_after_confirm} баров после "
                           f"подтверждения ({ab.share * 100:.1f}%) — порога курс не даёт, "
                           f"вердикта нет")
            elif sig.absorption_missing:
                out.append(f"        уторговка {beyond_word} зоны: не измерена — "
                           f"{sig.absorption_missing}")
        for fac in pereprior.failed_update(bars, sw):
            any_pp = True
            what = "хай" if fac.side.value == "short" else "лой"
            out.append(f"  {TF_LABEL.get(tf, tf):>3}  доп-фактор: не обновлён {what} "
                       f"{_num(fac.last_price)} против {_num(fac.previous_price)} — "
                       f"это НЕ слом (стр. 55)")
    if not any_pp:
        out.append("  ни переприоров, ни доп-факторов")

    out.append("")
    out.append("ДОП-ФАКТОРЫ (сопровождают, не порождают сигнал — стр. 64)")
    if not d.reads:
        out.append("  не считались: кадров нет")
    for tf in timeframes:
        r = d.reads.get(tf)
        if r is None:
            continue
        full, sw = series[tf], r.swings
        # ⚠ Рекурсивные величины считаются ТОЛЬКО на непрерывном хвосте. Ряд с дырой
        # ATR/RSI/EMA считают как смежный, и сглаживание тащит искажение на десятки
        # баров вперёд — а от ATR зависит стоп. До 2026-08-04 `find_gaps` звалась лишь
        # для отчёта, и дыра доходила до расчёта беспрепятственно.
        bars = continuous_tail(full, tf)
        parts: list[str] = []
        if len(bars) < len(full):
            parts.append(f"⚠ разрыв в ряду: считаем по хвосту {len(bars)} из {len(full)}")
        # Свинги пересчитываются на хвосте: их индексы принадлежат СВОЕМУ ряду, и
        # подставить сюда свинги полного ряда значило бы адресовать чужие бары.
        sw_tail = sw if len(bars) == len(full) else swings.detect(bars)
        if isinstance(sw_tail, NotReady):
            out.append(f"  {TF_LABEL.get(tf, tf):>3}  хвост без разрывов слишком короток: "
                       f"{sw_tail.reason}")
            continue
        sw = sw_tail
        # RSI за уровнем 70/30 — доп-фактор; числа из классики (Уайлдер), НЕ из курса:
        # на скриншоте стр. 64 пунктир не подписан (indicators-course-2026-08-17.md).
        # Тот же явный допуск, что у дивергенций ниже.
        if admission.check("rsi14", len(bars), d.symbol, tf) is None:
            rz = factors.rsi_zone(_series(bars, indicators.rsi())[-1])
            if rz is not None:
                parts.append(f"RSI {rz.value:.1f} — "
                             + ("выше 70, перекупленность" if rz.overbought
                                else "ниже 30, перепроданность") + " (стр. 64)")
        for name, quantity, expr in (("RSI", "rsi14", indicators.rsi()),
                                     ("MACD", "macd", indicators.macd_line())):
            # ⚠ Допуск спрашивается ЯВНО с 2026-08-09: EMA в конвенции TV определена с
            # бара 0, и «значение есть в ряду» перестало означать «значение каноничное».
            # Раньше эту работу молча делала затравка TA-Lib (null до N-го бара).
            if admission.check(quantity, len(bars), d.symbol, tf) is not None:
                continue
            for div in factors.divergences(bars, sw, _series(bars, expr), name):
                # М-16: видов четыре, не два — подпись берётся из словаря, а не выводится
                # ветвлением «если не дивергенция, значит конвергенция».
                parts.append(f"{DIV_LABEL[div.kind.value]} {name}")
        # ⚠ Отсутствие фактора называется ЧИСЛАМИ, а не словами «истории мало». Требования
        # к истории замерены (`admission.REQUIRED_BARS`), и `admission.check` умеет
        # сказать «нужно N, есть M» — до 2026-08-06 эта функция не звалась ниоткуда, а
        # карточка сочиняла причину на месте. §4.3 требует названной причины, и причина с
        # числом проверяема, а без числа — нет.
        # ⚠ Средняя линия — SMA20 (`bbands_middle`), не EMA20. До 2026-08-09 здесь
        # стояла `ema(20)`, и ширина полос делилась на чужую величину — расхождение
        # нашла сверка с первоисточниками (Боллинджер считает и σ, и середину по SMA).
        sq = factors.squeeze(_series(bars, indicators.bbands_upper()),
                             _series(bars, indicators.bbands_lower()),
                             _series(bars, indicators.bbands_middle()))
        parts.append(f"полосы {sq.width_pct:.2f}% (уже {sq.percentile:.0f}% истории)"
                     if sq else _short("bb_upper", len(bars), d.symbol, tf, "полосы"))
        # Стр. 69 называет ОБЕ скользящие — «этих скользящих ИЛИ ОДНОЙ ИЗ НИХ», — и на
        # скриншоте настроек показаны обе с длиной 200. Считалась только EMA (М-16/М-13).
        for name, expr200 in (("EMA200", indicators.ema(200)),
                              ("MA200", indicators.sma(200))):
            # Тот же явный допуск: ewm отдаёт числа с первого бара, но каноничны они
            # с 200-го (admission.CANONICAL_FROM_BARS — раннее значение зависит от
            # точки старта ряда, замер: до 6.7% от цены).
            if admission.check("ema200", len(bars), d.symbol, tf) is not None:
                parts.append(_short("ema200", len(bars), d.symbol, tf, name))
                continue
            ma = factors.ma_touch(_series(bars, expr200), bars[-1].close)
            parts.append(f"{name} в {ma.distance_pct:.2f}% от цены" if ma
                         else _short("ema200", len(bars), d.symbol, tf, name))
        out.append(f"  {TF_LABEL.get(tf, tf):>3}  " + " · ".join(parts))
    return "\n".join(out) + "\n"


def _short(quantity: str, bars: int, symbol: str, timeframe: str, label: str) -> str:
    """Почему фактора нет — ЧИСЛОМ. Причину даёт `admission.check`, а не эта функция.

    Величина может отсутствовать и не из-за длины ряда; тогда `check` молчит, и здесь
    говорится ровно то, что известно, — «не посчитан», без выдуманного объяснения (§4.3).
    """
    why = admission.check(quantity, bars, symbol, timeframe)
    if why is not None:
        return f"{label}: {why.reason}"
    # Длины ряда хватает, а значения нет — причина другая, и придумывать её нельзя.
    return f"{label}: значения нет, хотя длины ряда достаточно"


def _series(bars: list[Bar], expr: pl.Expr) -> list[float | None]:
    df = pl.DataFrame({
        "open": [b.open for b in bars], "high": [b.high for b in bars],
        "low": [b.low for b in bars], "close": [b.close for b in bars],
    })
    return list(df.select(expr.alias("v"))["v"])


def _rr(x: float | None) -> str:
    return "нет цели" if x is None else f"1к{x:.2f}"


def _event(ev: breach.Breach) -> str:
    kind = {"puncture": "прокол", "breakout": "пробой",
            "unresolved": "курс вердикта не даёт", "open": "не разрешилось"}
    depth = abs(ev.extreme - ev.level) / ev.level * 100 if ev.level else 0.0
    return (f"{kind[ev.kind.value]} с бара {ev.start_index}, глубина {depth:.2f}%, "
            f"полных тел {ev.bodies}")


def _tf_ms(tf: str) -> int:
    return TIMEFRAME_MS[tf]
