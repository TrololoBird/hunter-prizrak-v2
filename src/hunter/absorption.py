"""Уторговка у зоны ПП — СЫРАЯ мера, без порога и без вердикта.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Источники (сырьё собрано в docs/audit/absorption-2026-08-17.md, техника выбрана
владельцем 2026-08-17):

  корпус, конспект видео 2026-08-09, seg6.md:46 — «отмена переприора: уторговка выше
  зоны снимает слом → зона становится слабой… тогда не шортить её, а ждать НОВЫЙ
  разворот сверху»; seg6.md:21 (кадр d-0173) — «снимаем слом-переприор, если
  уторговались выше», против «шпили» (тонкого прокола);
  seg6.md:65 — «возврат и торговля выше зоны слома "полноценно отменяет" слом» —
  критерий отмены, которого в PDF в явном виде нет.

Мера автора ПРОФИЛЬНАЯ, не скоростная: наторгованный объём в бинах ЗА зоной слома
после его подтверждения. Сторона сделки не нужна — запрет CVD (§3) не задет.

⚠ ЧЕГО В ИСТОЧНИКАХ НЕТ — здесь НЕ ВЫДУМАНО (absorption-2026-08-17.md §4): порог
«уторговались», окно счёта и единица счёта не названы ни курсом, ни корпусом.
Поэтому модуль возвращает ВЕЛИЧИНУ со знаменателем, а вердикт «слом снят» не
выносит: порог закрывается замером на истории с контролем (решётка случайных
порогов), и только после замера станет правилом. Окно взято инструментально —
от закрытия бара подтверждения до последнего закрытого бара ряда — и названо
в выдаче числом баров, чтобы замер порога мог его пересмотреть.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .bars import TIMEFRAME_MS
from .models import Bar, NotReady, TradeWindows
from .pereprior import Pereprior, PPSide


class AbsorptionRead(BaseModel):
    """Сколько наторговано ЗА зоной ПП после подтверждения слома.

    «За зоной» — против направления слома: у ПП в шорт (сломан лой) считаются бины
    с нижней границей СТРОГО ВЫШЕ `zone_hi`, у ПП в лонг — строго ниже `zone_lo`
    (seg6.md:46 и зеркально). Неравенство строгое — консервативно: бин, касающийся
    границы зоны, считается зоной, а не уторговкой. У сделочного источника цена бина —
    точная цена сделки; у свечного (`CandleWindows`) объём бара размазан по бинам его
    диапазона, и строгость режет пограничный бин в пользу зоны.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    qty_beyond: float
    qty_window: float
    """Весь объём окна — знаменатель. Доля без знаменателя запрещена (§4.3).

    ⚠ Числа СДЕЛОК здесь нет умышленно: мера автора — объём («наторгованный»), а
    свечной источник профиля (`CandleWindows`, решение владельца 2026-08-12) счётчики
    сделок не заполняет — поле печаталось бы ложным нулём."""

    window_from_ms: int
    window_to_ms: int
    bars_after_confirm: int
    """Длина окна в барах своего ТФ — чтобы читатель видел, НА ЧЁМ посчитана доля."""

    @property
    def share(self) -> float:
        # Деление без страховки «else 0.0»: пустой знаменатель отсечён в measure()
        # отказом, а не превращён в ложный ноль. Если объект собрали в обход measure
        # с qty_window=0 — громкий ZeroDivisionError честнее тихого 0.0%.
        return self.qty_beyond / self.qty_window


def measure(
    pp: Pereprior, bars: list[Bar], timeframe: str, trades: TradeWindows | None
) -> AbsorptionRead | NotReady:
    """Величина уторговки за зоной ПП. Не измерено — отказ с причиной, не ноль (§4.3)."""
    if trades is None:
        return NotReady(reason="поток сделок не передан прогону")
    step = TIMEFRAME_MS.get(timeframe)
    if step is None:
        return NotReady(reason=f"неизвестный таймфрейм {timeframe!r}")
    if not 0 <= pp.confirmed_at_index < len(bars):
        return NotReady(reason=f"индекс подтверждения {pp.confirmed_at_index} вне ряда")
    from_ms = bars[pp.confirmed_at_index].open_ms + step
    to_ms = bars[-1].open_ms + step
    if from_ms >= to_ms:
        return NotReady(reason="после бара подтверждения не закрылось ни одного бара")
    hist = trades.window(from_ms, to_ms)
    if isinstance(hist, NotReady):
        return hist
    if hist.qty_seen <= 0:
        # Пустой знаменатель — отказ, не ноль (§4.3): доля 0/0, напечатанная как
        # 0.0%, читалась бы как «уторговки нет», хотя мера не состоялась вовсе.
        return NotReady(reason="в окне после подтверждения не наторговано ничего — "
                               "доля 0/0 не печатается")
    # Суммирование по СОРТИРОВАННЫМ индексам: порядок сложения float не зависит от
    # порядка вставки в словарь — карточка обязана быть детерминированной (§10.6).
    qty_beyond = 0.0
    for idx in sorted(hist.qty_by_bin):
        price = float(hist.bin_price(idx))
        beyond = price > pp.zone_hi if pp.side is PPSide.SHORT else price < pp.zone_lo
        if beyond:
            qty_beyond += hist.qty_by_bin[idx]
    return AbsorptionRead(
        qty_beyond=qty_beyond,
        qty_window=hist.qty_seen,
        window_from_ms=from_ms,
        window_to_ms=to_ms,
        bars_after_confirm=(to_ms - from_ms) // step,
    )
