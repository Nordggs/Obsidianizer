"""AI post-processing stage tests — processed -> enriched, media, prune.

The stage must never write into the processed (source) root; incrementality is
driven by `ai_hash` (enriched) vs `source_hash` (processed). Media is copied so
the enriched vault stays self-contained; optional pruning removes orphans.
"""

from pathlib import Path

from obsidianizer.emit import atomic_write
from obsidianizer.enrich import build_card, build_frontmatter, compose
from obsidianizer.postprocess import enrich


class _FakeLLM:
    def __init__(
        self,
        result=None,
        *,
        fail=False,
    ):
        self.result = result if result is not None else {
            "summary": "Сводка по диалогу",
            "tags": ["тест", "обсуждение"],
            "topic": "тестовая тема",
            "type": "обсуждение",
        }
        self.fail = fail
        self.calls = 0

    def analyze(self, content):  # noqa: ARG002
        self.calls += 1
        if self.fail:
            return {"summary": "", "tags": [], "topic": "", "type": ""}
        return dict(self.result)


def _note(root: Path, rel: str, source_hash: str, body: str = "тело заметки", extra: dict | None = None):
    meta = {"title": rel, "service": "test", "messages": {}, "branches": 1}
    meta.update(extra or {})
    atomic_write(root / rel, compose(build_frontmatter(meta, "", [], source_hash), build_card(meta, "", []), body))
    return root / rel


def _enriched_note(root: Path, rel: str, ai_hash: str, body: str = "тело заметки", extra: dict | None = None):
    meta = {"title": rel, "service": "test", "messages": {}, "branches": 1, "ai_hash": ai_hash}
    meta.update(extra or {})
    atomic_write(
        root / rel,
        compose(
            build_frontmatter(meta, "Сводка", ["тест"], ai_hash),
            build_card(meta, "", []),
            body,
        ),
    )
    return root / rel


def _owner_index(live_ids: list[str]) -> "object":
    """Minimal live SourceIndex for prune tests (TICKET-012 identity contract)."""

    from obsidianizer.source_index import Kind, SourceIndex, SourceItem

    index = SourceIndex(namespace="test-ns", root="test-root")
    for i, sid in enumerate(live_ids):
        index.register(
            SourceItem(
                source_id=sid, rel_posix=f"live-{i}.md",
                kind=Kind.FILE, file_type="text",
            )
        )
    return index


