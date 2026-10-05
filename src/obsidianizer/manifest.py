"""Ownership manifest.

The manifest is the journal of every file Obsidianizer created in the target
folder (notes, copied media, the generated index). It is the *only* basis for
deletion: --prune removes just OLD - CURRENT, never foreign files.

Order of operations (fixed by design):
    ... emit ...
    current = collect_created_files()
    old = read_manifest()
    if prune: prune(old, current)
    write_manifest(current)          # last, atomic, only on success

The manifest itself is written atomically (.tmp -> os.replace()) so a crash
never leaves a half-written journal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MANIFEST_NAME = ".obsidianizer-manifest.json"
_SCHEMA = {"generator": "obsidianizer", "version": 1}


def manifest_path(target_root: Path) -> Path:
    return target_root / MANIFEST_NAME


def read_manifest(target_root: Path) -> frozenset[str]:
    """Return the set of relative paths recorded in the old manifest.

    Missing/corrupt manifest -> empty set (safe default).
    """

    path = manifest_path(target_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return frozenset()
    created = data.get("created", []) if isinstance(data, dict) else []
    if not isinstance(created, list):
        return frozenset()
    return frozenset(str(p) for p in created)


def _safe_target_path(target_root: Path, rel: str) -> Path | None:
    """Resolve a manifest-relative path and ensure it stays in the target."""

    candidate = target_root / rel
    try:
        resolved = candidate.resolve()
        root = target_root.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None  # path would escape the target folder
    return resolved


def prune(old: frozenset[str], current: frozenset[str], target_root: Path) -> list[str]:
    """Delete (OLD - CURRENT). Only files listed in the old manifest.

    Returns the relative paths actually removed. Never touches empty states
    or foreign files.
    """

    removed: list[str] = []
    stale = sorted(old - current)
    for rel in stale:
        resolved = _safe_target_path(target_root, rel)
        if resolved is None or resolved in (target_root.resolve(),):
            continue
        try:
            if resolved.is_file():
                resolved.unlink()
                removed.append(rel)
        except OSError:
            continue
    return removed


def write_manifest(target_root: Path, created: set[str]) -> None:
    """Atomically write the current manifest. Call only at the very end."""

    data = {**_SCHEMA, "created": sorted(created)}
    path = manifest_path(target_root)
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass  # Windows quirk: exist_ok may still raise for an existing dir
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)