"""ГЕЙТ: только публичные данные. FOUNDATION.md §1 и §5.

§1: «не исполняет ордера, не хранит ключи бирж».
§5: «публичные потоки. Приватные потоки и ключи не нужны».

Ищет по AST вызовы приватных методов CCXT и чтение ключей из окружения.
Охват печатается числом.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# ⚠ `scripts` добавлен 2026-08-07, находка Э-2 прошлого разбора,
# подтверждённая исполнением: три гейта давали 0 на нарушении в `scripts/`
# и 1 на том же файле в `src/`. `production_writer` написан из-за инцидента,
# где в боевой леджер написал именно СКРИПТ, — и до `scripts/` не дотягивался.
ROOTS = ("src", "gates", "scripts")

# Приватные методы CCXT: торговля, счёт, позиции, вывод средств.
PRIVATE_CALLS = {
    "create_order", "createOrder", "create_limit_order", "create_market_order",
    "edit_order", "editOrder", "cancel_order", "cancelOrder", "cancel_all_orders",
    "fetch_balance", "fetchBalance", "fetch_positions", "fetchPositions",
    "fetch_my_trades", "fetchMyTrades", "fetch_orders", "fetchOrders",
    "fetch_open_orders", "fetchOpenOrders", "fetch_closed_orders", "fetchClosedOrders",
    "fetch_order", "fetchOrder", "withdraw", "transfer", "set_leverage", "setLeverage",
    "set_margin_mode", "setMarginMode", "add_margin", "addMargin",
    "fetch_deposit_address", "fetchDepositAddress", "sapi_get", "fapi_private_get",
    "fapiPrivateGetAccount", "watch_orders", "watchOrders", "watch_balance", "watchBalance",
    "watch_my_trades", "watchMyTrades", "watch_positions", "watchPositions",
}

# Ключи в окружении / в конструкторе биржи.
KEY_NAMES = {"apiKey", "api_key", "secret", "apiSecret", "api_secret",
             "privateKey", "private_key", "password", "uid", "walletAddress"}


def scan(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None
            )
            if name in PRIVATE_CALLS:
                out.append((node.lineno, f"приватный вызов CCXT: {name}"))
            for kw in node.keywords:
                if kw.arg in KEY_NAMES:
                    out.append((node.lineno, f"ключ в аргументах: {kw.arg}"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in KEY_NAMES and len(node.value) > 3:
                out.append((node.lineno, f"строка-ключ: {node.value!r}"))
    return out


def main() -> int:
    files = [p for r in ROOTS for p in Path(r).rglob("*.py")]
    findings: list[str] = []
    for p in files:
        if p.name == Path(__file__).name:
            continue  # сам список запретов
        for line, why in scan(p):
            findings.append(f"{p}:{line}: {why}")
    print(f"гейт «только публичные данные»: просмотрено файлов {len(files) - 1}, "
          f"нарушений {len(findings)}")
    for f in findings:
        print(f"  НАРУШЕНИЕ {f}")
    if len(files) <= 1:
        print("ПРОВАЛ: не просмотрено ни одного файла — проверка не состоялась")
        return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