def test_enrich_writes_to_enriched_and_leaves_processed_untouched(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1", "первые строки")

    before = (processed / "a.md").read_text(encoding="utf-8")
    report = enrich(processed, enriched, _FakeLLM())

    assert report.processed == 1
    assert report.failed == []
    assert (processed / "a.md").read_text(encoding="utf-8") == before

    out = enriched / "a.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "source_hash: H1" in text
    assert "ai_hash: H1" in text
    assert "summary:" in text
    assert "Сводка по диалогу" in text
    assert "первые строки" in text


def test_enrich_skips_fresh_copies_without_calling_llm(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    old = _enriched_note(enriched, "a.md", "H1")
    old_text = old.read_text(encoding="utf-8")

    llm = _FakeLLM()
    report = enrich(processed, enriched, llm)

    assert report.skipped == 1
    assert report.processed == 0
    assert llm.calls == 0
    assert old.read_text(encoding="utf-8") == old_text


def test_enrich_reprocesses_when_source_changed(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H2")
    _enriched_note(enriched, "a.md", "H1")

    report = enrich(processed, enriched, _FakeLLM())

    assert report.skipped == 0
    assert report.processed == 1
    assert "ai_hash: H2" in (enriched / "a.md").read_text(encoding="utf-8")


def test_enrich_ignores_foreign_files(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    atomic_write(processed / "foreign.md", "no frontmatter here\n---\nsome text")

    report = enrich(processed, enriched, _FakeLLM())

    assert report.processed == 0
    assert report.skipped == 0
    assert not (enriched / "foreign.md").exists()


def test_failed_empty_response_records_error_and_writes_nothing(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")

    report = enrich(processed, enriched, _FakeLLM(fail=True))

    assert report.processed == 0
    assert len(report.failed) == 1
    assert not (enriched / "a.md").exists()


def test_enrich_rebuilds_index_in_enriched(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1", body="первый")
    _note(processed, "b.md", "H2", body="второй")

    enrich(processed, enriched, _FakeLLM())

    index = enriched / "_index.md"
    assert index.is_file()
    text = index.read_text(encoding="utf-8")
    assert "Индекс" in text
    assert "a" in text and "b" in text


def test_enrich_copies_referenced_media(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    media_dir = processed / "chatgpt" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "pic.png").write_bytes(b"PNG")
    _note(processed, "chatgpt/note.md", "H1", body="смотри ![](media/pic.png)")

    report = enrich(processed, enriched, _FakeLLM())

    assert report.processed == 1
    assert (enriched / "chatgpt" / "media" / "pic.png").is_file()
    assert (enriched / "chatgpt" / "media" / "pic.png").read_bytes() == b"PNG"


def test_enrich_does_not_copy_remote_or_hash_media(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(
        processed,
        "note.md",
        "H1",
        body="![](https://x/y.png) ![](media/a.png) ![](#anchor)",
    )

    enrich(processed, enriched, _FakeLLM())

    assert not (enriched / "media").exists()


def _orphan_fixture(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "keep.md", "H1", body="![x](media/keep.png)",
          extra={"source_id": "SID-KEEP"})
    media = processed / "media"
    media.mkdir(parents=True)
    (media / "keep.png").write_bytes(b"K")

    old_a = _enriched_note(enriched, "keep.md", "H1", body="![x](media/keep.png)",
                           extra={"source_id": "SID-KEEP"})
    (enriched / "media").mkdir(parents=True, exist_ok=True)
    (enriched / "media" / "keep.png").write_bytes(b"K")
    _enriched_note(enriched, "orphan.md", "H9", body="![x](media/orphan.png)",
                   extra={"source_id": "SID-DEAD"})
    (enriched / "media" / "orphan.png").write_bytes(b"O")
    return processed, enriched, old_a


def test_prune_removes_orphans_and_only_their_media(tmp_path):
    processed, enriched, _ = _orphan_fixture(tmp_path)

    # TICKET-012: prune runs on identity (dead SID-DEAD, live SID-KEEP).
    report = enrich(processed, enriched, _FakeLLM(), prune=True,
                    source_index=_owner_index(["SID-KEEP"]))

    assert report.pruned == ["orphan.md"]
    assert not (enriched / "orphan.md").exists()
    assert not (enriched / "media" / "orphan.png").exists()
    assert (enriched / "keep.md").exists()
    assert (enriched / "media" / "keep.png").exists()


def test_prune_never_rel_deletes_idless_notes(tmp_path):
    """TICKET-012: a note without ``source_id`` is kept, never rel-deleted."""

    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "keep.md", "H1")
    _enriched_note(enriched, "orphan.md", "H9")

    report = enrich(processed, enriched, _FakeLLM(), prune=True,
                    source_index=_owner_index([]))

    assert "orphan.md" not in report.pruned
    assert (enriched / "orphan.md").exists()


def test_prune_keeps_media_still_shared_by_other_notes(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    media = processed / "media"
    media.mkdir(parents=True)
    (media / "shared.png").write_bytes(b"S")
    _note(processed, "a.md", "H1", body="![x](media/shared.png)",
          extra={"source_id": "SID-A"})
    _note(processed, "b.md", "H2", body="![x](media/shared.png)",
          extra={"source_id": "SID-B"})

    for rel, h, sid in (("a.md", "H1", "SID-A"), ("b.md", "H2", "SID-B")):
        _enriched_note(enriched, rel, h, body="![x](media/shared.png)",
                       extra={"source_id": sid})
    en_media = enriched / "media"
    en_media.mkdir(parents=True, exist_ok=True)
    (en_media / "shared.png").write_bytes(b"S")
    _enriched_note(enriched, "orphan.md", "H9", body="![x](media/shared.png)",
                   extra={"source_id": "SID-DEAD"})

    # TICKET-012: prune runs on identity.
    report = enrich(processed, enriched, _FakeLLM(), prune=True,
                    source_index=_owner_index(["SID-A", "SID-B"]))

    assert report.pruned == ["orphan.md"]
    assert (enriched / "media" / "shared.png").exists(), (
        "shared media referenced by another note must survive pruning"
    )


def test_cancel_stops_and_skips_prune_and_index(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "keep.md", "H1")
    _enriched_note(enriched, "orphan.md", "H9")

    report = enrich(processed, enriched, _FakeLLM(), prune=True, cancel_check=lambda: True)

    assert report.cancelled is True
    assert report.pruned == []
    assert (enriched / "orphan.md").exists(), "cancel must not prune"
    assert not (enriched / "_index.md").exists(), "cancel must not write index"