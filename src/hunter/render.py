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


@dataclass(frozen=True)
class ZoneSpec:
    """Одна полоса на графике. `kind`: level | pp."""

    side: str
    timeframe: str
    price: float
    zone_lo: float
    zone_hi: float
    kind: str = "level"


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

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor("#f6f7f5")
    ax.set_facecolor("#f0f2ef")

    for z in sorted(zones, key=lambda z: z.zone_hi - z.zone_lo, reverse=True):
        if z.zone_lo > hi or z.zone_hi < lo:
            continue
        fill, edge = _zone_style(z)
        z_lo, z_hi = max(z.zone_lo, lo), min(z.zone_hi, hi)
        ax.add_patch(patches.Rectangle(
            (0, z_lo), len(bars) + 14, max(z_hi - z_lo, (hi - lo) * 0.002),
            facecolor=fill, edgecolor=edge, linewidth=1.0, alpha=0.75, zorder=1,
        ))
        if lo <= z.price <= hi:
            ax.axhline(z.price, color=edge, linewidth=1.3, zorder=2)
            ax.annotate(
                f"{z.price:g}", xy=(len(bars) + 13.5, z.price),
                xytext=(2, 0), textcoords="offset points",
                va="center", ha="left", fontsize=9, fontweight="bold",
                color="white",
                bbox={"boxstyle": "square,pad=0.25", "facecolor": edge,
                      "edgecolor": "none"},
                zorder=5, annotation_clip=False,
            )

    w = 0.62
    for i, b in enumerate(bars):
        c = _UP if b.close >= b.open else _DN
        ax.plot([i, i], [b.low, b.high], color=c, linewidth=0.9, zorder=3)
        body_lo, body_hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(patches.Rectangle(
            (i - w / 2, body_lo), w, max(body_hi - body_lo, (hi - lo) * 0.0012),
            facecolor=c, edgecolor=c, zorder=4,
        ))

    ax.set_xlim(-1, len(bars) + 14)
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
