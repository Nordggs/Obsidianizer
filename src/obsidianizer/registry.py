"""Processor registry — maps file extensions to processor classes and scans
the source folder for candidates."""

from __future__ import annotations

from pathlib import Path

from .base import Processor
from .models import SourceFile


class ProcessorRegistry:
    def __init__(self) -> None:
        self._processors: dict[str, Processor] = {}

    def register(self, extension: str, processor_cls: type[Processor]) -> None:
        ext = extension.lower()
        if not ext.startswith("."):
            ext = "." + ext
        self._processors[ext] = processor_cls()

    def processor_for(self, extension: str) -> Processor | None:
        return self._processors.get(extension.lower())

    def registered_extensions(self) -> frozenset[str]:
        return frozenset(self._processors)

    def scan(self, source_root: Path) -> list[SourceFile]:
        """Walk the source tree and return registered files."""

        candidates: list[SourceFile] = []
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                ext = path.suffix.lower()
                if ext in self._processors:
                    rel = path.relative_to(source_root).as_posix()
                    candidates.append(SourceFile(abs_path=path, rel_path=rel, ext=ext))
        return candidates