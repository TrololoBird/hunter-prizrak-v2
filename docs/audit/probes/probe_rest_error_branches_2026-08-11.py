"""Ловит ли транспорт ТРЕТЬЮ ветвь дерева исключений ccxt — на всех REST-вызовах.

Повод. Обратная сверка 2026-08-11 (от ccxt к нашему коду, а не наоборот) показала, что
цепочки `except` у пяти REST-мест разошлись: третья ветвь (`OperationFailed` и потомки)
стояла ровно в том методе, где её нашёл живой зонд, — то есть починено было МЕСТО
ПАДЕНИЯ, а не КЛАСС дефекта.

Дерево ccxt 4.5.71: под `BaseError` три ветви — `ExchangeError`, `OperationFailed`,
`UnsubscribeError`, — и `NetworkError` лежит ПОД `OperationFailed`, а не рядом с ним.
Значит пара `except NetworkError` + `except ExchangeError` пропускает `OperationFailed`,
`BadResponse`, `NullResponse`, `CancelPending`, `UnsubscribeError`.

ЧТО МЕРЯЕТСЯ. Каждому REST-методу транспорта подсовывается исключение каждого класса, и
проверяется, что метод вернул НАЗВАННЫЙ отказ, а не бросил.

КОНТРОЛЬ (обязателен по CLAUDE.md), и он двойной:

  1. `СТАРАЯ ЦЕПОЧКА` — та же таблица, прогнанная через заведомо прежний обработчик
     (`except NetworkError` + `except ExchangeError`). Она ОБЯЗАНА провалиться на третьей
     ветви. Если она проходит всё — прибор не различает старое и новое, и «починено»
     ничего не значит;
  2. `УСПЕХ` — вызов без исключения обязан вернуть данные и увеличить счётчик веса.
     Без этого «все отказы поймались» неотличимо от «метод всегда возвращает отказ».

⚠ Достижимость третьей ветви на публичных эндпоинтах — отдельный вопрос, и он решён не
здесь: карта `describe()['exceptions']` самой ccxt для binanceusdm отображает в неё 57
кодов Binance, среди них `-1000`, `-1001`, `-1006`, `-1010` и `linear/-1008` («Server is
currently overloaded»). Код `-1000` уже наблюдался живым зондом на публичном aggTrades.

Сети не требует: биржа подменяется целиком.

Воспроизведение:
    uv run python docs/audit/probes/probe_rest_error_branches_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import ccxt

from hunter.exchange import Exchange
from hunter.models import NotReady

BRANCHES: tuple[tuple[str, type[Exception]], ...] = (
    ("1 сеть", ccxt.NetworkError),
    ("1 лимит", ccxt.RateLimitExceeded),
    ("2 биржа", ccxt.ExchangeError),
    ("2 запрос", ccxt.BadRequest),
    ("3 OperationFailed", ccxt.OperationFailed),
    ("3 BadResponse", ccxt.BadResponse),
    ("3 NullResponse", ccxt.NullResponse),
    ("3 CancelPending", ccxt.CancelPending),
    ("отдельная UnsubscribeError", ccxt.UnsubscribeError),
)

PAGE = [{"price": "1", "amount": "1", "timestamp": 1, "id": "1"}]
BAR = [[0, 1.0, 1.0, 1.0, 1.0, 1.0]]


class Boom:
    """Подставная биржа: каждый вызов бросает заданный класс либо отдаёт данные."""

    def __init__(self, exc: type[Exception] | None) -> None:
        self.exc = exc
        self.last_response_headers: dict[str, str] = {"x-mbx-used-weight-1m": "7"}
        self.id = "проба"
        self.markets: dict[str, Any] = {}

    def _go(self, ok: Any) -> Any:
        if self.exc is not None:
            raise self.exc("подставной отказ")
        return ok

    async def fetch_ohlcv(self, *a: Any, **k: Any) -> Any:
        return self._go(BAR)

    async def fetch_trades(self, *a: Any, **k: Any) -> Any:
        return self._go(PAGE)


async def old_chain(call: Any) -> Any:
    """ЗАВЕДОМО ПРЕЖНИЙ обработчик — контроль №1. Третью ветвь пропускает."""
    try:
        return await call()
    except (ccxt.RateLimitExceeded, ccxt.DDoSProtection):
        return NotReady(reason="лимит")
    except ccxt.NetworkError:
        return NotReady(reason="сеть")
    except ccxt.ExchangeError:
        return NotReady(reason="биржа")


def probe(exc: type[Exception] | None) -> Exchange:
    ex = Exchange.__new__(Exchange)
    Exchange.__init__(ex)
    ex._ex = Boom(exc)  # type: ignore[assignment]
    return ex


async def main() -> int:
    print("ccxt", ccxt.__version__)
    print(f"{'класс исключения':30} {'наш _rest':>12} {'СТАРАЯ ЦЕПОЧКА':>16}")
    print("-" * 62)
    caught_new = caught_old = 0
    for label, exc in BRANCHES:
        ex = probe(exc)
        got = await ex._rest("проба", "ключ", lambda: ex._ex.fetch_ohlcv())
        new_ok = isinstance(got, NotReady)
        try:
            await old_chain(lambda: probe(exc)._ex.fetch_ohlcv())
            old_ok = True
        except ccxt.BaseError:
            old_ok = False
        caught_new += new_ok
        caught_old += old_ok
        print(f"{label:30} {'поймал' if new_ok else 'УПУСТИЛ':>12} "
              f"{'поймала' if old_ok else 'УПУСТИЛА':>16}")

    print()
    print(f"наш `_rest`:     поймал {caught_new} из {len(BRANCHES)}")
    print(f"старая цепочка:  поймала {caught_old} из {len(BRANCHES)}")

    # --- КОНТРОЛЬ 2: без исключения методы обязаны отдавать ДАННЫЕ ---
    # Берётся `_fetch_ohlcv_guarded`, а не `fetch_closed_ohlcv`: у второго за разбором
    # ветвей стоит ещё и отбор закрытых баров, которому нужны сведённые часы. Ветви
    # правились именно в первом, и меряться должен он.
    ok = probe(None)
    bars = await ok._fetch_ohlcv_guarded("X/Y:Y", "5m", None, 1)
    trades = await ok.fetch_agg_trades_from("X/Y:Y", 0, 1)
    hist = await ok.count_history("X/Y:Y", "5m", cap=1)
    alive = (not isinstance(trades, NotReady) and not isinstance(hist, NotReady)
             and not isinstance(bars, NotReady) and ok.weight_reads > 0)
    print(f"\nКОНТРОЛЬ 2 (успешный вызов): счёт истории {hist}, "
          f"сделок {len(trades) if not isinstance(trades, NotReady) else '—'}, "
          f"баров {len(bars) if not isinstance(bars, NotReady) else '—'}, "
          f"замеров веса {ok.weight_reads}")

    print()
    if caught_new != len(BRANCHES):
        print("⚠ ПРОВАЛ: наш обработчик упускает классы — дефект НЕ закрыт")
        return 1
    if caught_old == len(BRANCHES):
        print("⚠ КОНТРОЛЬ 1 ПРОВАЛЕН: старая цепочка ловит всё, значит прибор не")
        print("  различает прежний код и нынешний. Вывод «починено» НЕ СЛЕДУЕТ.")
        return 1
    if not alive:
        print("⚠ КОНТРОЛЬ 2 ПРОВАЛЕН: метод не отдаёт данные и без исключения —")
        print("  «поймал все отказы» неотличимо от «всегда отказ».")
        return 1
    print(f"Оба контроля пройдены: старая цепочка упускала "
          f"{len(BRANCHES) - caught_old} классов из {len(BRANCHES)}, нынешняя — ноль,")
    print("и при этом успешный вызов по-прежнему возвращает данные.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
