"""Скриншот карты уровней — в стилистике обзоров автора курса.

МОДУЛЬ ВЫВОДА: читает готовый расчёт и рисует; сам не считает ничего (§10.2 — расчёт
живёт в `engine.decide`, карта — в леджере). Введён 2026-08-10 решением владельца:
бот доставки отвечает на тикер скриншотом графика + текстом в формате канала.

Стилистика снята с 4 скриншотов обзора 09.08.2026 и видео (протокол
docs/audit/author-review-2026-08-09-vs-bot.md): светлый фон, зелёные/красные свечи,
цветные ГОРИЗОНТАЛЬНЫЕ ПОЛОСЫ зон с ценовыми плашками справа; у автора жёлтые —
локальные/промежуточные зоны, зелёные — глобальные лонговые, красные — глобальные
шортовые. Здесь то же правило: старшие ТФ (4ч/1Д/1Н) красятся по стороне, младшие —
жёлтым с цветной кромкой по стороне.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # серверный рендер: экрана нет и не должно быть
import matplotlib.pyplot as plt
from matplotlib import patches

from .models import Bar

SENIOR_TFS = ("4h", "1d", "1w")
"""Старшие ТФ красятся по стороне (зелёный/красный), младшие — жёлтым: так у автора."""

_UP, _DN = "#2f9e6e", "#d24a43"
_LONG_FILL, _LONG_EDGE = "#c8ecc9", "#3f9e46"
_SHORT_FILL, _SHORT_EDGE = "#f6c6c4", "#c94f47"
_LOCAL_FILL, _LOCAL_EDGE = "#f7f0a8", "#c9b616"
_PP_FILL, _PP_EDGE = "#ddd2f2", "#8a6fc9"
_STRUCT_FILL, _STRUCT_EDGE = "#bfdde6", "#6fa8bd"
"""Бокс СТРУКТУРЫ — голубой, как на разметке автора курса (скриншот владельца
2026-08-17). Цвет один на обе стороны: структура это факт истории, а не намерение."""


@dataclass(frozen=True)
class ZoneSpec:
    """Одна полоса на графике. `kind`: level | pp."""

    side: str
    timeframe: str
    price: float
    zone_lo: float
    zone_hi: float
    kind: str = "level"
    entry_rule: str = ""
    """Чем уровень торгуется: limit | confirmation | retest_flipped. Пусто — не записано
    (строка карты до схемы 6). Нужен, чтобы уровень, которого цена УЖЕ касалась, не
    выглядел как свежий: курс снимает лимитки на первое касание (стр. 25)."""

    state: str = "active"
    """Судьба уровня: active | worked_off | flipped. Нужна значкам разметки — красной
    стрелке отработки (стр. 25) и крестику отмены (стр. 43)."""

    retired_at_ms: int = 0
    """Когда уровень снят с карты. Ноль — не снят либо не передано. Задаёт, НА КАКОМ
    баре ставить значок: без него стрелка встала бы в конец графика, то есть соврала бы
    о моменте события."""

    from_ms: int = 0
    to_ms: int = 0
    """Окно СТРУКТУРЫ во времени. Ноль — не передано, бокс не рисуется.

    ⚠ Заведено 2026-08-17 по образцу разметки автора курса, который владелец показал
    скриншотом. У автора структура — КОМПАКТНЫЙ БОКС на тех барах, где она произошла, а
    не полоса через весь экран; от неё вправо идёт линия уровня, а зона входа стоит
    отдельным прямоугольником в БУДУЩЕМ. Прежний рендер растягивал всё на всю ширину, и
    отличить «где накопление было» от «где ждём цену» было нельзя."""

    boundary_lo: float = 0.0
    boundary_hi: float = 0.0
    """Границы СТРУКТУРЫ, породившей уровень (стр. 23-26) — того самого боковика, внутри
    которого считался профиль. Ноль в обоих полях — границы не переданы, контур не рисуется.

    ⚠ Заведено 2026-08-17 по запросу владельца, и запрос был содержательным: на графике
    зона и структура сливались в одну полосу, хотя это РАЗНЫЕ величины. Зона — Value Area
    70% объёма, структура — контейнер, из которого она посчитана. Замер на BTW показал,
    что у 4 уровней из 14 зона оказалась ШИРЕ границ структуры (до 3×): профиль считается
    по временному окну структуры, а цена внутри окна выходит за границы боковика проколами.
    Пока обе величины рисовались одинаково, увидеть это было нельзя."""


def _zone_style(z: ZoneSpec) -> tuple[str, str]:
    if z.kind == "pp":
        return _PP_FILL, _PP_EDGE
    if z.timeframe in SENIOR_TFS:
        return (_LONG_FILL, _LONG_EDGE) if z.side == "long" else (_SHORT_FILL, _SHORT_EDGE)
    return _LOCAL_FILL, _LOCAL_EDGE


def chart_png(
    symbol: str,
    timeframe: str,
    bars: list[Bar],
    zones: list[ZoneSpec],
    out_path: Path,
    caption: str = "",
) -> Path:
    """Нарисовать свечи с полосами зон и ценовыми плашками. Возвращает путь PNG.

    Шкала цены — по свечам (±5%): зоны за пределами видимого НЕ растягивают график
    (урок разбора собственной визуализации 2026-08-10 — растяжка в 4.5× сплющивала
    свечи), полосы клипуются, невидимые зоны просто не рисуются — их называет ТЕКСТ
    сообщения бота.
    """
    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad

    # ПОЛЕ БУДУЩЕГО справа — там стоят зоны входа, как у автора курса: слева история,
    # справа то, чего ещё не было. Без него зона входа неотличима от следа накопления.
    future = max(12, len(bars) // 6)
    right = len(bars) + future

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor("#f6f7f5")
    ax.set_facecolor("#f0f2ef")
    ax.axvspan(len(bars) - 0.5, right, facecolor="#e9ecea", zorder=0)

    def bar_index(ms: int) -> int | None:
        """Номер бара по метке времени. None — метка вне окна графика."""
        if ms <= 0 or not bars:
            return None
        step = bars[1].open_ms - bars[0].open_ms if len(bars) > 1 else 0
        if step <= 0:
            return None
        idx = (ms - bars[0].open_ms) // step
        return int(idx) if 0 <= idx < len(bars) else None

    for z in sorted(zones, key=lambda z: z.zone_hi - z.zone_lo, reverse=True):
        if z.zone_lo > hi or z.zone_hi < lo:
            continue
        fill, edge = _zone_style(z)
        z_lo, z_hi = max(z.zone_lo, lo), min(z.zone_hi, hi)

        # 1. БОКС СТРУКТУРЫ на СВОИХ барах — там, где накопление реально было.
        # ⚠ ТОЛЬКО СВОЙ ТФ. Замечание владельца 2026-08-17 («структуры вне свечей»)
        # проверено числами: расчёт верен, границы всех 14 уровней лежат внутри свечей
        # своего окна. Неверна была ОТРИСОВКА — бокс структуры ЧУЖОГО таймфрейма
        # растягивался по этому графику: часовая структура занимала 89 баров
        # пятнадцатиминутки, свечи внутри неё ходили 0.275…0.35, и полоса выглядела
        # «мимо свечей». У автора на графике ТФ размечены структуры этого же ТФ.
        # Уровни старших ТФ остаются линией и зоной входа — без бокса.
        i0 = bar_index(z.from_ms) if z.timeframe == timeframe else None
        i1 = bar_index(z.to_ms) if z.timeframe == timeframe else None
        drawn_box = False
        if i0 is not None and z.boundary_lo > 0 and z.boundary_hi > z.boundary_lo:
            i1 = i1 if i1 is not None else len(bars) - 1
            b_lo, b_hi = max(z.boundary_lo, lo), min(z.boundary_hi, hi)
            if b_lo < hi and b_hi > lo and i1 >= i0:
                # РАМКА БЕЗ ЗАЛИВКИ — так у автора курса. Проверено по PDF (стр. 13,
                # 18, 20, 30, 34): структура обводится тонкой бирюзовой рамкой, сквозь
                # которую видны свечи, а проколы за границы ДОПУСКАЮТСЯ и торчат наружу
                # (стр. 18: «если на 3++ точках были проколы за границы - стоп всегда
                # ставится за этот прокол»). Заливка прятала бы и свечи, и проколы.
                ax.add_patch(patches.Rectangle(
                    (i0 - 0.5, b_lo), max(i1 - i0 + 1, 1),
                    max(b_hi - b_lo, (hi - lo) * 0.004),
                    facecolor="none", edgecolor=_STRUCT_EDGE, linewidth=1.4,
                    zorder=6,
                ))
                drawn_box = True

        # 2. ЛИНИЯ УРОВНЯ — от структуры вправо, а не через весь экран.
        start = (i0 if drawn_box and i0 is not None else 0)
        # ⚠ ЛИНИЯ ОБРЫВАЕТСЯ В ТОЧКЕ ОТРАБОТКИ. Стр. 25 дословно: «этот уровень
        # становиться больше не актуальным, т.е. мы этот уровень удаляем». На рисунках
        # стр. 22 и 27 линия физически кончается там, где цена его забрала, — тянуть её
        # дальше значит показывать живым то, чего уже нет.
        i_ev = bar_index(z.retired_at_ms) if z.state != "active" else None
        line_end = i_ev if i_ev is not None else right
        if lo <= z.price <= hi:
            ax.plot([start, line_end], [z.price, z.price], color="#2b2b2b",
                    linewidth=1.0, zorder=3)
            if i_ev is None:
                ax.annotate(
                    f"{z.price:g}", xy=(right, z.price),
                    xytext=(3, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=8.5, fontweight="bold",
                    color="white",
                    bbox={"boxstyle": "square,pad=0.22", "facecolor": edge,
                          "edgecolor": "none"},
                    zorder=6, annotation_clip=False,
                )

        # 3. ЗОНА ВХОДА — прямоугольник В БУДУЩЕМ, подписанный стороной сделки.
        if z.kind == "level" and z.entry_rule in ("limit", "retest_flipped"):
            ax.add_patch(patches.Rectangle(
                (len(bars) + future * 0.15, z_lo), future * 0.75,
                max(z_hi - z_lo, (hi - lo) * 0.004),
                facecolor=fill, edgecolor=edge, linewidth=1.2, alpha=0.9, zorder=4,
            ))
            ax.annotate(
                "BUY" if z.side == "long" else "SELL",
                xy=(len(bars) + future * 0.52, (z_lo + z_hi) / 2),
                va="center", ha="center", fontsize=8, fontweight="bold",
                color="#1b3a1b" if z.side == "long" else "#4a1414", zorder=6,
            )
        # 3б. ЗНАЧКИ СУДЬБЫ — как на разметке автора: красная стрелка там, где уровень
        # отработан (стр. 25 «мы этот уровень удаляем»), синий крестик там, где пробит
        # и сменил сторону (стр. 43). Ставятся НА БАРЕ СОБЫТИЯ, а не в конце графика:
        # значок в неверном месте хуже отсутствующего — он утверждает время, которого
        # не было.
        if z.kind == "level" and z.state in ("worked_off", "flipped"):
            if i_ev is not None and lo <= z.price <= hi:
                if z.state == "worked_off":
                    # ЦВЕТ И НАПРАВЛЕНИЕ — по словарю значков автора, снятому с 16
                    # страниц PDF: ЗЕЛЁНАЯ стрелка ВВЕРХ под лонговым уровнем, КРАСНАЯ
                    # ВНИЗ над шортовым (стр. 19, 21-32, 39, 41, 43, 44, 46, 47).
                    # Прежняя редакция красила обе красным — это была моя выдумка.
                    up = z.side == "long"
                    dy = (hi - lo) * 0.055
                    ax.annotate(
                        "", xy=(i_ev, z.price + (dy if up else -dy)),
                        xytext=(i_ev, z.price + (-dy if up else dy) * 0.35),
                        arrowprops={"arrowstyle": "-|>",
                                    "color": "#2f9e4f" if up else "#d0342c",
                                    "linewidth": 2.4, "mutation_scale": 16},
                        zorder=8, annotation_clip=False,
                    )
                else:
                    # ⚠ КРЕСТИКА В КУРСЕ НЕТ. Проверено по всем 69 страницам: значков X
                    # нет ни на одной, слова «крестик» нет в тексте. Курс велит уровень
                    # СТИРАТЬ (стр. 25), и на рисунках линия просто обрывается.
                    # Крестик взят с РАЗМЕТКИ КАНАЛА автора (скриншот владельца
                    # 2026-08-17) — источник второго ряда, и здесь это названо, а не
                    # выдано за курс.
                    ax.plot([i_ev], [z.price], marker="x", color="#2f6fd0",
                            markersize=11, markeredgewidth=2.4, zorder=8)

        if z.kind == "pp":
            # Зона переприора — узкая полоса у правого края: это зона тени свечи слома,
            # а не область входа лимитками.
            ax.add_patch(patches.Rectangle(
                (len(bars) - future * 0.35, z_lo), future * 0.45,
                max(z_hi - z_lo, (hi - lo) * 0.003),
                facecolor=fill, edgecolor=edge, linewidth=1.0, alpha=0.8, zorder=4,
            ))

    w = 0.62
    for i, b in enumerate(bars):
        c = _UP if b.close >= b.open else _DN
        ax.plot([i, i], [b.low, b.high], color=c, linewidth=0.9, zorder=5)
        body_lo, body_hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(patches.Rectangle(
            (i - w / 2, body_lo), w, max(body_hi - body_lo, (hi - lo) * 0.0012),
            facecolor=c, edgecolor=c, zorder=5,
        ))

    # ПРОФИЛЬ ОБЪЁМА у правого края — как на разметке автора курса. Считается по
    # ВИДИМЫМ барам: объём каждого раскладывается равномерно по его диапазону
    # (то же приближение, что у `profile_source.CandleWindows`), и это честное
    # приближение, а не тот профиль, из которого посчитаны уровни: тот строится по
    # минутным свечам окна структуры, а здесь — по барам этого графика.
    rows_n = 48
    step_px = (hi - lo) / rows_n
    if step_px > 0:
        buckets = [0.0] * rows_n
        for b in bars:
            k0 = max(0, min(rows_n - 1, int((b.low - lo) / step_px)))
            k1 = max(0, min(rows_n - 1, int((b.high - lo) / step_px)))
            share = b.volume / (k1 - k0 + 1)
            for k in range(k0, k1 + 1):
                buckets[k] += share
        vmax = max(buckets) or 1.0
        poc_k = buckets.index(max(buckets))
        width_px = future * 0.9
        for k, v in enumerate(buckets):
            if v <= 0:
                continue
            colr = "#c8b93a" if k == poc_k else "#9fb8d4"
            ax.add_patch(patches.Rectangle(
                (right - v / vmax * width_px, lo + k * step_px),
                v / vmax * width_px, step_px * 0.92,
                facecolor=colr, edgecolor="none", alpha=0.55, zorder=1,
            ))

    ax.set_xlim(-1, right)
    ax.set_ylim(lo, hi)
    ax.set_yticks([])
    ax.set_xticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    title = f"{symbol} · {timeframe} · карта уровней hunter"
    if caption:
        title += f" · {caption}"
    ax.set_title(title, loc="left", fontsize=11, color="#333")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
