"""Пропуск как значение. FOUNDATION.md §4.3."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NotReady:
    """Данных нет. Причина обязательна — §4.3 запрещает молчаливый пропуск."""

    reason: str

    def __str__(self) -> str:
        return f"не готово: {self.reason}"


def value_or_raise[V](x: V | NotReady, context: str) -> V:
    """Достать значение там, где отсутствие недопустимо."""
    if isinstance(x, NotReady):
        raise ValueError(f"{context}: {x.reason}")
    return x
