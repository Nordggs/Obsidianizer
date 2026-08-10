"""Manifest + prune tests — ownership journal is the only deletion basis."""

import json
from pathlib import Path

from obsidianizer.manifest import (
    MANIFEST_NAME,
    prune,
    read_manifest,
    write_manifest,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_missing_manifest_reads_empty(tmp_path):
    assert read_manifest(tmp_path) == frozenset()


def test_write_read_roundtrip(tmp_path):
    write_manifest(tmp_path, {"a.md", "media/x.png"})
    assert read_manifest(tmp_path) == frozenset({"a.md", "media/x.png"})


def test_write_manifest_is_atomic(tmp_path):
    write_manifest(tmp_path, {"a.md"})
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    data = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data["generator"] == "obsidianizer"
    assert data["created"] == ["a.md"]


def test_prune_without_manifest_deletes_nothing(tmp_path):
    foreign = tmp_path / "my_own_note.md"
    _touch(foreign)
    removed = prune(frozenset(), frozenset(), tmp_path)
    assert removed == []
    assert foreign.exists()


def test_prune_removes_only_old_minus_current(tmp_path):
    _touch(tmp_path / "stale.md")
    _touch(tmp_path / "current.md")
    foreign = tmp_path / "my_own_note.md"
    _touch(foreign)

    removed = prune(
        frozenset({"stale.md", "current.md"}),
        frozenset({"current.md"}),
        tmp_path,
    )
    assert removed == ["stale.md"]
    assert (tmp_path / "stale.md").exists() is False
    assert (tmp_path / "current.md").exists()
    assert foreign.exists()


def test_prune_ignores_paths_escaping_target(tmp_path):
    outside = tmp_path / ".." / "outside.tmp"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("x", encoding="utf-8")
    removed = prune(frozenset({"../../outside.tmp"}), frozenset(), tmp_path)
    assert removed == []
    assert outside.exists()


def test_prune_leaves_media_not_in_manifest(tmp_path):
    _touch(tmp_path / "stale.md")
    _touch(tmp_path / "candidate.png")
    removed = prune(frozenset({"stale.md"}), frozenset(), tmp_path)
    assert removed == ["stale.md"]
    assert (tmp_path / "candidate.png").exists()