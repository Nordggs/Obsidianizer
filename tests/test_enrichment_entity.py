"""TICKET-008A: Enrichment Entity form, links, additivity, wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidianizer.enrichment import (
    EnrichmentEntity,
    build_entity,
    is_linked,
    update_entity,
)


def test_form_requires_explicit_source_id_no_auto_id() -> None:
    with pytest.raises(ValueError):
        build_entity("")
    with pytest.raises(ValueError):
        build_entity("   ")
    e = build_entity("ns-id-1", data={"summary": "s"})
    assert isinstance(e, EnrichmentEntity)
    assert e.source_id == "ns-id-1"
    assert e.data == {"summary": "s"}
    assert e.provenance == {} and e.version == {}
    assert e.ai_metadata == {} and e.relations == []


def test_field_names_match_contract() -> None:
    e = build_entity(
        "x",
        data={"summary": "s", "topics": ["t"]},
        provenance={"producer": "pipeline"},
        version={"ai_hash": "h"},
        ai_metadata={"model": "m"},
        relations=["other-id"],
    )
    assert e.data["topics"] == ["t"]
    assert e.provenance["producer"] == "pipeline"
    assert e.version["ai_hash"] == "h"
    assert e.ai_metadata["model"] == "m"
    assert e.relations == ["other-id"]


def test_update_entity_pure_merge_source_id_stable() -> None:
    e = build_entity("x", data={"a": 1}, relations=["r1"])
    u = update_entity(e, data={"b": 2}, relations=["r2", "r1"])
    assert u.source_id == "x"
    assert u.data == {"a": 1, "b": 2}
    assert u.relations == ["r1", "r2"]
    assert e.data == {"a": 1}  # frozen original untouched


def test_is_linked_predicate() -> None:
    assert is_linked({"source_id": "abc"}) is True
    assert is_linked({}) is False
    assert is_linked({"source_id": ""}) is False


def test_md_body_is_not_entity() -> None:
    # The entity layer never parses note bodies: only explicit constructors
    # and frontmatter links exist. A body mentioning source_id creates nothing.
    assert is_linked({}) is False
    with pytest.raises(ValueError):
        build_entity("", data={"body": "source_id: forged"})


# ── build_frontmatter priority ───────────────────────────────────────────


def test_frontmatter_priority_meta_kept_arg_none_no_clobber() -> None:
    from obsidianizer.enrich import build_frontmatter

    out = build_frontmatter({"source_id": "X"}, "s", [], "h")
    assert "source_id: X" in out


def test_frontmatter_arg_written_only_when_not_none() -> None:
    from obsidianizer.enrich import build_frontmatter

    assert "source_id: Y" in build_frontmatter({}, "s", [], "h", source_id="Y")
    assert "source_id" not in build_frontmatter({}, "s", [], "h")
    # fresh argument wins over a stale meta value; None never clobbers
    assert "source_id: Y" in build_frontmatter(
        {"source_id": "X"}, "s", [], "h", source_id="Y"
    )


def test_frontmatter_legacy_calls_unchanged() -> None:
    from obsidianizer.enrich import build_frontmatter

    out = build_frontmatter({"title": "t"}, "", [], "h")
    assert "source_hash: h" in out
    assert "source_id" not in out


# ── wiring ───────────────────────────────────────────────────────────────


CHAT_MD = (
    "<!-- schema_version: 1 -->\n**Источник:** test\n**Stable ID:**\nabc123\n"
    "<!-- hash: a1b2c3 title: t chat_order: 0 -->\n#### 👤 Вы\nпривет\n"
)


def _write(path: Path, text: str = CHAT_MD) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_pipeline_frontmatter_contains_source_id(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.registry import ProcessorRegistry

    src = tmp_path / "src"
    _write(src / "a.md")
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    report = run_pipeline(reg, s)
    assert report.failed == []
    assert report.source_index is not None
    expected = report.source_index.get_by_path("a.md").source_id
    text = (s.target / "a.md").read_text(encoding="utf-8")
    assert f"source_id: {expected}" in text
    assert "source_hash:" in text  # old keys beside the new link


def test_pipeline_old_notes_not_rewritten_version_stable(tmp_path: Path) -> None:
    from obsidianizer.config import PROCESS_VERSION, Settings
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.registry import ProcessorRegistry

    assert PROCESS_VERSION == 3  # 008A must not bump the format version
    src = tmp_path / "src"
    _write(src / "a.md")
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    first = run_pipeline(reg, s)
    assert first.processed == 1
    second = run_pipeline(reg, s)
    assert second.processed == 0 and second.skipped == 1  # incremental, no rewrite


def test_postprocess_carries_source_id(tmp_path: Path) -> None:
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter
    from obsidianizer.postprocess import enrich as enrich_fn

    class _LLM:
        def analyze(self, body):
            return {"summary": "s", "tags": [], "topic": "t", "type": "x"}

    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    meta = {"title": "a", "service": "t", "messages": {"total": 1}}
    atomic_write(
        processed / "a.md",
        build_frontmatter(meta, "", [], "h-a", source_id="SID-1") + "\n"
        + build_card(meta, "", []) + "\n---\nbody\n",
    )
    report = enrich_fn(processed, enriched, _LLM())
    assert report.failed == []
    text = (enriched / "a.md").read_text(encoding="utf-8")
    assert "source_id: SID-1" in text  # carried over, not clobbered by None
    assert "ai_hash: h-a" in text  # old ownership key still works


def test_search_index_reads_source_id(tmp_path: Path) -> None:
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter
    from obsidianizer.search import ChatIndex

    target = tmp_path / "processed"
    meta = {"title": "a", "service": "t", "messages": {"total": 1}}
    atomic_write(
        target / "a.md",
        build_frontmatter(meta, "", [], "h-a", source_id="SID-9") + "\n"
        + build_card(meta, "", []) + "\n---\nbody text here\n",
    )
    idx = ChatIndex(target, tmp_path / "idx", llm=None, embed_model="")
    stats = idx.refresh()
    assert stats["added"] == 1
    assert idx._notes[0].get("source_id") == "SID-9"


def test_search_schema_v3_identity(tmp_path: Path) -> None:
    """TICKET-009: search schema bumped to v3; identity is ``source_id``.

    Supersedes the 008A "no schema bump" assertion: the migration now keys
    the ChatIndex by ``source_id`` and skips notes without one (no rel
    fallback — backfill is the sanctioned path)."""

    import json

    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter
    from obsidianizer.search import ChatIndex, INDEX_VERSION

    target = tmp_path / "processed"
    meta = {"title": "a", "service": "t", "messages": {"total": 1}}
    atomic_write(
        target / "a.md",
        build_frontmatter(meta, "", [], "h-a", source_id="SID-A") + "\n"
        + build_card(meta, "", []) + "\n---\nbody text here\n",
    )
    atomic_write(
        target / "b.md",
        build_frontmatter(meta, "", [], "h-b") + "\n"
        + build_card(meta, "", []) + "\n---\nno id here\n",
    )
    assert INDEX_VERSION == 3  # TICKET-009 identity migration
    idx = ChatIndex(target, tmp_path / "idx", llm=None, embed_model="")
    stats = idx.refresh()
    assert stats["added"] == 1
    assert stats["skipped_no_id"] == 1
    data = json.loads((tmp_path / "idx" / "index.json").read_text(encoding="utf-8"))
    assert data["version"] == 3
    assert data["notes"][0]["source_id"] == "SID-A"
    idx2 = ChatIndex(target, tmp_path / "idx", llm=None, embed_model="")
    assert idx2.refresh()["added"] == 0  # reloads fine as v3


def test_source_snapshot_no_writes(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.registry import ProcessorRegistry

    src = tmp_path / "src"
    _write(src / "a.md")
    before = sorted(p.as_posix() for p in src.rglob("*"))
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    run_pipeline(reg, s)
    assert sorted(p.as_posix() for p in src.rglob("*")) == before
