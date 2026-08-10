"""Строка состояния Claude Code: ветка, незакоммиченное, леджер, свежесть карты.

Заведена 2026-08-11. Показывает ровно то, что в этом проекте нужно знать ПОСТОЯННО и
что иначе приходится спрашивать командой:

  * ветка и число незакоммиченных файлов — сколько работы висит непроверенной;
  * сигналы леджера и сколько из них БЕЗ ИСХОДА — знаменатель, о котором проект
    забывал (2026-08-10: «средний R» считался по трети журнала);
  * возраст карты уровней — карта живёт между прогонами (стр. 25, 31), и уровень,
    посчитанный неделю назад, выглядит в карточке так же, как посчитанный сегодня.

⚠ БЫСТРОДЕЙСТВИЕ — условие, а не пожелание: строка перерисовывается постоянно. Поэтому
здесь ЧИСТЫЙ stdlib и соединение с леджером ТОЛЬКО НА ЧТЕНИЕ, без импорта `hunter`
(он тянет polars и pydantic — сотни миллисекунд). Любая беда молчит и печатает то, что
успела: строка состояния не имеет права ронять сессию или задерживать её.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

LEDGER = Path("data/ledger.sqlite3")


def _git(*args: str) -> str:
    try:
        out = subprocess.run(("git", *args), capture_output=True, text=True,
                             encoding="utf-8", timeout=2)
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _ledger() -> str:
    """Сигналы, доля без исхода и возраст карты. Пусто — базы нет, и это не ошибка."""
    if not LEDGER.exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{LEDGER.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            sig = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            out = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            seen = conn.execute(
                "SELECT MAX(last_seen) FROM levels WHERE state='active'").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return "леджер: не прочитан"
    part = f"леджер: {sig} сигн., без исхода {sig - out}"
    if seen:
        # ⚠ ЛОКАЛЬНЫЕ часы, вопреки §6, и вот почему это законно ИМЕННО ЗДЕСЬ.
        # `clock.now_ms()` требует сведения с биржей: в этом процессе его нет и быть не
        # может — строка состояния в сеть не ходит. При этом она ничего не РЕШАЕТ:
        # ни один вывод отсюда не попадает ни в расчёт, ни в леджер, ни в карточку.
        # Цена ошибки — неточность подписи «N ч назад» на величину расхождения часов,
        # то есть на секунды при пороге в час. Тот же приём и та же форма пометки, что
        # у `clock.local_ms()`, где локальные часы читаются по существу.
        hours = (time.time() * 1000 - float(seen)) / 3_600_000  # noqa: TID251
        part += f" · карта {hours:.0f} ч назад" if hours >= 1 else " · карта свежая"
    return part


def main() -> int:
    # Claude Code подаёт на вход JSON сессии. Он здесь не нужен, но прочитать его надо:
    # иначе процесс, пишущий в закрытый канал, получит ошибку.
    try:
        json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        pass

    parts: list[str] = []
    if branch := _git("rev-parse", "--abbrev-ref", "HEAD"):
        dirty = len([ln for ln in _git("status", "--porcelain").splitlines() if ln])
        parts.append(f"{branch}" + (f" +{dirty}" if dirty else " чисто"))
    if ahead := _git("rev-list", "--count", "origin/main..HEAD"):
        if ahead not in ("", "0"):
            parts.append(f"не отправлено: {ahead}")
    if led := _ledger():
        parts.append(led)
    print(" │ ".join(parts) or "hunter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
