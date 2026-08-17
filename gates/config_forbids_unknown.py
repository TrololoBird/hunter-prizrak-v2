"""ГЕЙТ: загрузчики TOML отвергают неизвестный ключ. FOUNDATION.md §10.1, §4.3.

Дефект, который это держит, случался ДВАЖДЫ: `bars_per_timeframe` (удалён 2026-08-11)
и `admission_required_bars` (удалён 2026-08-17) лежали в конфигурации мёртвыми — их не
читала ни одна строка кода, а докстроки ссылались на них как на действующие. Зеркальная
половина того же дефекта — опечатка в имени ключа: загрузчик, молча пропускающий
неизвестное, подставляет умолчание вместо значения оператора, и оператор об этом не
узнаёт никогда. Это то же правило, что `extra="forbid"` у моделей (гейт
models_forbid_extra), перенесённое на TOML. Разбор: docs/audit/frozen-rules-2026-08-17.md §4.

Проверка — ПОДСАЖЕННЫМ НАРУШЕНИЕМ, а не чтением кода: каждый загрузчик получает файл
с одним лишним ключом и обязан упасть `ValueError`, называющим ключ по имени. Рядом
контроль в обратную сторону: боевые файлы `config/*.toml` обязаны читаться без отказа —
гейт, который валит и правильное, ничего не проверяет.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from hunter.config import load_bot_config, load_universe

PLANTED_KEY = "kluch_kotorogo_net"


def planted_rejected(name: str, loader: Callable[[Path], Any], text: str) -> bool:
    """Файл с лишним ключом обязан дать ValueError, называющий ключ."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "planted.toml"
        p.write_text(text, encoding="utf-8")
        try:
            loader(p)
        except ValueError as e:
            if PLANTED_KEY in str(e):
                print(f"ok      {name}: подсаженный ключ отвергнут с именем")
                return True
            print(f"ПРОВАЛ  {name}: отказ есть, но ключ {PLANTED_KEY!r} не назван: {e}")
            return False
        print(f"ПРОВАЛ  {name}: файл с ключом {PLANTED_KEY!r} прочитан молча")
        return False


def live_readable(name: str, loader: Callable[[Path], Any], path: Path) -> bool:
    """Боевой файл обязан читаться: гейт не должен валить правильное."""
    try:
        loader(path)
    except Exception as e:  # любой отказ на боевом файле это провал гейта
        print(f"ПРОВАЛ  {name}: боевой {path} не читается: {type(e).__name__} {e}")
        return False
    print(f"ok      {name}: боевой {path} читается")
    return True


def main() -> int:
    checks = [
        planted_rejected(
            "universe", load_universe,
            f'[universe]\nsymbols=["BTC/USDT:USDT"]\ntimeframes=["5m"]\n{PLANTED_KEY}=1\n'),
        planted_rejected("bot", load_bot_config, f"[bot]\n{PLANTED_KEY}=1\n"),
        # Лишняя ТАБЛИЦА — вторая половина того же дефекта (rules-auditor 2026-08-17):
        # вернувшийся [bars] или опечатанный [universo] пролежал бы мёртвым молча.
        planted_rejected(
            "universe-секция", load_universe,
            f'[universe]\nsymbols=["BTC/USDT:USDT"]\ntimeframes=["5m"]\n'
            f"[{PLANTED_KEY}]\nx=1\n"),
        planted_rejected("bot-секция", load_bot_config,
                         f"[bot]\n[{PLANTED_KEY}]\nx=1\n"),
        live_readable("universe", load_universe, Path("config/universe.toml")),
        live_readable("bot", load_bot_config, Path("config/bot.toml")),
    ]
    print(f"проверок: {len(checks)}, провалов: {checks.count(False)}")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
