"""Path-overlap guard.

Rejects dangerous layout combinations before any work happens:
    - first == second
    - second is inside first
    - first is inside second

Used for every adjacent stage pair (source/target for the import stage,
target/enriched for the AI stage, source/enriched as a belt-and-braces both).
Refusal is hard: the tool exits rather than trying to "figure it out".
Comparisons use Path.resolve() and case normalization, so the checks behave
correctly on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path


class GuardError(Exception):
    """Raised when a path pair layout is unsafe."""


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _is_within(child_norm: str, parent_norm: str) -> bool:
    """True if child_norm == parent_norm or child lives inside parent_norm."""

    if child_norm == parent_norm:
        return True
    sep = os.sep
    return child_norm.startswith(parent_norm + sep)


def check(first: Path, second: Path) -> None:
    """Validate a stage pair. Raises GuardError on any overlap."""

    a = _normalized(first)
    b = _normalized(second)

    if a == b:
        raise GuardError(
            f"Первый и второй путь совпадают: {first}\n"
            "Запуск запрещён — этап уничтожил бы собственный вход."
        )
    if _is_within(b, a):
        raise GuardError(
            f"Второй путь находится внутри первого:\n"
            f"  первый: {first}\n"
            f"  второй: {second}\n"
            "Запуск запрещён — предусмотренные результаты были бы видны как новые входные данные."
        )
    if _is_within(a, b):
        raise GuardError(
            f"Первый путь находится внутри второго:\n"
            f"  первый: {first}\n"
            f"  второй: {second}\n"
            "Запуск запрещён — этап стал бы сканировать собственные результаты."
        )