"""Source boundary exclusions (TICKET-006).

User-configurable "do not consider" layer for tabs 2-3: directory names
(at any depth, like the frozen tab-1 precedent) + file-name masks
(fnmatch). Excluded means "not discovered, not processed, not deleted"
(ARCH-DECISION-006-1); retention of derivatives is handled at the
call sites via the old manifest, not here.

Service artifacts are protected by a hard allowlist and are never
excluded, no matter what masks the user configures.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Callable

DEFAULT_EXCLUDE_DIRS: list[str] = [
    ".git",
    "node_modules",
    "venv",
    "__pycache__",
    "build",
    "dist",
    ".obsidian",
]

DEFAULT_EXCLUDE_MASKS: list[str] = ["*.tmp", "*.bak", "*.log"]

# Service artifacts (representation/ownership layer) — never excluded.
_PROTECTED_FILES = {"_index.md", ".obsidianizer-manifest.json"}
_PROTECTED_DIRS = (".obsidianizer", "topics")

FilterFn = Callable[[Path, str, str], bool]


def parse_list(value: str | list[str] | tuple | None) -> list[str]:
    """Normalize a repeatable/CSV CLI or config value into a clean list."""

    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for v in value:
            items.extend(parse_list(v))
        return items
    parts = str(value).split(",")
    return [p.strip() for p in parts if p.strip()]


def _is_protected(rel_posix: str) -> bool:
    rel = (rel_posix or "").replace("\\", "/").strip("/")
    if not rel:
        return False
    name = rel.rsplit("/", 1)[-1]
    if name in _PROTECTED_FILES:
        return True
    first = rel.split("/", 1)[0]
    return first in _PROTECTED_DIRS


class ExclusionFilter:
    """Predicate + descent prune for user exclusions."""

    def __init__(
        self,
        exclude_dirs: list[str] | tuple | str | None = None,
        exclude_masks: list[str] | tuple | str | None = None,
    ) -> None:
        self.exclude_dirs = parse_list(exclude_dirs)
        self.exclude_masks = parse_list(exclude_masks)
        self._dir_set = set(self.exclude_dirs)

    def is_excluded_dir(self, dir_rel: str) -> bool:
        """True when a directory itself is excluded (any segment matches)."""

        rel = (dir_rel or "").replace("\\", "/").strip("/")
        if not rel or _is_protected(rel + "/x"):
            # protected roots (topics/, .obsidianizer/) are never pruned
            if rel.split("/", 1)[0] in _PROTECTED_DIRS:
                return False
        for seg in rel.split("/"):
            if seg in self._dir_set:
                return True
        return False

    def excluded(self, rel_posix: str) -> bool:
        """True when *rel_posix* must not be considered."""

        rel = (rel_posix or "").replace("\\", "/").strip("/")
        if not rel:
            return False
        if _is_protected(rel):
            return False
        segments = rel.split("/")
        # directory part: any segment (except the file name) matches
        for seg in segments[:-1] if len(segments) > 1 else []:
            if seg in self._dir_set:
                return True
        # a directory rel itself (no file part ambiguity): check all segments
        if rel in self._dir_set or segments[-1] in self._dir_set:
            # file or dir whose own name is an excluded dir name — excluded
            # unless it is a protected artifact (checked above)
            if len(segments) == 1:
                return True
            # for paths like "a/.git": the dir segment already matched above;
            # for "a/.git" as a file rel the same rule holds
            if any(s in self._dir_set for s in segments):
                return True
        name = segments[-1]
        for mask in self.exclude_masks:
            if fnmatch.fnmatch(name, mask):
                return True
        return False

    def prune_dirnames(self, parent_rel: str, dirnames: list[str]) -> None:
        """In-place prune for ``os.walk``-style traversal (excluded first)."""

        parent = (parent_rel or "").replace("\\", "/").strip("/")
        keep: list[str] = []
        for name in dirnames:
            rel = f"{parent}/{name}" if parent else name
            if self.is_excluded_dir(rel):
                continue
            keep.append(name)
        dirnames[:] = sorted(keep, key=str.casefold)

    def split(self, rels: list[str]) -> tuple[list[str], list[str]]:
        """Partition *rels* into (kept, excluded) preserving order."""

        kept: list[str] = []
        dropped: list[str] = []
        for rel in rels:
            (dropped if self.excluded(rel) else kept).append(rel)
        return kept, dropped


def build_exclusion(settings) -> ExclusionFilter:
    """Build the exclusion predicate from ``Settings`` (config.yml + CLI)."""

    return ExclusionFilter(
        getattr(settings, "exclude_dirs", None) or [],
        getattr(settings, "exclude_masks", None) or [],
    )


def union_exclude_dirs(settings) -> list[str]:
    """Union of user ``Settings.exclude_dirs`` and canonical system set.

    TICKET-013: tab-1 discovery predicate. System names
    (``DEFAULT_EXCLUDE_DIRS``) can never be removed by editing config.yml.
    """

    seen: list[str] = []
    for name in list(DEFAULT_EXCLUDE_DIRS) + list(
        parse_list(getattr(settings, "exclude_dirs", None) or [])
    ):
        if name not in seen:
            seen.append(name)
    return seen


def build_tab1_exclusion(settings) -> ExclusionFilter:
    """Discovery predicate for Folder Obsidianizer (tab 1).

    Union ``Settings.exclude_dirs ∪ DEFAULT_EXCLUDE_DIRS`` + user masks.
    """

    return ExclusionFilter(
        union_exclude_dirs(settings),
        getattr(settings, "exclude_masks", None) or [],
    )


def exclusion_kind(name: str, user_dirs: list[str] | set[str] | tuple | None = None) -> str:
    """Classify an excluded dir name: system > user > hidden-only.

    ``system`` — name in ``DEFAULT_EXCLUDE_DIRS``; else ``user`` — name only
    in user ``Settings.exclude_dirs`` (explicit action wins over hidden);
    else ``hidden-only`` (dot-name, no rule matched).
    """

    user_set = set(user_dirs or [])
    if name in DEFAULT_EXCLUDE_DIRS:
        return "system"
    if name in user_set:
        return "user"
    return "hidden-only"


def build_filter(
    settings,
    *,
    apply_chat: bool = True,
    selection: set[str] | None = None,
    selection_provided: bool = False,
) -> FilterFn:
    """Single settings-aware filter factory for CLI and GUI.

    Exclusion is always applied; the chat sub-filter only when
    ``apply_chat`` is True (scan preview uses ``apply_chat=False`` so
    non-chats survive — TICKET-004 protection).
    """

    from .chat_detect import make_chat_filter

    excl = build_exclusion(settings)
    chat_fn = make_chat_filter(
        chat_only=bool(getattr(settings, "chat_only", True)) if apply_chat else False,
        selection=selection,
        selection_provided=selection_provided if apply_chat else False,
    )

    def _filter(abs_path: Path, rel_path: str, ext: str) -> bool:
        if excl.excluded(rel_path):
            return False
        return chat_fn(abs_path, rel_path, ext)

    # Expose the predicate for call sites that filter rel-lists/payloads.
    _filter.exclusion = excl  # type: ignore[attr-defined]
    return _filter
