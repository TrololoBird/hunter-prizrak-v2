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
from .bars import TIMEFRAME_MS, continuous_tail
from .models import Bar, NotReady

TF_LABEL = {"5m": "5м", "15m": "15м", "1h": "1ч", "4h": "4ч", "1d": "1Д", "1w": "1Н"}
SIDE_LABEL = {"long": "ЛОНГ", "short": "ШОРТ"}
TREND_LABEL = {"up": "восходящий", "down": "нисходящий", "none": "не определён"}
STATE_LABEL = {"active": "активен", "worked_off": "отработан", "flipped": "флипнут"}

STOP_BASIS_LABEL = {
    "anchor": "за стоповым объёмом либо проколом (стр. 18)",
    "margin": "запас за структуру (стр. 33)",
}
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
AGREE_LABEL = {"by_trend": "по тренду", "against_trend": "ПРОТИВ тренда",
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
        out.append(f"  {TF_LABEL.get(tf, tf):>3}  баров {sc.bars_scanned}  "
                   f"структур {len(sc.closed)}  распадов {sc.resets}  "
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
        )
        out.append(
            f"        границы {_num(lvl.boundary_lo)}…{_num(lvl.boundary_hi)}  "
            f"вход: {ENTRY_LABEL[st.entry_rule.value]}  "
            f"{AGREE_LABEL[ag.value]}"
            + (f" ({TF_LABEL.get(pr.timeframe, pr.timeframe)})" if pr.timeframe else "")
        )
        s = dec.setup
        if s is None:
            # ⚠ Геометрии НЕТ, и это не пропуск. Стоп, лестница и цели существуют только
            # у сделки, которая будет взята; печатать их для уровня, который система не
            # торгует, значит печатать сигнал, которого нет. Причина названа (§4.3).
            out.append(f"        СДЕЛКИ НЕТ: {dec.hold}")
            if st.event is not None:
                out.append(f"        событие: {_event(st.event)}")
            continue
        # ОДИН стоп и его основание. Прежде печатались три цены — безопасный ближний,
        # безопасный дальний и рисковый, — и выбор оставался оператору. Выставить можно
        # один; меню вместо сигнала делало РР неопределённым (стр. 18, 33).
        out.append(
            f"        стоп {_num(s.stop)} — {STOP_BASIS_LABEL[s.stop_basis.value]}  "
            f"({_pct(s.entry, s.stop)} от входа)  "
            f"{'закуп дробим' if s.split_orders else 'один ордер'}"
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
        for pp in pereprior.detect(bars, sw, tf):
            any_pp = True
            width = (pp.zone_hi - pp.zone_lo) / pp.zone_lo * 100 if pp.zone_lo else 0.0
            test = ("теста не было" if pp.tested_at_index is None
                    else f"тест через {pp.tested_at_index - pp.confirmed_at_index} баров")
            out.append(f"  {TF_LABEL.get(tf, tf):>3}  {PP_LABEL[pp.kind.value]} в "
                       f"{SIDE_LABEL[pp.side.value]}  слом {_num(pp.broken_price)}  "
                       f"зона тени {_num(pp.zone_lo)}…{_num(pp.zone_hi)} ({width:.3f}%)  "
                       f"{test}")
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
        for name, expr in (("RSI", indicators.rsi()), ("MACD", indicators.macd_line())):
            for div in factors.divergences(bars, sw, _series(bars, expr), name):
                what = ("дивергенция" if div.kind is factors.DivergenceKind.DIVERGENCE
                        else "конвергенция")
                parts.append(f"{what} {name}")
        # ⚠ Отсутствие фактора называется ЧИСЛАМИ, а не словами «истории мало». Требования
        # к истории замерены (`admission.REQUIRED_BARS`), и `admission.check` умеет
        # сказать «нужно N, есть M» — до 2026-08-06 эта функция не звалась ниоткуда, а
        # карточка сочиняла причину на месте. §4.3 требует названной причины, и причина с
        # числом проверяема, а без числа — нет.
        sq = factors.squeeze(_series(bars, indicators.bbands_upper()),
                             _series(bars, indicators.bbands_lower()),
                             _series(bars, indicators.ema(20)))
        parts.append(f"полосы {sq.width_pct:.2f}% (уже {sq.percentile:.0f}% истории)"
                     if sq else _short("bb_upper", len(bars), d.symbol, tf, "полосы"))
        ma = factors.ma_touch(_series(bars, indicators.ema(200)), bars[-1].close)
        parts.append(f"EMA200 в {ma.distance_pct:.2f}% от цены" if ma
                     else _short("ema200", len(bars), d.symbol, tf, "EMA200"))
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
