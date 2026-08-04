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
    accumulation,
    breach,
    factors,
    geometry,
    indicators,
    levels,
    pereprior,
    priority,
    swings,
)
from .bars import TIMEFRAME_MS
from .models import Bar, NotReady, TradeWindows

TF_LABEL = {"5m": "5м", "15m": "15м", "1h": "1ч", "4h": "4ч", "1d": "1Д", "1w": "1Н"}
SIDE_LABEL = {"long": "ЛОНГ", "short": "ШОРТ"}
TREND_LABEL = {"up": "восходящий", "down": "нисходящий", "none": "не определён"}
STATE_LABEL = {"active": "активен", "worked_off": "отработан", "flipped": "флипнут"}
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


def _num(x: float | Decimal, digits: int = 8) -> str:
    """Число без экспоненты и без хвостовых нулей — иначе дифф шумит на форматировании."""
    s = f"{float(x):.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def render(
    symbol: str,
    series: dict[str, list[Bar]],
    trades: TradeWindows | None,
    timeframes: tuple[str, ...],
) -> str:
    """Карточка символа по всем поданным ТФ. Чистая функция от кадров.

    Порядок ТФ задаётся ЗДЕСЬ — по длительности, от младшего к старшему, — а не берётся
    от вызывающего. Иначе карточка зависит от того, как её позвали: живой прогон идёт по
    порядку вселенной, а повтор — по именам файлов, и тексты расходятся при неизменном
    расчёте. Замер 2026-08-04: первый же повтор дал «изменилось» у 2 символов из 2 ровно
    по этой причине.
    """
    timeframes = tuple(sorted(timeframes, key=lambda t: TIMEFRAME_MS.get(t, 0)))
    out: list[str] = [f"СИМВОЛ {symbol}"]

    scans: dict[str, accumulation.AccumulationScan] = {}
    trends: dict[str, swings.Trend] = {}
    sw_by_tf: dict[str, swings.SwingSet] = {}
    unbuilt: list[str] = []

    for tf in timeframes:
        bars = series.get(tf)
        if not bars:
            out.append(f"  {TF_LABEL.get(tf, tf)}: НЕТ КАДРОВ")
            continue
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            # §4.3: короткий ряд — это названная причина, а не молчаливый пропуск ТФ.
            out.append(f"  {TF_LABEL.get(tf, tf)}: {sw.reason}")
            continue
        sw_by_tf[tf] = sw
        trends[tf] = swings.trend(sw)
        scans[tf] = accumulation.detect(bars, sw, tf)

    out.append("")
    out.append("ТРЕНД И СТРУКТУРА")
    for tf in timeframes:
        if tf not in scans:
            continue
        sc, tr, bars = scans[tf], trends[tf], series[tf]
        tail = ("нет" if sc.open_tail is None
                else f"открыта {sc.open_tail.bars_open} баров, точек {sc.open_tail.points}")
        out.append(f"  {TF_LABEL.get(tf, tf):>3}  баров {sc.bars_scanned}  "
                   f"структур {len(sc.closed)}  распадов {sc.resets}  "
                   f"тренд {TREND_LABEL[tr.direction.value]} (держится на {tr.holds_for})  "
                   f"незакрытая структура: {tail}")

    out.append("")
    out.append("УРОВНИ (ПОК накоплений)")
    frozen, reasons = levels.build_all(symbol, series, trades, timeframes)
    unbuilt.extend(
        f"{TF_LABEL.get(u.timeframe, u.timeframe)}"
        + (f" структура {u.index}" if u.index is not None else "")
        + f": {u.reason}"
        for u in reasons
    )
    if not frozen:
        out.append("  уровней нет")
    for lvl in frozen:
        st = levels.status(lvl, series[lvl.timeframe])
        pr = priority.resolve(trends, lvl.timeframe)
        ag = priority.agreement(lvl.side, pr)
        s = geometry.build_setup(lvl, frozen)
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
        out.append(
            f"        стоп безопасный {_num(s.stop_safe_near)}…{_num(s.stop_safe_far)}  "
            f"рисковый {_num(s.stop_risky)}  "
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
        rr_far, rr_risk = s.rr(s.stop_safe_far), s.rr(s.stop_risky)
        out.append(f"        РР: безопасный {_rr(rr_far)}  рисковый {_rr(rr_risk)}  "
                   f"(стандарт курса 1к{_num(geometry.GOLDEN_RR, 0)})")
        if st.event is not None:
            out.append(f"        событие: {_event(st.event)}")

    if unbuilt:
        out.append("")
        out.append("УРОВЕНЬ НЕ ПОСТРОЕН — причины (§4.3)")
        for u in unbuilt:
            out.append(f"  {u}")

    out.append("")
    out.append("ПЕРЕПРИОР")
    any_pp = False
    for tf in timeframes:
        if tf not in scans:
            continue
        bars, sw = series[tf], sw_by_tf[tf]
        for pp in pereprior.detect(bars, sw):
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
    if not scans:
        out.append("  не считались: кадров нет")
    for tf in timeframes:
        if tf not in scans:
            continue
        bars, sw = series[tf], sw_by_tf[tf]
        parts: list[str] = []
        for name, expr in (("RSI", indicators.rsi()), ("MACD", indicators.macd_line())):
            for d in factors.divergences(bars, sw, _series(bars, expr), name):
                what = ("дивергенция" if d.kind is factors.DivergenceKind.DIVERGENCE
                        else "конвергенция")
                parts.append(f"{what} {name}")
        sq = factors.squeeze(_series(bars, indicators.bbands_upper()),
                             _series(bars, indicators.bbands_lower()),
                             _series(bars, indicators.ema(20)))
        parts.append(f"полосы {sq.width_pct:.2f}% (уже {sq.percentile:.0f}% истории)"
                     if sq else "полосы не посчитаны")
        ma = factors.ma_touch(_series(bars, indicators.ema(200)), bars[-1].close)
        parts.append(f"EMA200 в {ma.distance_pct:.2f}% от цены" if ma
                     else "EMA200 не определена (истории мало)")
        out.append(f"  {TF_LABEL.get(tf, tf):>3}  " + " · ".join(parts))
    return "\n".join(out) + "\n"


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
