"""Source Index (TICKET-007) — internal representation of discovered Source objects.

``SourceIndex`` owns object identity: ``source_id = sha1(namespace + rel)``
(ARCH-DECISION-007-1). Files and folders (including empty ones) are objects
with ``parent_source_id`` links; ``children_of()`` is derived from those
links, never a second source of truth.

Detection (ARCH-DECISION-007-2): ``detect_type()`` + a module-level ext map
close contract-005 Q4 only partially — a full detector registry is a
TICKET-008 task. ``content_hash`` stays ``None`` at Detection: only the cheap
``read_head()`` is allowed here; the full hash is computed by
pipeline/extract when needed, so 007 adds no second full file read.

Excluded rels (TICKET-006) never enter the Index. Nothing is ever written
into Source; folders never trigger auto-Enrichment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class Kind(str, Enum):
    """Closed physical level: what the object *is* on the filesystem."""

    FILE = "file"
    FOLDER = "folder"


# Extensible content layer for ``kind == FILE`` (TABLE is a content property
# for Detection/Enrichment, never a type — the Index must not become a
# format parser).
FILE_TYPE_MAP: dict[str, str] = {
    ".md": "text",
    ".txt": "text",
    ".rst": "text",
    ".adoc": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".svg": "image",
    ".bmp": "image",
    ".ico": "image",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".csv": "spreadsheet",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".7z": "archive",
    ".rar": "archive",
    ".exe": "binary",
    ".dll": "binary",
    ".so": "binary",
    ".bin": "binary",
    ".dwg": "cad",
    ".dxf": "cad",
    ".pdf": "document",
    ".docx": "document",
    ".doc": "document",
    ".rtf": "document",
    ".odt": "document",
}


def register_file_type(
    ext: str,
    file_type: str,
    *,
    predicate: Callable[[Path], bool] | None = None,
    priority: int = 0,
) -> None:
    """Detector registry (TICKET-008D closes contract-005 Q4 architecturally).

    Two forms, fully backward compatible:

    - ``predicate=None`` (default): plain extension registration — the
      pre-008D behaviour, bit-for-bit.
    - ``predicate`` given: a content predicate scoped to *ext*, tried (by
      descending *priority*) only when the extension map has no entry for
      the file's ext. Extension matches always win over content
      predicates (so a predicate registered for a mapped ext is shadowed —
      documented, not silent); ``priority`` orders predicates within one
      unmapped ext.

    Without explicit predicate registrations no new content reads happen
    (the only default content read is the cheap ``.md`` head below) —
    the 007 scan-cost contract holds.
    """

    key = ext.lower()
    if not key.startswith("."):
        key = "." + key
    if predicate is None:
        FILE_TYPE_MAP[key] = file_type
        return
    _CONTENT_DETECTORS[:] = [
        d for d in _CONTENT_DETECTORS if not (d[0] == key and d[1] == file_type)
    ]
    _CONTENT_DETECTORS.append((key, file_type, predicate, priority))


# Content-predicate registrations: (ext, file_type, predicate, priority).
# Tried only for exts absent from FILE_TYPE_MAP (extension wins).
_CONTENT_DETECTORS: list[tuple[str, str, Callable[[Path], bool], int]] = []


def make_source_id(namespace: str, rel_posix: str) -> str:
    """Stable object identity: ``sha1(namespace + "\\0" + rel)``.

    Content/mtime/inode never participate — identity is the filesystem
    truth (path within the Source record). Continuity across renames is a
    TICKET-008 matching task, not part of the id.
    """

    return hashlib.sha1(f"{namespace}\0{rel_posix}".encode("utf-8")).hexdigest()


def detect_type(ext: str, abs_path: Path | None = None) -> str:
    """Content layer for a file via the detector registry.

    Fixed chain: built-in ``.md`` head check (cheap ``read_head()`` only —
    the pre-008D default, bit-for-bit) → extension map → registered
    content predicates for this ext (descending priority) → ``unknown``.
    Unreadable files degrade to the ext-map result, never fail the scan.
    """

    key = (ext or "").lower()
    if key == ".md" and abs_path is not None:
        # Default content detector (kept inline: it predates the registry
        # and defines the bit-for-bit .md behaviour).
        try:
            from .chat_detect import is_chat_export, read_head

            if is_chat_export(read_head(abs_path)):
                return "chat_export"
        except OSError:
            pass
        return "text"
    hit = FILE_TYPE_MAP.get(key)
    if hit is not None:
        return hit
    if abs_path is not None:
        for _ext, file_type, predicate, _priority in sorted(
            (d for d in _CONTENT_DETECTORS if d[0] == key),
            key=lambda d: -d[3],
        ):
            try:
                if predicate(abs_path):
                    return file_type
            except OSError:
                continue
    return "unknown"


@dataclass(frozen=True)
class SourceItem:
    """Immutable view of one Source object. Paths are posix ``str``."""

    source_id: str
    rel_posix: str  # "" for the Source root folder itself
    kind: Kind
    file_type: str  # content layer for FILE; "folder" for FOLDER
    size: int | None = None
    mtime_ns: int | None = None
    content_hash: str | None = None  # always None at Detection (007)
    parent_source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "present"


@dataclass
class SourceIndex:
    """Registry of discovered Source objects."""

    namespace: str
    root: str  # mutable record field: persisted root path (str)
    items: dict[str, SourceItem] = field(default_factory=dict)

    def register(self, item: SourceItem) -> None:
        self.items[item.source_id] = item

    def get_by_path(self, rel_posix: str) -> SourceItem | None:
        rel = (rel_posix or "").replace("\\", "/").strip("/")
        return next(
            (item for item in self.items.values() if item.rel_posix == rel),
            None,
        )

    def children_of(self, source_id: str) -> list[SourceItem]:
        """Direct children, derived from ``parent_source_id`` links."""

        kids = [i for i in self.items.values() if i.parent_source_id == source_id]
        return sorted(kids, key=lambda i: i.rel_posix.casefold())


def _parent_rel(rel: str) -> str | None:
    if not rel:
        return None  # the root folder has no parent
    parent, _, _ = rel.rpartition("/")
    return parent


def build_source_index(
    root: Path, namespace: str, exclusion=None
) -> SourceIndex:
    """One walk over *root*: files + folders (incl. empty) into a SourceIndex.

    Excluded dirs are pruned during descent (never entered); excluded files
    are skipped. ``stat`` supplies size/mtime (metadata, never identity);
    only ``.md`` files get a cheap head read for Detection. Read-only:
    nothing is created inside Source.
    """

    index = SourceIndex(namespace=namespace, root=str(root))
    if not root.is_dir():
        return index

    from .registry import iter_source_tree

    def _excluded(rel: str) -> bool:
        return bool(exclusion is not None and exclusion.excluded(rel))

    root_id = make_source_id(namespace, "")
    index.register(
        SourceItem(
            source_id=root_id,
            rel_posix="",
            kind=Kind.FOLDER,
            file_type="folder",
            parent_source_id=None,
        )
    )

    # Single pass over the shared traversal (same iterator as registry.scan):
    # every dir (incl. empty ones) + every file, excluded dirs pruned.
    for entry, rel, is_dir in iter_source_tree(root, exclusion):
        try:
            if is_dir:
                dir_id = make_source_id(namespace, rel)
                parent = _parent_rel(rel)
                index.register(
                    SourceItem(
                        source_id=dir_id,
                        rel_posix=rel,
                        kind=Kind.FOLDER,
                        file_type="folder",
                        parent_source_id=(
                            make_source_id(namespace, parent)
                            if parent is not None
                            else None
                        ),
                    )
                )
                continue
            if _excluded(rel):
                continue
            try:
                st = entry.stat()
                size: int | None = st.st_size
                mtime_ns: int | None = st.st_mtime_ns
            except OSError:
                size, mtime_ns = None, None
            ext = entry.suffix.lower()
            file_id = make_source_id(namespace, rel)
            parent = _parent_rel(rel)
            metadata: dict[str, Any] = {}
            if ext == ".md":
                try:
                    from .chat_detect import read_head
                    import re

                    head = read_head(entry)
                    m = re.search(
                        r"^\*\*Stable ID:\*\*[\r\n]+\s*(\S+)",
                        head,
                        re.MULTILINE,
                    )
                    if m:
                        metadata["chat_id"] = m.group(1)
                except OSError:
                    pass
            index.register(
                SourceItem(
                    source_id=file_id,
                    rel_posix=rel,
                    kind=Kind.FILE,
                    file_type=detect_type(ext, entry),
                    size=size,
                    mtime_ns=mtime_ns,
                    content_hash=None,
                    parent_source_id=(
                        make_source_id(namespace, parent)
                        if parent is not None
                        else None
                    ),
                    metadata=metadata,
                )
            )
        except OSError:
            continue
    return index
