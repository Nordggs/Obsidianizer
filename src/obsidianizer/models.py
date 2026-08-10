"""Data models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    """A raw file that a registered processor will handle."""

    abs_path: Path
    rel_path: str  # posix path relative to the source root, e.g. "deepseek/foo.md"
    ext: str  # normalized extension, e.g. ".md"

    @property
    def name(self) -> str:
        return self.abs_path.name

    @property
    def rel_dir(self) -> str:
        """Posix directory of the file relative to the source root."""

        idx = self.rel_path.rfind("/")
        return self.rel_path[:idx] if idx >= 0 else ""


@dataclass
class ProcessedFile:
    """Everything the pipeline knows about one output note."""

    source: SourceFile
    meta: dict = field(default_factory=dict)  # flat metadata -> frontmatter keys
    body: str = ""  # original body verbatim
    media_refs: list[str] = field(default_factory=list)
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    source_hash: str = ""
    out_rel_path: str = ""  # posix path inside the target folder