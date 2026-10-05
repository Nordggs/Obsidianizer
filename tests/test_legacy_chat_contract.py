"""TICKET-009 — Legacy Regression Contract (semantics + idempotency).

The old Chat Processing behaviour must survive the Enrichment migration. This
is a *semantic* contract over a frozen representative fixture — not a second
pipeline and not a byte-for-byte comparison: it pins the observable outcomes
(chat detection, content preservation, enrichment data, a written
representation, and a stable Source) plus idempotency (a second run changes
nothing and creates no duplicates).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from obsidianizer.chat_detect import is_chat_export, make_chat_filter
from obsidianizer.config import Settings
from obsidianizer.md_processor import MdProcessor
from obsidianizer.manifest import read_manifest
from obsidianizer.nav_index import frontmatter_of
from obsidianizer.pipeline import run as run_pipeline, source_hash_of
from obsidianizer.registry import ProcessorRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "md"
NS = "legacy-ns"


def _settings(source: Path, target: Path) -> Settings:
    s = Settings()
    s.source = source
    s.target = target
    s.enriched = target.parent / "enriched"
    s.llm_enabled = False
    s.source_namespace = NS
    return s


def _run(source: Path, target: Path):
    reg = ProcessorRegistry(filter_fn=make_chat_filter(chat_only=True))
    reg.register(".md", MdProcessor)
    return run_pipeline(reg, _settings(source, target))


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    target = tmp_path / "processed"
    shutil.copytree(FIXTURES, source)
    return source, target


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): source_hash_of(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_legacy_semantics_preserved(tmp_path):
    source, target = _prepare(tmp_path)
    report = _run(source, target)

    assert report.failed == []
    assert report.processed == 2

    for rel, original in _snapshot(source).items():
        if not rel.endswith(".md"):
            continue
        note = target / rel
        assert note.is_file(), f"representation missing for {rel}"

        # chat detection still holds on the produced note's verbatim body
        text = note.read_text(encoding="utf-8")
        assert is_chat_export(text), f"chat markers lost for {rel}"

        meta = frontmatter_of(note)
        # ownership + identity + enrichment data are all present
        assert meta.get("source_hash") == original
        assert meta.get("source_id")
        assert meta.get("doc_type") == "chat"
        assert "title" in meta and "messages" in meta

    # a navigation representation and an ownership manifest were written
    assert (target / "_index.md").is_file()
    manifest = read_manifest(target)
    assert "deepseek/deepseek_09_nextcloudobsidian_d78923c8.md" in manifest


def test_legacy_idempotent_second_run(tmp_path):
    source, target = _prepare(tmp_path)
    first = _run(source, target)
    assert first.processed == 2

    files_before = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    notes_before = {
        p.relative_to(target).as_posix(): p.read_bytes()
        for p in target.rglob("*.md")
    }

    second = _run(source, target)

    assert second.processed == 0
    assert second.skipped == 2  # nothing re-written, no duplicates
    files_after = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    assert files_after == files_before  # no duplicates, no new files, no loss
    for rel, blob in notes_before.items():
        assert (target / rel).read_bytes() == blob  # no corruption


def test_legacy_source_immutable(tmp_path):
    source, target = _prepare(tmp_path)
    before = _snapshot(source)

    _run(source, target)

    assert _snapshot(source) == before  # Source is read-only for enrichment
