"""TICKET-008B: additive idempotent backfill of source_id (+ CLI trigger)."""

from __future__ import annotations

from pathlib import Path

from obsidianizer.emit import atomic_write
from obsidianizer.enrich import build_card, build_frontmatter
from obsidianizer.source_index import make_source_id


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _processed_note(root: Path, rel: str, source_hash: str = "h-1") -> Path:
    meta = {"title": rel, "service": "t", "messages": {"total": 1},
            "process_version": 3}
    body = "тело заметки\n\n## Comments\n\nручной комментарий\n"
    p = root / rel
    _write(p, build_frontmatter(meta, "", [], source_hash) + "\n"
           + build_card(meta, "", []) + "\n---\n" + body)
    return p


def _enriched_note(root: Path, rel: str, source_hash: str = "h-1") -> Path:
    from obsidianizer.nav_index import frontmatter_of

    meta = {"title": rel, "service": "t", "messages": {"total": 1},
            "process_version": 3}
    body = "тело заметки\n"
    p = root / rel
    _write(p, build_frontmatter(meta, "s", ["t1"], source_hash) + "\n"
           + build_card(meta, "s", ["t1"]) + "\n---\n" + body)
    # mark owned like postprocess does
    text = p.read_text(encoding="utf-8")
    assert frontmatter_of(p) is not None
    p.write_text(
        text.replace("source_hash: h-1", "source_hash: h-1\nai_hash: h-1", 1),
        encoding="utf-8",
    )
    return p


NS = "11111111-1111-4111-8111-111111111111"


def _run(owned: Path, source: Path, namespace: str = NS, exclusion=None):
    from obsidianizer.enrichment import backfill_source_ids

    return backfill_source_ids(
        owned, source_root=source, namespace=namespace, exclusion=exclusion
    )


def test_old_notes_get_ids(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    owned = tmp_path / "processed"
    _processed_note(owned, "a.md")
    report = _run(owned, src)
    assert report["migrated"] == ["a.md"]
    assert report["unresolved"] == []
    text = (owned / "a.md").read_text(encoding="utf-8")
    assert f"source_id: {make_source_id(NS, 'a.md')}" in text


def test_enriched_notes_get_ids(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    owned = tmp_path / "enriched"
    _enriched_note(owned, "a.md")
    report = _run(owned, src)
    assert report["migrated"] == ["a.md"]
    text = (owned / "a.md").read_text(encoding="utf-8")
    assert f"source_id: {make_source_id(NS, 'a.md')}" in text
    assert "ai_hash: h-1" in text


def test_surgical_insert_byte_preserving(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    owned = tmp_path / "processed"
    p = _processed_note(owned, "a.md")
    before = p.read_text(encoding="utf-8").splitlines(keepends=True)
    _run(owned, src)
    after = (owned / "a.md").read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(after) == len(before) + 1
    inserted = [ln for ln in after if ln not in before]
    assert len(inserted) == 1 and inserted[0].startswith("source_id: ")
    removed = [ln for ln in before if ln not in after]
    assert removed == []
    # markers untouched
    text = "".join(after)
    assert "source_hash: h-1" in text
    assert "process_version: 3" in text
    assert "ручной комментарий" in text


def test_idempotent_second_run_no_changes(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    owned = tmp_path / "processed"
    _processed_note(owned, "a.md")
    first = _run(owned, src)
    assert first["migrated"] == ["a.md"]
    snapshot = (owned / "a.md").read_bytes()
    second = _run(owned, src)
    assert second == {"migrated": [], "unresolved": []}
    assert (owned / "a.md").read_bytes() == snapshot


def test_unresolved_no_source_item(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    owned = tmp_path / "processed"
    _processed_note(owned, "ghost.md")
    report = _run(owned, src)
    assert report["migrated"] == []
    assert report["unresolved"] == [{"rel": "ghost.md", "reason": "no_source_item"}]
    assert "source_id" not in (owned / "ghost.md").read_text(encoding="utf-8")


def test_unresolved_no_namespace_no_minting(tmp_path: Path) -> None:
    from obsidianizer.config import Settings

    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    owned = tmp_path / "processed"
    _processed_note(owned, "a.md")
    s = Settings()
    assert s.source_namespace == ""
    report = _run(owned, src, namespace=s.source_namespace)
    assert report["migrated"] == []
    assert report["unresolved"] == [{"rel": "a.md", "reason": "no_namespace"}]
    assert s.source_namespace == ""  # identity never minted during backfill


def test_unresolved_source_unavailable(tmp_path: Path) -> None:
    owned = tmp_path / "processed"
    _processed_note(owned, "a.md")
    report = _run(owned, tmp_path / "no-such-source")
    assert report["migrated"] == []
    assert report["unresolved"] == [{"rel": "a.md", "reason": "source_unavailable"}]


def test_topics_and_service_skipped(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    owned = tmp_path / "enriched"
    _enriched_note(owned, "a.md")
    topics_dir = owned / "topics"
    _write(topics_dir / "Name.md",
            "---\ntype: topic\ntitle: Name\ntopic_hash: ab12\nchats: []\n---\nbody\n")
    _write(owned / "_index.md", "# index\n")
    _write(owned / ".obsidianizer" / "index.json", "{}")
    report = _run(owned, src)
    assert report["migrated"] == ["a.md"]
    assert report["unresolved"] == []
    assert "source_id" not in (topics_dir / "Name.md").read_text(encoding="utf-8")
    assert "source_id" not in (owned / "_index.md").read_text(encoding="utf-8")


def test_source_snapshot_unchanged(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "a.md", "# raw\n")
    _write(src / "sub" / "b.txt", "x\n")
    before = sorted((p.as_posix(), p.read_bytes()) for p in src.rglob("*") if p.is_file())
    owned = tmp_path / "processed"
    _processed_note(owned, "a.md")
    _run(owned, src)
    after = sorted((p.as_posix(), p.read_bytes()) for p in src.rglob("*") if p.is_file())
    assert before == after


def test_cli_flag_parses() -> None:
    from obsidianizer.cli import build_parser

    args = build_parser().parse_args(["--backfill-source-ids"])
    assert args.backfill_source_ids is True
    assert build_parser().parse_args([]).backfill_source_ids is False


def test_non_utf8_note_unresolved_no_crash(tmp_path: Path) -> None:
    # frontmatter_of() reads with errors="replace", so a note with foreign
    # bytes passes candidate selection; the strict pre-insert read must then
    # report unresolved instead of raising UnicodeDecodeError (which would
    # abort the whole run — the remaining candidates must still migrate).
    src = tmp_path / "src"
    _write(src / "bad.md", "# raw\n")
    _write(src / "good.md", "# raw\n")
    owned = tmp_path / "processed"
    _processed_note(owned, "good.md")
    bad = _processed_note(owned, "bad.md")
    raw = bad.read_bytes() + "external edit \xff\xfe\n".encode("latin-1")
    bad.write_bytes(raw)
    report = _run(owned, src)
    assert report["migrated"] == ["good.md"]
    assert report["unresolved"] == [{"rel": "bad.md", "reason": "no_source_item"}]
    assert bad.read_bytes() == raw  # foreign bytes untouched, no rewrite
    assert "source_id" in (owned / "good.md").read_text(encoding="utf-8")
