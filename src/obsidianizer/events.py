"""Event contract for the core pipeline.

The core emits events through an optional ``on_event`` callback. Interfaces
(CLI, future UI) consume them; the core never knows who is listening and never
picks a presentation for them.

This module is the SINGLE source of truth for event names and payloads.
``cli.py`` and ``ui.py`` import from here; they never define their own events.

Example for FILE_DONE with index=17, total=120:

- GUI: "✓ Обработан 17 из 120"
- CLI: "[17/120] ✓ filename.md"

The ``AI_*`` family is emitted by the optional second stage
(``postprocess.enrich``) which re-processes already-produced notes: the same
file counters, with the same semantics as the ``FILE_*``/``SCAN_*`` events.

The ``TOPIC_*`` family is emitted by the topic builder (``topics.create_topic``)
when several chats are merged into a single topic note, and by
``topics.group_all`` for auto-grouping the whole collection (``TOPIC_MAP_STARTED``
opens the clustering pass, then one ``TOPIC_FILE_*`` per created topic).
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
    # AI post-processing stage (second pass over already-produced notes)
    AI_SCAN_STARTED = "ai_scan_started"
    AI_FILE_STARTED = "ai_file_started"
    AI_FILE_DONE = "ai_file_done"
    AI_FILE_SKIPPED = "ai_file_skipped"
    AI_FILE_ERROR = "ai_file_error"
    AI_FINISHED = "ai_finished"
    # Topic builder (group analysis of several chats into one topic note)
    TOPIC_SCAN_STARTED = "topic_scan_started"
    TOPIC_MAP_STARTED = "topic_map_started"
    TOPIC_FILE_STARTED = "topic_file_started"
    TOPIC_FILE_DONE = "topic_file_done"
    TOPIC_FILE_ERROR = "topic_file_error"
    TOPIC_FINISHED = "topic_finished"
    # Topic lifecycle (per-topic management, no per-file progress)
    TOPIC_UPDATED = "topic_updated"
    TOPIC_RENAMED = "topic_renamed"
    TOPIC_DELETED = "topic_deleted"
    # AI assistant chat (interactive Q&A, no per-file progress)
    CHAT_REPLY = "chat_reply"
    CHAT_ERROR = "chat_error"
    # Chat search results: found candidate chats for the current question
    CHAT_FOUND = "chat_found"
    # Folder Obsidianizer (read-only scan → Markdown cards per folder)
    OBS_SCAN_STARTED = "obs_scan_started"
    OBS_FOLDER_DONE = "obs_folder_done"
    OBS_FINISHED = "obs_finished"
    OBS_ERROR = "obs_error"
    # AI folder review (tab 3): LLM overview → <folder>_обзор.md per folder
    REVIEW_STARTED = "review_started"
    REVIEW_FOLDER_DONE = "review_folder_done"
    REVIEW_FINISHED = "review_finished"
    REVIEW_ERROR = "review_error"


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