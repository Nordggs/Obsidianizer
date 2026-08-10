"""Emitter — atomic writes and media copying.

Every note is written atomically (.tmp -> os.replace). Referenced media is
copied per actual reference (never guessed): resolved first against the note's
own directory, then against the source root. Copied media keeps the same
relative layout so `![](media/...)` links stay valid inside the vault.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import ProcessedFile


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_note(target_root: Path, processed: ProcessedFile, final_md: str) -> None:
    atomic_write(target_root / processed.out_rel_path, final_md)


def _resolve_media(source_root: Path, note_dir: Path, ref: str) -> Path | None:
    """Find a local media file for a reference. Safe against traversal."""

    from .manifest import _safe_target_path

    for candidate in (note_dir / ref, source_root / ref):
        resolved = _safe_target_path(source_root, str(candidate.relative_to(source_root)))
        if resolved is None:
            continue
        if resolved.is_file():
            return resolved
    return None


def copy_media(
    source_root: Path,
    target_root: Path,
    processed: ProcessedFile,
) -> list[str]:
    """Copy each referenced media file into the target. Returns copied rel paths."""

    copied: list[str] = []
    note_dir = source_root if not processed.source.rel_dir else (
        source_root / processed.source.rel_dir
    )
    for ref in processed.media_refs:
        if _is_remote(ref):
            continue
        src = _resolve_media(source_root, note_dir, ref)
        if src is None:
            continue
        dest_rel = _join_rel(processed.source.rel_dir, ref)
        dest = target_root / dest_rel
        if _escapes(target_root.resolve(), dest.resolve()):
            continue
        try:
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
            copied.append(dest_rel)
        except OSError:
            continue
    return copied


def _is_remote(ref: str) -> bool:
    lowered = ref.lower()
    return lowered.startswith(("http://", "https://", "//", "data:"))


def _join_rel(rel_dir: str, ref: str) -> str:
    if rel_dir:
        return f"{rel_dir}/{ref}"
    return ref


def _escapes(root: Path, candidate: Path) -> bool:
    return root not in candidate.parents and candidate != root