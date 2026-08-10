"""Processor interface — the only file-type-specific code.

The core pipeline never contains type logic; it only talks to this ABC.
New file types are implemented as new Processor subclasses and registered in
the ProcessorRegistry. See docs/processors.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import SourceFile


class Processor(ABC):
    """Interface every processor must implement."""

    extensions: frozenset[str] = frozenset()

    @abstractmethod
    def parse(self, src: SourceFile) -> dict:
        """Extract flat metadata for the frontmatter.

        Raises ValueError on malformed input.
        """

    @abstractmethod
    def body(self, src: SourceFile) -> str:
        """Return the original body verbatim, never transformed."""

    def media_refs(self, src: SourceFile) -> list[str]:
        """Local media references used by this file, relative to its dir."""

        return []