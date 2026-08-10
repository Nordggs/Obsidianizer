"""Event contract for the core pipeline.

The core emits events through an optional ``on_event`` callback. Interfaces
(CLI, future UI) consume them; the core never knows who is listening and never
picks a presentation for them.

This module is the SINGLE source of truth for event names and payloads.
``cli.py`` and ``ui.py`` import from here; they never define their own events.

Example for FILE_DONE with index=17, total=120:

- GUI: "✓ Обработан 17 из 120"
- CLI: "[17/120] ✓ filename.md"
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EventType(str, Enum):
    """Lifecycle events emitted by ``pipeline.run``."""

    SCAN_STARTED = "scan_started"
    FILE_STARTED = "file_started"
    LLM_STARTED = "llm_started"
    FILE_DONE = "file_done"
    FILE_SKIPPED = "file_skipped"
    FILE_ERROR = "file_error"
    FINISHED = "finished"


@dataclass(frozen=True)
class Event:
    """A single progress/result notification from the core.

    ``path`` is relative to the source root (or empty for stage events).
    ``index``/``total`` are the 1-based file counters for progress bars.
    ``message`` carries a human-readable detail (e.g. an error description).
    """

    type: EventType
    path: str = ""
    index: int = 0
    total: int = 0
    message: str = ""