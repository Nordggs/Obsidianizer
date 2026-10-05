"""Content-based chat detection for AIChat Exporter exports.

Determines whether a Markdown file is an exported AI chat by checking for
structural markers unique to the AIChat Exporter format.  The check is
content-only (no path dependency) so files survive move/rename.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

# Markers from the AIChat Exporter format (confirmed via fixtures).
_RE_SCHEMA = re.compile(r"<!--\s*schema_version:\s*\d+\s*-->")
_RE_SOURCE = re.compile(r"^\*\*Источник:\*\*", re.MULTILINE)
_RE_STABLE_ID = re.compile(r"^\*\*Stable ID:\*\*", re.MULTILINE)
_RE_HASH_COMMENT = re.compile(r"<!--\s*hash:\s*\w+\s+title:\s*\S+\s+chat_order:\s*\d+\s*-->")
_RE_MESSAGE = re.compile(r"^####\s+[👤🤖]", re.MULTILINE)
_RE_TOPIC_TYPE = re.compile(r"^---\s*\n.*?type:\s*topic\b", re.DOTALL)

FilterFn = Callable[[Path, str, str], bool]


def read_head(path: Path, max_lines: int = 100) -> str:
    """Read the first *max_lines* of *path* (UTF-8, best-effort)."""

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines: list[str] = []
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                lines.append(line)
            return "".join(lines)
    except OSError:
        return ""


def is_chat_export(text: str) -> bool:
    """Return True when *text* looks like an AIChat Exporter chat export.

    Detection requires at least two distinct structural markers.  No single
    marker (including ``schema_version``) is sufficient on its own.
    """

    # Obsidianizer topic files have ``type: topic`` in frontmatter — not chats.
    if _RE_TOPIC_TYPE.search(text):
        return False

    score = 0
    if _RE_SCHEMA.search(text):
        score += 1
    if _RE_SOURCE.search(text):
        score += 1
    if _RE_STABLE_ID.search(text):
        score += 1
    if _RE_HASH_COMMENT.search(text):
        score += 1
    if _RE_MESSAGE.search(text):
        score += 1
    return score >= 2


def detect_chat_service(text: str) -> str | None:
    """Extract the chat service name from *text*, or ``None``."""

    m = re.search(r"^\*\*Источник:\*\*\s*(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip().lower()
    return None


def make_chat_filter(
    *,
    chat_only: bool,
    selection: set[str] | None = None,
    selection_provided: bool = False,
) -> FilterFn:
    """Return a ``filter_fn`` for :class:`ProcessorRegistry`.

    Parameters match the ordering described in PLAN §5.4:
      1. ``ext != ".md"`` → keep (other types are not our concern)
      2. ``selection_provided`` → ``rel_path in selection`` (manual override)
      3. ``not chat_only`` → keep everything (legacy / advanced mode)
      4. ``chat_only`` → ``is_chat_export(head)``
    """

    sel = selection if selection is not None else set()

    def _filter(abs_path: Path, rel_path: str, ext: str) -> bool:
        if ext != ".md":
            return True
        if selection_provided:
            return rel_path in sel
        if not chat_only:
            return True
        head = read_head(abs_path)
        return is_chat_export(head)

    return _filter
