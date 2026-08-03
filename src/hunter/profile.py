"""Гистограмма «цена → объём» на реальных сделках. FOUNDATION.md §5.

§5: «Профиль объёма строится на реальных сделках (aggTrade), бины привязаны к
`tickSize` инструмента». Хранится не сырьё, а сумма объёма по бинам: профилю нужно
только это отображение, и при бине, равном tickSize, агрегация точна.

Сторона сделки НЕ хранится умышленно: §3 запрещает CVD, а поле стороны — единственное,
из чего он собирается.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True)
class TradeHistogram:
    symbol: str
    tick_size: Decimal
    qty_by_bin: dict[int, float] = field(default_factory=dict)
    count_by_bin: dict[int, int] = field(default_factory=dict)
    trades_seen: int = 0
    qty_seen: float = 0.0
    """Контроль: сумма по сырью до агрегации. Сверяется с суммой по бинам."""

    first_ms: int | None = None
    last_ms: int | None = None

    def bin_index(self, price: float) -> int:
        return int(Decimal(str(price)) // self.tick_size)

    def bin_price(self, idx: int) -> Decimal:
        """Нижняя граница бина."""
        return Decimal(idx) * self.tick_size

    def add(self, price: float, qty: float, ts_ms: int) -> None:
        idx = self.bin_index(price)
        self.qty_by_bin[idx] = self.qty_by_bin.get(idx, 0.0) + qty
        self.count_by_bin[idx] = self.count_by_bin.get(idx, 0) + 1
        self.trades_seen += 1
        self.qty_seen += qty
        if self.first_ms is None or ts_ms < self.first_ms:
            self.first_ms = ts_ms
        if self.last_ms is None or ts_ms > self.last_ms:
            self.last_ms = ts_ms

    def binned_qty_total(self) -> float:
        return sum(self.qty_by_bin.values())

    def reconciliation_error(self) -> float:
        """|сумма по бинам − сумма по сырью| / сумма по сырью. Приёмка 1.5."""
        if self.qty_seen == 0.0:
            return 0.0
        return abs(self.binned_qty_total() - self.qty_seen) / self.qty_seen
