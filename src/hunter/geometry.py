"""Геометрия сделки §2.7. Источник — мини-курс, стр. 9, 19, 24, 30, 33, 18.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

  стр. 30  вход: «надёжнее всего брать от уровня ПОК (немного выше/ниже, т.к. идеально
           может не доходить)… чтобы точно забрало ваш ордер — используем 2-3 ордера,
           на зону, и на уровень ПОК». Крупное накопление (1Д-1Н-1М) — делить на зону и
           уровень; мелкое (5м-1ч) — «эффективнее входить 1 ордером от уровня»
  стр. 33  стоп: «Безопасный СТОП за дно структуры с ЗАПАСОМ 1-3%… Рисковый СТОП: прямо
           за лой структуры или же за объёмную зону»
  стр. 18  «Если в диапазоне 2-5% от границы есть стоповый объём — база мелкого ТФ — или
           Лой того же ТФ или ТФ-1 — ИДЕАЛЬНО стоп прятать за них»
  стр. 24  цель: «другой сопоставимый уровень 4ч ТФ, либо уровень 1Д тф, если он
           ближайший… могут быть взяты как промежуточные цели с небольшими тейками…
           Уровни ТФ-2 (15м и ниже) обычно не берутся в расчет»
  стр. 19  «По верхней границе делаете тейк 50% — но не 100%»
  стр. 9   «Золотым стандартом считаются сделки с РР 1к3 и выше»

Проценты здесь допустимы вопреки §4.1: он сам делает исключение для величин, которые курс
задаёт в процентах прямо, и называет ровно этот случай — стоп 1-3% за структуру.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .levels import Level, LevelSide
from .levels import nested as nested_levels

TF_ORDER = ("5m", "15m", "1h", "4h", "1d", "1w")
"""Стр. 17: «Мы используем основные ТФ (5м/15м/час/4ч/1Д/1Н)». Порядок задаёт ТФ-1 и ТФ-2."""

STOP_MARGIN_MIN_PCT = 1.0
STOP_MARGIN_MAX_PCT = 3.0
"""Стр. 33: «с запасом 1-3%». Курс даёт ДИАПАЗОН и внутри него не выбирает.

Поэтому здесь не выбирается тоже: считаются оба края, и RR приводится для обоих.
Выбрать один — значит назначить число, которого в источнике нет.
Стр. 19 в одном месте называет 1-5%, но 1-3% встречается трижды (стр. 33, 36, 58).
"""

PARTIAL_TAKE_PCT = 50.0
"""Стр. 19: «делаете тейк 50% — но не 100%». Число названо курсом прямо."""

GOLDEN_RR = 3.0
"""Стр. 9: «Золотым стандартом считаются сделки с РР 1к3 и выше». Не гейт, а отметка."""

BIG_STRUCTURE_TFS = frozenset({"1d", "1w"})
"""Стр. 30: крупное накопление — закуп делить на зону И уровень. Мелкое — одним ордером."""


class TargetRole(StrEnum):
    PRIMARY = "primary"
    """Сопоставимый уровень своего ТФ либо ближайший старший (стр. 24)."""

    INTERMEDIATE = "intermediate"
    """ТФ-1: «промежуточные цели с небольшими тейками» (стр. 24)."""


class Target(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: TargetRole
    price: Decimal
    timeframe: str
    distance_pct: float


class Setup(BaseModel):
    """Сделка от уровня: вход, два стопа, цели. Ничего не сворачивается в одно число."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    entry: Decimal
    """ПОК уровня (стр. 30)."""

    entry_zone_lo: Decimal
    entry_zone_hi: Decimal
    split_orders: bool
    """Стр. 30: крупная база — делить закуп на зону и уровень; мелкая — один ордер."""

    ladder: tuple[Decimal, ...]
    """Все уровни внутри большой структуры, снизу вверх (стр. 32).

    Первым элементом всегда идёт `entry` — основной ПОК; дальше ПОК вложенных структур
    младшего ТФ. Пустой лестницы не бывает: одиночный уровень — это лестница из одного.
    Стр. 32: «лучше закуп делать на все уровни».
    """

    @property
    def average_entry_equal_shares(self) -> Decimal:
        """Средняя ТВХ при РАВНЫХ долях на каждый уровень лестницы.

        Стр. 32 называет цель — «что бы ваша средняя твх была максимально безопасная», —
        но долей НЕ задаёт. Поэтому равные доли названы прямо в имени: это допущение
        читателя, а не правило курса, и молча зашивать его в поле `entry` нельзя.
        """
        return sum(self.ladder, Decimal(0)) / Decimal(len(self.ladder))

    stop_safe_near: Decimal
    stop_safe_far: Decimal
    """Безопасный стоп за структуру с запасом. Два края диапазона 1-3% (стр. 33)."""

    stop_risky: Decimal
    """Прямо за границу структуры, без запаса (стр. 33)."""

    structural_anchor: Decimal | None
    """Стоповый объём или лой ТФ-1 в 2-5% от границы — «идеально стоп прятать за них» (стр. 18).

    None означает «такого якоря не подано», а не «его нет»: искать его — работа
    вызывающего, у которого есть структуры младшего ТФ.
    """

    targets: tuple[Target, ...]
    partial_take_pct: float = PARTIAL_TAKE_PCT

    def rr(self, stop: Decimal) -> float | None:
        """РР до ПЕРВОЙ цели (стр. 9). None — целей нет, а не «ноль»."""
        primary = [t for t in self.targets if t.role is TargetRole.PRIMARY]
        if not primary:
            return None
        risk = abs(self.entry - stop)
        if risk == 0:
            return None
        return float(abs(primary[0].price - self.entry) / risk)


