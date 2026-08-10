"""Path-overlap guard.

Rejects dangerous source/target combinations before any work happens:
    - source == target
    - target is inside source
    - source is inside target

Refusal is hard: the tool exits rather than trying to "figure it out".
Comparisons use Path.resolve() and case normalization, so the checks behave
correctly on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path


class GuardError(Exception):
    """Raised when the source/target layout is unsafe."""


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _is_within(child_norm: str, parent_norm: str) -> bool:
    """True if child_norm == parent_norm or child lives inside parent_norm."""

    if child_norm == parent_norm:
        return True
    sep = os.sep
    return child_norm.startswith(parent_norm + sep)


def check(source: Path, target: Path) -> None:
    """Validate source/target. Raises GuardError on any overlap."""

    src = _normalized(source)
    tgt = _normalized(target)

    if src == tgt:
        raise GuardError(
            f"source и target совпадают: {source}\n"
            "Запуск запрещён — Obsidianizer уничтожил бы собственный вход."
        )
    if _is_within(tgt, src):
        raise GuardError(
            f"target находится внутри source:\n"
            f"  source: {source}\n"
            f"  target: {target}\n"
            "Запуск запрещён — предусмотренные результаты были бы видны как новые входные данные."
        )
    if _is_within(src, tgt):
        raise GuardError(
            f"source находится внутри target:\n"
            f"  source: {source}\n"
            f"  target: {target}\n"
            "Запуск запрещён — обработчик стал бы сканировать собственные результаты."
        )