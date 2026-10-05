"""TICKET-007: Source Index — identity invariants, structure, typology."""

from __future__ import annotations

import hashlib
from pathlib import Path

from obsidianizer.exclusions import ExclusionFilter
from obsidianizer.source_index import (
    Kind,
    SourceIndex,
    build_source_index,
    detect_type,
    make_source_id,
    register_file_type,
)

NS_A = "11111111-1111-4111-8111-111111111111"
NS_B = "22222222-2222-4222-8222-222222222222"

CHAT_MD = (
    "<!-- schema_version: 1 -->\n**Источник:** test\n**Stable ID:**\nabc123\n"
    "<!-- hash: a1b2c3 title: t chat_order: 0 -->\n#### 👤 Вы\nпривет\n"
)


def _write(path: Path, text: str = "# заметки\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── identity ─────────────────────────────────────────────────────────────


def test_make_source_id_formula() -> None:
    assert make_source_id(NS_A, "a/b.md") == hashlib.sha1(
        f"{NS_A}\0a/b.md".encode()
    ).hexdigest()


def test_id_deterministic_across_rebuilds(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", CHAT_MD)
    first = build_source_index(tmp_path, NS_A)
    second = build_source_index(tmp_path, NS_A)
    assert first.get_by_path("a.md").source_id == second.get_by_path("a.md").source_id


def test_content_and_mtime_change_keep_id(tmp_path: Path) -> None:
    p = _write(tmp_path / "a.md", "версия 1\n")
    before = build_source_index(tmp_path, NS_A).get_by_path("a.md").source_id
    p.write_text("совершенно другой контент\n" * 50, encoding="utf-8")
    after = build_source_index(tmp_path, NS_A).get_by_path("a.md").source_id
    assert before == after


def test_rename_gives_new_id_move_gives_new_id(tmp_path: Path) -> None:
    _write(tmp_path / "a.md")
    idx = build_source_index(tmp_path, NS_A)
    old_id = idx.get_by_path("a.md").source_id
    (tmp_path / "a.md").rename(tmp_path / "b.md")
    idx2 = build_source_index(tmp_path, NS_A)
    assert idx2.get_by_path("a.md") is None  # absent rel → no object
    assert idx2.get_by_path("b.md").source_id != old_id
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.md").rename(tmp_path / "sub" / "b.md")
    idx3 = build_source_index(tmp_path, NS_A)
    assert idx3.get_by_path("sub/b.md").source_id != old_id


def test_reappearance_at_same_rel_keeps_id(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "v1\n")
    old_id = build_source_index(tmp_path, NS_A).get_by_path("a.md").source_id
    (tmp_path / "a.md").unlink()
    assert build_source_index(tmp_path, NS_A).get_by_path("a.md") is None
    _write(tmp_path / "a.md", "v2 совсем другое\n")
    assert build_source_index(tmp_path, NS_A).get_by_path("a.md").source_id == old_id


def test_root_move_with_same_namespace_keeps_ids(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "sub" / "a.md")
    ids_before = {
        i.rel_posix: i.source_id
        for i in build_source_index(src, NS_A).items.values()
    }
    moved = tmp_path / "moved"
    src.rename(moved)
    ids_after = {
        i.rel_posix: i.source_id
        for i in build_source_index(moved, NS_A).items.values()
    }
    assert ids_before == ids_after


def test_different_source_same_rel_different_ids(tmp_path: Path) -> None:
    s1 = tmp_path / "s1"
    s2 = tmp_path / "s2"
    _write(s1 / "a.md")
    _write(s2 / "a.md")
    id1 = build_source_index(s1, NS_A).get_by_path("a.md").source_id
    id2 = build_source_index(s2, NS_B).get_by_path("a.md").source_id
    assert id1 != id2


# ── structure ────────────────────────────────────────────────────────────


def test_folders_incl_empty_and_parent_children(tmp_path: Path) -> None:
    _write(tmp_path / "sub" / "a.md")
    (tmp_path / "empty").mkdir()
    idx = build_source_index(tmp_path, NS_A)
    sub = idx.get_by_path("sub")
    empty = idx.get_by_path("empty")
    assert sub is not None and sub.kind == Kind.FOLDER
    assert empty is not None and empty.kind == Kind.FOLDER
    assert empty.file_type == "folder"
    assert empty.size is None and empty.mtime_ns is None
    root = idx.get_by_path("")
    assert root is not None and root.kind == Kind.FOLDER
    kids = {i.rel_posix for i in idx.children_of(root.source_id)}
    assert {"sub", "empty"} <= kids
    assert {i.rel_posix for i in idx.children_of(sub.source_id)} == {"sub/a.md"}
    assert idx.get_by_path("sub/a.md").parent_source_id == sub.source_id


def test_excluded_absent(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "a.md")
    _write(tmp_path / "keep" / "b.md")
    _write(tmp_path / "x.tmp")
    idx = build_source_index(
        tmp_path, NS_A, ExclusionFilter([".git"], ["*.tmp"])
    )
    rels = {i.rel_posix for i in idx.items.values()}
    assert ".git" not in rels
    assert "keep/b.md" in rels
    assert "x.tmp" not in rels
    # service allowlist survives user masks
    _write(tmp_path / "_index.md")
    idx2 = build_source_index(tmp_path, NS_A, ExclusionFilter([], ["*.md"]))
    assert "_index.md" in {i.rel_posix for i in idx2.items.values()}


def test_posix_serialization_and_no_source_writes(tmp_path: Path) -> None:
    _write(tmp_path / "sub" / "a.md")
    before = sorted(p.as_posix() for p in tmp_path.rglob("*"))
    idx = build_source_index(tmp_path, NS_A)
    after = sorted(p.as_posix() for p in tmp_path.rglob("*"))
    assert before == after  # nothing created inside Source
    for item in idx.items.values():
        assert "\\" not in item.rel_posix
        assert isinstance(item.rel_posix, str)


# ── typology ─────────────────────────────────────────────────────────────


def test_detect_type_table(tmp_path: Path) -> None:
    assert detect_type(".dwg") == "cad"
    assert detect_type(".xlsx") == "spreadsheet"
    assert detect_type(".png") == "image"
    assert detect_type(".zip") == "archive"
    assert detect_type(".pdf") == "document"
    assert detect_type(".whatever") == "unknown"
    chat = _write(tmp_path / "c.md", CHAT_MD)
    plain = _write(tmp_path / "p.md", "# обычные заметки\n")
    assert detect_type(".md", chat) == "chat_export"
    assert detect_type(".md", plain) == "text"
    assert detect_type(".md", tmp_path / "missing.md") == "text"


def test_register_file_type_extension_point() -> None:
    register_file_type(".gcode", "cnc")
    try:
        assert detect_type(".gcode") == "cnc"
    finally:
        from obsidianizer.source_index import FILE_TYPE_MAP

        del FILE_TYPE_MAP[".gcode"]


def test_build_typology_and_chat_id_metadata(tmp_path: Path) -> None:
    _write(tmp_path / "plan.dwg")
    _write(tmp_path / "chat.md", CHAT_MD)
    _write(tmp_path / "notes.md", "# обычные\n")
    idx = build_source_index(tmp_path, NS_A)
    assert idx.get_by_path("plan.dwg").file_type == "cad"
    assert idx.get_by_path("plan.dwg").content_hash is None
    chat = idx.get_by_path("chat.md")
    assert chat.file_type == "chat_export"
    assert chat.metadata.get("chat_id") == "abc123"
    assert idx.get_by_path("notes.md").file_type == "text"


def test_source_index_register_get() -> None:
    idx = SourceIndex(namespace=NS_A, root="/x")
    item = idx.get_by_path("nope.md")
    assert item is None
    assert idx.children_of("missing") == []


# ── wiring (availability layer, behaviour unchanged) ─────────────────────


def test_pipeline_report_carries_source_index(tmp_path: Path) -> None:
    from obsidianizer.config import Settings
    from obsidianizer.md_processor import MdProcessor
    from obsidianizer.pipeline import run as run_pipeline
    from obsidianizer.registry import ProcessorRegistry

    src = tmp_path / "src"
    _write(src / "sub" / "a.md", CHAT_MD)
    (src / "empty").mkdir()
    s = Settings()
    s.source = src
    s.target = tmp_path / "tgt"
    s.chat_only = False
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    report = run_pipeline(reg, s)
    assert report.failed == []
    idx = report.source_index
    assert idx is not None
    assert idx.get_by_path("sub/a.md") is not None
    assert idx.get_by_path("empty") is not None
    assert idx.get_by_path("sub/a.md").file_type == "chat_export"
    # no side effects: no Markdown in Source, no config file created
    assert sorted(p.suffix for p in src.rglob("*") if p.is_file()) == [".md"]
    assert s.source_namespace  # assigned in-memory
    assert not (tmp_path / "config.yml").exists()


def test_ui_scan_stores_source_index(tmp_path: Path) -> None:
    from obsidianizer.ui import UIApp

    src = tmp_path / "src"
    _write(src / "plain.md", "# обычные заметки\n")
    app = UIApp()
    app.settings.source = src
    res = app.scan()
    assert res["ok"] is True
    idx = app._last_source_index
    assert idx is not None
    assert idx.get_by_path("plain.md") is not None
    # preview response shape: legacy fields kept (TICKET-011 adds the
    # additive ``aggregate``/``detected_total``)
    assert set(res) >= {"ok", "total", "files"}
    assert "aggregate" in res and "detected_total" in res


# ── namespace persistence ────────────────────────────────────────────────


def test_namespace_roundtrip(tmp_path: Path) -> None:
    from obsidianizer.config import Settings

    cfg = tmp_path / "config.yml"
    s = Settings()
    s.source_namespace = NS_A
    s.save(cfg)
    assert Settings.load(cfg).source_namespace == NS_A


def test_ensure_first_run_silent_single_uuid(tmp_path: Path, caplog) -> None:
    from obsidianizer.config import Settings, ensure_source_namespace

    cfg = tmp_path / "config.yml"
    s = Settings()
    s.config_path = cfg
    with caplog.at_level("WARNING", logger="obsidianizer.config"):
        ns, created = ensure_source_namespace(s)
    assert created is True and ns
    assert not [r for r in caplog.records if "source_namespace" in r.getMessage()]
    # second call reuses, never regenerates silently
    ns2, created2 = ensure_source_namespace(s)
    assert (ns2, created2) == (ns, False)
    assert Settings.load(cfg).source_namespace == ns


def test_ensure_missing_key_in_existing_config_warns(tmp_path: Path, caplog) -> None:
    from obsidianizer.config import Settings, ensure_source_namespace

    cfg = tmp_path / "config.yml"
    cfg.write_text("source: ./raw\n", encoding="utf-8")
    s = Settings.load(cfg)
    assert s.source_namespace == ""
    with caplog.at_level("WARNING", logger="obsidianizer.config"):
        ns, created = ensure_source_namespace(s)
    assert created is True and ns
    assert any("source_namespace" in r.getMessage() for r in caplog.records)
    assert Settings.load(cfg).source_namespace == ns
