"""Processor registry — maps file extensions to processor classes and scans
the source folder for candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .base import Processor
from .models import SourceFile

FilterFn = Callable[[Path, str, str], bool]

# Yielded by ``iter_source_tree``: (absolute path, posix rel, is_dir).
WalkEntry = tuple[Path, str, bool]


def iter_source_tree(source_root: Path, exclusion=None):
    """Shared low-level traversal: every dir + file, deterministic DFS order.

    Excluded directories are pruned during descent (never entered). This is
    the single iteration behind both ``ProcessorRegistry.scan()`` (its
    filtered projection for the pipeline) and ``build_source_index()``
    (TICKET-007) — one iterator, no divergent walks.
    """

    try:
        if not source_root.is_dir():
            return
        stack: list[Path] = sorted(
            (p for p in source_root.iterdir()),
            key=lambda p: p.as_posix().casefold(),
        )
    except OSError:
        return
    # Seed-level prune (top-level excluded dirs never entered)
    if exclusion is not None:
        stack = [
            p
            for p in stack
            if not (
                p.is_dir()
                and exclusion.is_excluded_dir(
                    p.relative_to(source_root).as_posix()
                )
            )
        ]
    while stack:
        path = stack.pop(0)
        try:
            rel = path.relative_to(source_root).as_posix()
        except ValueError:
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                if exclusion is not None and exclusion.is_excluded_dir(rel):
                    continue
                yield (path, rel, True)
                try:
                    children = sorted(
                        (c for c in path.iterdir()),
                        key=lambda c: c.as_posix().casefold(),
                    )
                except OSError:
                    continue
                # prune excluded subdirs before descent
                if exclusion is not None:
                    pruned: list[Path] = []
                    for child in children:
                        try:
                            if child.is_dir() and not child.is_symlink():
                                crel = child.relative_to(source_root).as_posix()
                                if exclusion.is_excluded_dir(crel):
                                    continue
                            pruned.append(child)
                        except OSError:
                            continue
                    children = pruned
                # depth-first, but keep global deterministic order:
                # push children sorted, process in order
                stack = children + stack
                continue
            if path.is_file():
                yield (path, rel, False)
        except OSError:
            continue


class ProcessorRegistry:
    def __init__(self, filter_fn: FilterFn | None = None, exclusion_filter=None) -> None:
        self._processors: dict[str, Processor] = {}
        self._filter_fn = filter_fn
        self._exclusion = exclusion_filter

    def register(self, extension: str, processor_cls: type[Processor]) -> None:
        ext = extension.lower()
        if not ext.startswith("."):
            ext = "." + ext
        self._processors[ext] = processor_cls()

    def processor_for(self, extension: str) -> Processor | None:
        return self._processors.get(extension.lower())

    def registered_extensions(self) -> frozenset[str]:
        return frozenset(self._processors)

    def project(self, index, source_root: Path) -> list[SourceFile]:
        """Project pipeline candidates from a prebuilt ``SourceIndex``.

        TICKET-009 single walk: ``build_source_index()`` performs the only
        filesystem traversal; the candidate list is derived from it with the
        exact same filters/order as :meth:`scan` (registered extensions,
        exclusions, ``filter_fn``). ``scan`` is kept unchanged for the scan
        preview and backward compatibility.
        """

        from .exclusions import ExclusionFilter
        from .source_index import Kind

        excl: ExclusionFilter | None = self._exclusion
        if excl is None and self._filter_fn is not None:
            excl = getattr(self._filter_fn, "exclusion", None)
        candidates: list[SourceFile] = []
        for item in index.items.values():
            if item.kind != Kind.FILE:
                continue
            rel = item.rel_posix
            ext = Path(rel).suffix.lower()
            if ext not in self._processors:
                continue
            if excl is not None and excl.excluded(rel):
                continue
            path = source_root.joinpath(*rel.split("/"))
            if self._filter_fn and not self._filter_fn(path, rel, ext):
                continue
            candidates.append(
                SourceFile(
                    abs_path=path, rel_path=rel, ext=ext, source_id=item.source_id
                )
            )
        return sorted(candidates, key=lambda sf: sf.abs_path.as_posix().casefold())

    def scan(self, source_root: Path) -> list[SourceFile]:
        """Walk the source tree and return registered files.

        Filtered projection of the shared ``iter_source_tree()`` iteration
        for the pipeline: only registered extensions, minus excluded and
        chat-filtered files. Sorting stays deterministic.
        """

        from .exclusions import ExclusionFilter

        candidates: list[SourceFile] = []
        excl: ExclusionFilter | None = self._exclusion
        if excl is None and self._filter_fn is not None:
            excl = getattr(self._filter_fn, "exclusion", None)
        try:
            root_resolved = source_root.resolve()
        except OSError:
            return []
        if not source_root.is_dir():
            return []
        ordered: list[Path] = [
            path for path, _rel, is_dir in iter_source_tree(source_root, excl)
            if not is_dir
        ]
        for path in sorted(ordered, key=lambda p: p.as_posix().casefold()):
            try:
                if not path.is_file():
                    continue
                ext = path.suffix.lower()
                if ext in self._processors:
                    rel = path.relative_to(root_resolved).as_posix() if path.is_absolute() else path.relative_to(source_root).as_posix()
                    try:
                        rel = path.relative_to(source_root).as_posix()
                    except ValueError:
                        continue
                    if excl is not None and excl.excluded(rel):
                        continue
                    if self._filter_fn and not self._filter_fn(path, rel, ext):
                        continue
                    candidates.append(SourceFile(abs_path=path, rel_path=rel, ext=ext))
            except OSError:
                continue
        return candidates