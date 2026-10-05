"""TICKET-008C: continuity matching, lifecycle, id-prune, visibility."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from obsidianizer.config import Settings
from obsidianizer.emit import atomic_write
from obsidianizer.enrich import build_card, build_frontmatter, compose
from obsidianizer.enrichment import (
    build_entity,
    continue_enrichment,
    is_visible,
    mark_active,
    mark_retired,
    mark_superseded,
    match_continuity,
    retire,
)
from obsidianizer.exclusions import ExclusionFilter
from obsidianizer.source_index import build_source_index, make_source_id

NS = "11111111-1111-4111-8111-111111111111"
CONTENT = "содержимое чата\nвторая строка\n"
OTHER = "совершенно другой текст\n"


def _file_sig(sid, rel, h=None, size=None, mtime=None, kind="file", parent=None):
    return {
        "source_id": sid, "kind": kind, "file_type": None,
        "content_hash": h, "size": size, "mtime_ns": mtime,
        "name": rel.rsplit("/", 1)[-1], "parent_id": parent,
    }


# ── pure matching ────────────────────────────────────────────────────────


def test_match_strong_hash_rename() -> None:
    dead = _file_sig("old-id", "old/a.md", h="H1")
    live = [_file_sig("new-id", "new/a.md", h="H1")]
    assert match_continuity(dead, live) == "new-id"


def test_match_refuses_false_positives() -> None:
    dead = _file_sig("old-id", "old/a.md", h="H1")
    assert match_continuity(dead, []) is None
    assert match_continuity(dead, [_file_sig("x", "b.md", h="H2")]) is None
    # duplicate content: ambiguous, refuse rather than guess
    dupes = [_file_sig("x", "b.md", h="H1"), _file_sig("y", "c.md", h="H1")]
    assert match_continuity(dead, dupes) is None


def test_match_kind_barrier_folder_never_file() -> None:
    dead = _file_sig("old-id", "old", kind="folder", parent="p1")
    live = [{
        "source_id": "kid", "kind": "file", "file_type": None,
        "content_hash": None, "size": None, "mtime_ns": None,
        "name": "old", "parent_id": "p1",
    }]
    assert match_continuity(dead, live) is None


def test_match_folder_parent_name() -> None:
    dead = _file_sig("old-id", "docs", kind="folder", parent="p1")
    live = [_file_sig("new-id", "docs", kind="folder", parent="p1")]
    assert match_continuity(dead, live) == "new-id"
    moved = [_file_sig("new-id", "docs", kind="folder", parent="p2")]
    assert match_continuity(dead, moved) is None


def test_match_weak_size_mtime_name() -> None:
    dead = _file_sig("old-id", "a.bin", size=10, mtime=99)
    live = [_file_sig("new-id", "sub/a.bin", size=10, mtime=99)]
    assert match_continuity(dead, live) == "new-id"
    assert match_continuity(dead, [_file_sig("n", "b.bin", size=10, mtime=99)]) is None


def test_match_never_self() -> None:
    sig = _file_sig("same", "a.md", h="H1")
    assert match_continuity(sig, [sig]) is None


# ── pure lifecycle ops ───────────────────────────────────────────────────


def test_continue_and_retire_pure() -> None:
    e = build_entity("old", data={"summary": "s"}, relations=["r"])
    c = continue_enrichment(e, "new")
    assert c.source_id == "new"
    assert c.data == {"summary": "s"} and c.relations == ["r"]
    assert c.provenance["continued_from"] == "old"
    with pytest.raises(ValueError):
        continue_enrichment(e, "old")
    with pytest.raises(ValueError):
        continue_enrichment(e, "")
    r = retire(c, successor="newer")
    assert r.provenance["lifecycle"] == "retired"
    assert r.provenance["superseded_by"] == "newer"
    assert r.source_id == "new"
    assert retire(e).provenance["lifecycle"] == "retired"


def test_status_text_ops_preserve_rest() -> None:
    text = "---\ntitle: t\nai_hash: h\n---\nbody\n"
    retired = mark_retired(text)
    assert "status: retired" in retired and "ai_hash: h" in retired
    assert "body" in retired
    sup = mark_superseded(text, "nid")
    assert "status: superseded" in sup and "superseded_by: nid" in sup
    back = mark_active(sup)
    assert "status" not in back and "superseded_by" not in back
    assert "ai_hash: h" in back and "body" in back
    assert mark_active(text) == text  # no status lines: byte-identical


def test_is_visible_predicate() -> None:
    assert is_visible("a.md", {}) is True
    assert is_visible("a.md", {"status": "retired"}) is False
    assert is_visible("a.md", {"status": "superseded"}) is False
    assert is_visible("a.md", {"status": "active"}) is True
    excl = ExclusionFilter([], ["*.tmp"])
    assert is_visible("x.tmp", {}, excl) is False


# ── fixtures ─────────────────────────────────────────────────────────────


class _LLM:
    def __init__(self):
        self.calls = 0

    def analyze(self, body):
        self.calls += 1
        return {"summary": "s", "tags": [], "topic": "t", "type": "x"}


def _write(path: Path, text: str) -> Path:
    # newline="\n": keep file bytes identical to the text (no CRLF
    # translation), so sha1-over-bytes matches sha1-over-text in fixtures.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _processed(root: Path, rel: str, content: str = CONTENT, sid=None) -> Path:
    import hashlib as _h

    h = _h.sha1(content.encode()).hexdigest()
    meta = {"title": rel, "service": "t", "messages": {"total": 1},
            "process_version": 3}
    text = build_frontmatter(meta, "", [], h, source_id=sid) + "\n" \
        + build_card(meta, "", []) + "\n---\n" + content
    return _write(root / rel, text)


def _enriched(root: Path, rel: str, content: str = CONTENT, sid=None,
              status: str | None = None) -> Path:
    import hashlib as _h

    h = _h.sha1(content.encode()).hexdigest()
    meta = {"title": rel, "service": "t", "messages": {"total": 1},
            "process_version": 3, "ai_hash": h}
    if status:
        meta["status"] = status
    text = build_frontmatter(meta, "s", [], h, source_id=sid) + "\n" \
        + build_card(meta, "s", []) + "\n---\n" + content
    return _write(root / rel, text)


def _index(src: Path, namespace: str = NS):
    from obsidianizer.exclusions import build_exclusion

    s = Settings()
    return build_source_index(src, namespace, build_exclusion(s))


# ── reconcile ────────────────────────────────────────────────────────────


def test_reconcile_marks_dead_retired_prune_off(tmp_path: Path) -> None:
    from obsidianizer.postprocess import enrich as enrich_fn

    src = tmp_path / "src"
    _write(src / "live.md", OTHER)
    processed, enriched = tmp_path / "p", tmp_path / "e"
    _processed(processed, "live.md", OTHER, sid=make_source_id(NS, "live.md"))
    _processed(processed, "gone.md", CONTENT, sid="dead-id")
    _enriched(enriched, "gone.md", CONTENT, sid="dead-id")
    report = enrich_fn(processed, enriched, _LLM(), prune=False,
                       source_index=_index(src))
    assert report.reconcile_skipped is False
    assert report.retired == ["gone.md"]
    assert (enriched / "gone.md").exists()  # marked, not deleted (no prune)
    assert "status: retired" in (enriched / "gone.md").read_text(encoding="utf-8")


def test_reconcile_transfer_on_rename(tmp_path: Path) -> None:
    from obsidianizer.postprocess import reconcile_enriched

    src = tmp_path / "src"
    _write(src / "new.md", CONTENT)
    enriched = tmp_path / "e"
    _enriched(enriched, "old.md", CONTENT, sid="dead-id")
    rec = reconcile_enriched(enriched, index=_index(src))
    assert rec.skipped is False
    new_id = make_source_id(NS, "new.md")
    assert rec.transfers == [f"old.md -> {new_id}"]
    text = (enriched / "old.md").read_text(encoding="utf-8")
    assert "status: superseded" in text and f"superseded_by: {new_id}" in text
    # idempotent: second pass is a no-op (already superseded to the same id)
    rec2 = reconcile_enriched(enriched, index=_index(src))
    assert rec2.transfers == [] and rec2.marked == [] and rec2.reset == []


def test_reconcile_resets_revived_id(tmp_path: Path) -> None:
    from obsidianizer.postprocess import reconcile_enriched

    src = tmp_path / "src"
    _write(src / "a.md", CONTENT)
    enriched = tmp_path / "e"
    sid = make_source_id(NS, "a.md")
    _enriched(enriched, "a.md", CONTENT, sid=sid, status="retired")
    rec = reconcile_enriched(enriched, index=_index(src))
    assert rec.reset == ["a.md"]
    text = (enriched / "a.md").read_text(encoding="utf-8")
    assert "status" not in text.split("---")[1] and "ai_hash" in text


def test_reconcile_without_index_skips(tmp_path: Path) -> None:
    from obsidianizer.postprocess import reconcile_enriched

    enriched = tmp_path / "e"
    _enriched(enriched, "a.md", CONTENT, sid="x")
    rec = reconcile_enriched(enriched, index=None)
    assert rec.skipped is True
    assert "status" not in (enriched / "a.md").read_text(encoding="utf-8")


def test_reconcile_ignores_topics_and_foreign(tmp_path: Path) -> None:
    from obsidianizer.postprocess import reconcile_enriched

    src = tmp_path / "src"
    _write(src / "a.md", CONTENT)
    enriched = tmp_path / "e"
    topic = enriched / "topics" / "T.md"
    _write(topic, "---\ntype: topic\ntitle: T\ntopic_hash: h\n---\nbody\n")
    foreign = enriched / "foreign.md"
    _write(foreign, "# чужая заметка\n")
    before = (topic.read_bytes(), foreign.read_bytes())
    rec = reconcile_enriched(enriched, index=_index(src))
    assert rec.marked == [] and rec.transfers == []
    assert (topic.read_bytes(), foreign.read_bytes()) == before


# ── prune matrix ─────────────────────────────────────────────────────────


def test_prune_enriched_alone_no_flap_live_twin(tmp_path: Path) -> None:
    from obsidianizer.postprocess import enrich as enrich_fn

    src = tmp_path / "src"
    _write(src / "other.md", OTHER)
    processed, enriched = tmp_path / "p", tmp_path / "e"
    _processed(processed, "old.md", CONTENT, sid="dead-id")  # live twin
    _enriched(enriched, "old.md", CONTENT, sid="dead-id")
    report = enrich_fn(processed, enriched, _LLM(), prune=True,
                       source_index=_index(src))
    assert (enriched / "old.md").exists()  # Pair Guard: twin alive → kept
    assert "old.md" not in report.pruned
    assert report.retired == ["old.md"]  # but marked, visible in report


def test_full_cleanup_both_flags(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.manifest import read_manifest
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.postprocess import enrich as enrich_fn
    from obsidianizer.registry import ProcessorRegistry

    src = tmp_path / "src"
    _write(src / "new.md", CONTENT)
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.source_namespace = NS
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    # old processed note with dead id + its enriched twin + shared media
    _processed(s.target, "old.md", CONTENT, sid="dead-id")
    media = s.target / "media" / "pic.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"PNG")
    from obsidianizer.manifest import write_manifest
    write_manifest(s.target, frozenset({"old.md", "media/pic.png"}))
    enriched = tmp_path / "enr"
    _enriched(enriched, "old.md", CONTENT, sid="dead-id")

    pipe = run_pipeline(reg, s, prune=True)
    assert not (s.target / "old.md").exists()  # dead id pruned (stage 1)
    # media keep the legacy path rule (unchanged by 008C): a manifest-listed
    # orphan whose pair is gone is deleted…
    assert not (s.target / "media" / "pic.png").exists()
    assert "old.md" not in read_manifest(s.target)

    ai = enrich_fn(s.target, enriched, _LLM(), prune=True,
                   source_index=_index(src))
    assert not (enriched / "old.md").exists()  # twin gone → full cleanup
    assert "old.md" in ai.pruned


def test_pipeline_live_id_note_kept_despite_missing_rel(tmp_path: Path) -> None:
    from obsidianizer.pipeline import _prune_manifest_notes_and_media

    tgt = tmp_path / "tgt"
    tgt.mkdir()
    live_rel, live_sid = "new.md", make_source_id(NS, "new.md")
    stale = tgt / "stale.md"
    _write(stale, "---\ntitle: s\nsource_hash: h\nsource_id: %s\n---\nbody\n" % live_sid)
    gone = tgt / "gone.md"
    _write(gone, "---\ntitle: g\nsource_hash: h\nsource_id: dead\n---\nbody\n")
    idx_src = tmp_path / "src"
    (idx_src / "new.md").parent.mkdir(parents=True, exist_ok=True)
    _write(idx_src / "new.md", CONTENT)
    removed = _prune_manifest_notes_and_media(
        frozenset({"stale.md", "gone.md"}), set(), tgt, _index(idx_src)
    )
    assert removed == ["gone.md"]  # dead id deleted…
    assert stale.exists()  # …live id kept as safety net


def test_old_processed_hangs_without_prune(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.registry import ProcessorRegistry

    src = tmp_path / "src"
    _write(src / "new.md", CONTENT)
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.source_namespace = NS
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    _processed(s.target, "old.md", CONTENT, sid="dead-id")
    run_pipeline(reg, s, prune=False)
    assert (s.target / "old.md").exists()  # no prune: hangs as expected


def test_is_fresh_regression_and_size_persist(tmp_path: Path) -> None:
    from obsidianizer.postprocess import enrich as enrich_fn

    src = tmp_path / "src"
    _write(src / "a.md", CONTENT)
    processed, enriched = tmp_path / "p", tmp_path / "e"
    _processed(processed, "a.md", CONTENT, sid=make_source_id(NS, "a.md"))
    llm = _LLM()
    first = enrich_fn(processed, enriched, llm, source_index=_index(src))
    assert first.processed == 1
    text = (enriched / "a.md").read_text(encoding="utf-8")
    assert "source_size:" in text and "source_mtime:" in text
    second = enrich_fn(processed, enriched, llm, source_index=_index(src))
    assert second.skipped == 1 and llm.calls == 1  # _is_fresh by hash intact


# ── visibility ───────────────────────────────────────────────────────────


def test_retired_hidden_from_index_and_search(tmp_path: Path) -> None:
    from obsidianizer.nav_index import build_index_from_dir
    from obsidianizer.search import ChatIndex

    tgt = tmp_path / "tgt"
    _processed(tgt, "live.md", CONTENT, sid="s1")
    _processed(tgt, "old.md", CONTENT, sid="dead")
    old_text = (tgt / "old.md").read_text(encoding="utf-8")
    (tgt / "old.md").write_text(
        old_text.replace("process_version: 3", "process_version: 3\nstatus: retired", 1),
        encoding="utf-8",
    )
    md = build_index_from_dir(tgt)
    assert "live" in md and "old" not in md
    idx = ChatIndex(tgt, tmp_path / "idx", llm=None, embed_model="")
    stats = idx.refresh()
    assert stats["added"] == 1
    assert [n["rel"] for n in idx._notes] == ["live.md"]


def test_manifest_stable_and_media_kept(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.manifest import read_manifest
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.registry import ProcessorRegistry

    src = tmp_path / "src"
    _write(src / "new.md", CONTENT)
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.source_namespace = NS
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    _processed(s.target, "old.md", CONTENT, sid="dead-id")
    shared = s.target / "shared.png"
    shared.write_bytes(b"PNG")
    foreign = s.target / "foreign.png"
    foreign.write_bytes(b"PNG")
    from obsidianizer.manifest import write_manifest
    write_manifest(s.target, frozenset({"old.md", "shared.png"}))
    run_pipeline(reg, s, prune=True)
    manifest = read_manifest(s.target)
    assert "old.md" not in manifest
    # media: legacy path rule unchanged — a manifest-listed orphan is
    # deleted, while files outside the manifest are never touched.
    assert not (s.target / "shared.png").exists()
    assert (s.target / "foreign.png").exists()


def test_snapshot_settings_keeps_namespace(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.ui import _snapshot_settings

    src = Settings()
    src.source_namespace = NS
    src.config_path = tmp_path / "config.yml"
    snap = _snapshot_settings(src)
    assert snap.source_namespace == NS
    assert snap.config_path == tmp_path / "config.yml"


def test_retired_rebuilds_index_without_processing_or_pruning(tmp_path: Path) -> None:
    # Audit BLOCKER: reconcile marks retired with processed == 0 and
    # pruned == 0 (everything fresh, prune off) — enriched/_index.md must
    # still be rebuilt so the retired note leaves the navigation.
    from obsidianizer.postprocess import enrich as enrich_fn

    src = tmp_path / "src"
    _write(src / "live.md", OTHER)
    processed, enriched = tmp_path / "p", tmp_path / "e"
    live_sid = make_source_id(NS, "live.md")
    _processed(processed, "live.md", OTHER, sid=live_sid)
    _enriched(enriched, "live.md", OTHER, sid=live_sid)
    _enriched(enriched, "gone.md", CONTENT, sid="dead-id")
    report = enrich_fn(processed, enriched, _LLM(), prune=False,
                       source_index=_index(src))
    assert report.processed == 0 and report.pruned == []
    assert report.retired == ["gone.md"]
    index_md = (enriched / "_index.md").read_text(encoding="utf-8")
    assert "live" in index_md and "gone" not in index_md