def _tf_step(a: str, b: str) -> int | None:
    """На сколько ступеней ТФ `b` младше `a`. None — один из ТФ не из основных."""
    if a not in TF_ORDER or b not in TF_ORDER:
        return None
    return TF_ORDER.index(a) - TF_ORDER.index(b)


def build_targets(level: Level, pool: tuple[Level, ...]) -> tuple[Target, ...]:
    """Цели по стр. 24: свой ТФ и старшие — основные, ТФ-1 — промежуточные, ТФ-2 — нет.

    Целью служит уровень ПРОТИВОПОЛОЖНОЙ стороны по ходу сделки: от лонгового уровня
    идём вверх к шортовому (стр. 24, пример «цена забирает 4ч Лонг уровень; ваша цель —
    шорт уровень 4ч тф»).
    """
    entry = level.price
    up = level.side is LevelSide.LONG
    want = LevelSide.SHORT if up else LevelSide.LONG

    out: list[Target] = []
    for other in pool:
        if other.symbol != level.symbol or other.side is not want:
            continue
        if (other.price > entry) is not up or other.price == entry:
            continue
        step = _tf_step(level.timeframe, other.timeframe)
        if step is None or step >= 2:
            # Стр. 24: «Уровни ТФ-2 (15м и ниже) обычно не берутся в расчёт».
            continue
        role = TargetRole.INTERMEDIATE if step == 1 else TargetRole.PRIMARY
        out.append(Target(role=role, price=other.price, timeframe=other.timeframe,
                          distance_pct=float(abs(other.price - entry) / entry * 100)))
    return tuple(sorted(out, key=lambda t: t.distance_pct))


def build_setup(
    level: Level,
    pool: tuple[Level, ...] = (),
    *,
    structural_anchor: Decimal | None = None,
    margin_min_pct: float = STOP_MARGIN_MIN_PCT,
    margin_max_pct: float = STOP_MARGIN_MAX_PCT,
) -> Setup:
    """Сделка от уровня. Ничего не выбирает за оператора: оба стопа и оба края запаса.

    Курс даёт два стопа (безопасный и рисковый) и диапазон запаса — здесь всё это
    сообщается как есть. Свернуть в одно число значило бы принять решение, которого
    источник не принимает.
    """
    up = level.side is LevelSide.LONG
    edge = level.boundary_lo if up else level.boundary_hi
    sign = Decimal(-1) if up else Decimal(1)

    def with_margin(pct: float) -> Decimal:
        return edge + sign * edge * Decimal(str(pct)) / Decimal(100)

    inner = nested_levels(level, pool)
    return Setup(
        level=level,
        entry=level.price,
        entry_zone_lo=level.zone_lo,
        entry_zone_hi=level.zone_hi,
        split_orders=level.timeframe in BIG_STRUCTURE_TFS,
        ladder=tuple(sorted({level.price, *(x.price for x in inner)})),
        stop_safe_near=with_margin(margin_min_pct),
        stop_safe_far=with_margin(margin_max_pct),
        stop_risky=edge,
        structural_anchor=structural_anchor,
        targets=build_targets(level, pool),
    )


