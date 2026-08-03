"""Детерминированный повтор и дифф карточки. FOUNDATION.md §10.3, §10.6 условие 2.

§10.6 дословно: «Любое изменение расчёта предъявляется как дифф детерминированного повтора
на сохранённых кадрах — на этих сохранённых данных карточка была такой, стала такой. Вы
видите два текста и разницу между ними».

Это единственный инструмент проверки, не требующий чтения кода. Порядок работы:
  1. `hunter run` сохраняет кадры И карточку, порождённую этими кадрами;
  2. расчёт меняется;
  3. `hunter replay <прогон>` заново строит карточку ИЗ ТЕХ ЖЕ кадров и печатает разницу.

Совпадение построчное, а не «похоже»: карточка — чистая функция от кадров (`card.render`),
поэтому любое расхождение означает, что изменился расчёт, а не данные.
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel, ConfigDict

from . import card, store
from .models import NotReady


class SymbolDiff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    identical: bool
    diff: str
    """Пустая строка при совпадении. Формат — унифицированный дифф."""

    lines_before: int
    lines_after: int


class ReplayResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    symbols: tuple[SymbolDiff, ...]
    skipped: tuple[str, ...]
    """Символы, которые повторить не удалось, с причиной. Молчания здесь быть не должно."""

    @property
    def changed(self) -> tuple[SymbolDiff, ...]:
        return tuple(s for s in self.symbols if not s.identical)


def replay_symbol(run_id: str, dir_name: str) -> SymbolDiff | NotReady:
    """Пересобрать карточку одного символа из кадров и сравнить с сохранённой."""
    meta = store.read_meta(run_id, dir_name)
    if isinstance(meta, NotReady):
        return meta
    symbol, tick, bucket = meta

    saved = store.read_card(run_id, dir_name)
    if isinstance(saved, NotReady):
        return saved

    tfs = store.saved_timeframes(run_id, dir_name)
    if not tfs:
        return NotReady(reason=f"{symbol}: кадров баров в прогоне нет")
    series = {tf: store.read_bars(run_id, dir_name, tf) for tf in tfs}

    # Имя символа берётся из meta, а не из имени каталога: иначе искажённое
    # `BTC_USDT_USDT` попадает в тексты причин и уезжает в карточку владельца.
    trades = store.read_binned_trades(run_id, dir_name, tick, bucket, symbol)
    binned = None if isinstance(trades, NotReady) else trades

    rebuilt = card.render(symbol, series, binned, tfs)
    diff = "".join(difflib.unified_diff(
        saved.splitlines(keepends=True), rebuilt.splitlines(keepends=True),
        fromfile=f"{symbol} сохранено", tofile=f"{symbol} пересчитано", n=2,
    ))
    return SymbolDiff(
        symbol=symbol, identical=not diff, diff=diff,
        lines_before=len(saved.splitlines()), lines_after=len(rebuilt.splitlines()),
    )


def replay_run(run_id: str) -> ReplayResult | NotReady:
    dirs = store.saved_symbols(run_id)
    if not dirs:
        return NotReady(reason=f"прогон {run_id} не найден или пуст — "
                               f"{store.FRAMES_DIR / run_id}")
    done: list[SymbolDiff] = []
    skipped: list[str] = []
    for d in dirs:
        r = replay_symbol(run_id, d)
        if isinstance(r, NotReady):
            skipped.append(r.reason)
        else:
            done.append(r)
    return ReplayResult(run_id=run_id, symbols=tuple(done), skipped=tuple(skipped))


def print_result(r: ReplayResult, show_diff: bool) -> int:
    """Печатает вердикт по-русски. Возвращает число расхождений (§7.5)."""
    print()
    print("=" * 78)
    print(f"ПОВТОР ПРОГОНА {r.run_id} — FOUNDATION.md §10.6, условие 2")
    print("=" * 78)
    print(f"\nсимволов повторено: {len(r.symbols)}")
    for s in r.symbols:
        mark = "совпало" if s.identical else "ИЗМЕНИЛОСЬ"
        print(f"  {mark:11} {s.symbol:22} строк {s.lines_before} → {s.lines_after}")
    if r.skipped:
        print(f"\nне повторено: {len(r.skipped)} (§4.3 — причина названа, не скрыта)")
        for why in r.skipped:
            print(f"  {why}")

    changed = r.changed
    print()
    if not r.symbols:
        print("ПЛОХО: не повторён ни один символ — проверка не состоялась")
        print("=" * 78)
        return 1
    if not changed:
        print(f"ХОРОШО: расчёт не изменился — все {len(r.symbols)} карточек совпали "
              f"построчно с сохранёнными.")
    else:
        print(f"ВНИМАНИЕ: расчёт изменился у {len(changed)} символов из {len(r.symbols)}.")
        print("Ниже — что было и что стало. Строки со знаком «-» были, со знаком «+» стали.")
        if show_diff:
            for s in changed:
                print()
                print(s.diff, end="")
        else:
            print("(показать разницу: добавьте --diff)")
    print("=" * 78)
    return len(changed) + len(r.skipped)
