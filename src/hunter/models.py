"""Контракты между слоями. FOUNDATION.md §10.1 — словари между слоями запрещены.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Причина, названная в §10.1: 910 вхождений `dict[str, Any]` в прошлой реализации
породили класс «поле без продюсера» — ключ читается, никто его не пишет, ошибка молчит
годами. У модели такого не бывает: mypy падает на несуществующем атрибуте.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NotReady(BaseModel):
    """Данных нет. Причина обязательна — §4.3 запрещает молчаливый пропуск."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"не готово: {self.reason}"


class Bar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    open_ms: int = Field(ge=0)
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _ohlc_consistent(self) -> Bar:
        # Не «разумное значение», а определение свечи: экстремумы обязаны накрывать
        # открытие и закрытие. Нарушение означает битые данные, а не редкий рынок.
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError(
                f"бар {self.open_ms}: high/low не накрывают open/close "
                f"(o={self.open} h={self.high} l={self.low} c={self.close})"
            )
        if self.high < self.low:
            raise ValueError(f"бар {self.open_ms}: high < low")
        return self


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    """Идентификатор биржи (BTCUSDT). Нужен для архива — там свои имена."""

    tick_size: Decimal = Field(gt=0)
    """Шаг цены из PRICE_FILTER. §5: бины профиля привязаны к нему."""


class ClockSync(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    offset_ms: int
    """Сдвиг: серверное время минус локальное. Положительный = локальные отстают."""

    rtt_ms: int = Field(ge=0)
    """Круговая задержка замера. Неопределённость сдвига — ±rtt/2."""

    measured_at_local_ms: int
    samples: int = Field(ge=1)


class TradeHistogram(BaseModel):
    """Гистограмма «цена → объём» на реальных сделках. §5.

    Сторона сделки НЕ хранится умышленно: §3 запрещает CVD, а поле стороны —
    единственное, из чего он собирается.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    tick_size: Decimal = Field(gt=0)
    qty_by_bin: dict[int, float] = Field(default_factory=dict)
    count_by_bin: dict[int, int] = Field(default_factory=dict)
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
        """|сумма по бинам − сумма по сырью| / сумма по сырью."""
        if self.qty_seen == 0.0:
            return 0.0
        return abs(self.binned_qty_total() - self.qty_seen) / self.qty_seen


class BarBinnedTrades(BaseModel):
    """Сделки, разложенные по БАРАМ и бинам цены. §5 + §10.3.

    Зачем отдельно от `TradeHistogram`: та агрегатная и времени не хранит, поэтому вырезать
    из неё окно структуры нельзя. А профиль по стр. 26 натягивается ровно на бары структуры.
    Без этой раскладки уровень §2.2 на живом прогоне не построить вовсе — только из
    суточного архива.

    Сетка `bucket_ms` — самый младший используемый ТФ: бары старших ТФ кратны ему, значит
    окно любой структуры складывается из целых корзин без остатка.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    tick_size: Decimal = Field(gt=0)
    bucket_ms: int = Field(gt=0)
    qty: dict[int, dict[int, float]] = Field(default_factory=dict)
    """корзина (open_ms) → бин цены → объём."""

    cnt: dict[int, dict[int, int]] = Field(default_factory=dict)
    """То же по числу сделок. Хранится отдельно, потому что число сделок в окне нельзя
    восстановить из объёмов: попытка выдать за него число бинов была бы именем-ложью."""

    trades_seen: int = 0
    qty_seen: float = 0.0

    def add(self, price: float, qty: float, ts_ms: int) -> None:
        bucket = ts_ms - ts_ms % self.bucket_ms
        idx = int(Decimal(str(price)) // self.tick_size)
        self.qty.setdefault(bucket, {})
        self.cnt.setdefault(bucket, {})
        self.qty[bucket][idx] = self.qty[bucket].get(idx, 0.0) + qty
        self.cnt[bucket][idx] = self.cnt[bucket].get(idx, 0) + 1
        self.trades_seen += 1
        self.qty_seen += qty

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady:
        """Профиль по окну `[from_ms, to_ms)`. Пустое окно — отказ, а не пустой профиль."""
        if not self.qty:
            return NotReady(reason=f"{self.symbol}: сделок не собрано вовсе")
        first, last = min(self.qty), max(self.qty)
        if from_ms < first or to_ms > last + self.bucket_ms:
            return NotReady(
                reason=f"{self.symbol}: окно [{from_ms},{to_ms}) выходит за собранное "
                       f"[{first},{last + self.bucket_ms})"
            )
        h = TradeHistogram(symbol=self.symbol, tick_size=self.tick_size)
        for bucket, bins in self.qty.items():
            if not from_ms <= bucket < to_ms:
                continue
            counts = self.cnt.get(bucket, {})
            for idx, q in bins.items():
                n = counts.get(idx, 0)
                h.qty_by_bin[idx] = h.qty_by_bin.get(idx, 0.0) + q
                h.count_by_bin[idx] = h.count_by_bin.get(idx, 0) + n
                h.qty_seen += q
                h.trades_seen += n
        if not h.qty_by_bin:
            return NotReady(reason=f"{self.symbol}: в окне [{from_ms},{to_ms}) сделок нет")
        h.first_ms, h.last_ms = from_ms, to_ms
        return h


class OhlcvFetch(BaseModel):
    """Результат REST-засева: что принято и что отклонено. §4.3."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bars: list[Bar]
    rejected: list[str] = Field(default_factory=list)


class SeriesState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    bars: list[Bar] = Field(default_factory=list)
    gaps: list[tuple[int, int]] = Field(default_factory=list)
    """Пары (open_ms предыдущего, open_ms следующего) там, где сетка разорвана."""

    not_ready: NotReady | None = None
    rejected_bars: list[str] = Field(default_factory=list)
    """Бары, отклонённые как битые. §4.3: пропуск виден, а не замалчивается."""

    ws_bars: int = 0
    ws_unclosed_violations: int = 0
    ws_offgrid_violations: int = 0


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync: ClockSync
    series: dict[tuple[str, str], SeriesState] = Field(default_factory=dict)
    histograms: dict[str, TradeHistogram] = Field(default_factory=dict)
    binned: dict[str, BarBinnedTrades] = Field(default_factory=dict)
    seeded_bars: int = 0
    seed_checked: int = 0
    clock_drift_ms: int | None = None
    clock_recheck_after_s: int | None = None
    frames_written: int = 0
    cards_written: int = 0
    signals_recorded: int = 0
    outcomes_recorded: int = 0
