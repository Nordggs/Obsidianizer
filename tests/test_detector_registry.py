"""TICKET-008D: detector registry chain, defaults, read-cost contract."""

from __future__ import annotations

from pathlib import Path

from obsidianizer.source_index import (
    _CONTENT_DETECTORS,
    detect_type,
    register_file_type,
)

CHAT_MD = (
    "<!-- schema_version: 1 -->\n**Источник:** test\n**Stable ID:**\nabc123\n"
    "<!-- hash: a1b2c3 title: t chat_order: 0 -->\n#### 👤 Вы\nпривет\n"
)


def _write(path: Path, text: str = "# заметки\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _restore(detectors_snapshot):
    _CONTENT_DETECTORS[:] = detectors_snapshot


def test_defaults_bit_for_bit(tmp_path: Path) -> None:
    # Same table as test_source_index.py::test_detect_type_table — the
    # registry generalization must not change a single default.
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
    assert detect_type(".md") == "text"


def test_two_arg_call_unchanged_with_restore() -> None:
    snapshot = list(_CONTENT_DETECTORS)
    try:
        register_file_type(".gcode2", "cnc")
        assert detect_type(".gcode2") == "cnc"
    finally:
        _restore(snapshot)
        from obsidianizer.source_index import FILE_TYPE_MAP

        FILE_TYPE_MAP.pop(".gcode2", None)
    assert detect_type(".gcode2") == "unknown"


def test_chain_priority_and_ext_wins(tmp_path: Path) -> None:
    snapshot = list(_CONTENT_DETECTORS)
    try:
        p = _write(tmp_path / "a.zzz", "hello\n")
        register_file_type(".zzz", "low", predicate=lambda path: True, priority=1)
        register_file_type(".zzz", "high", predicate=lambda path: True, priority=9)
        assert detect_type(".zzz", p) == "high"  # priority between predicates
        # extension map beats content predicates: .png stays image even
        # with a matching predicate registered for it (same for .md).
        register_file_type(".png", "special", predicate=lambda path: True, priority=99)
        assert detect_type(".png", tmp_path / "x.png") == "image"
        plain = _write(tmp_path / "p.md", "# обычные заметки\n")
        register_file_type(".md", "special", predicate=lambda path: True, priority=99)
        assert detect_type(".md", plain) == "text"
        # no path → predicates never run, ext map decides
        assert detect_type(".zzz") == "unknown"
    finally:
        _restore(snapshot)


def test_no_new_content_reads_without_predicate(tmp_path: Path) -> None:
    # Read-cost contract: mapped exts never touch file content; a
    # predicate runs only when explicitly registered for that ext — and
    # only when the ext map has no entry for it.
    blob = tmp_path / "b.bin"
    blob.write_bytes(bytes(range(256)) * 64)
    assert detect_type(".bin", blob) == "binary"
    calls: list[Path] = []
    snapshot = list(_CONTENT_DETECTORS)
    try:
        register_file_type(
            ".bin", "checked",
            predicate=lambda path: calls.append(path) or True,
        )
        assert detect_type(".bin", blob) == "binary"  # map wins, hook idle
        assert calls == []
        other = _write(tmp_path / "o.zzz", "hello\n")
        register_file_type(
            ".zzz", "checked",
            predicate=lambda path: calls.append(path) or False,
        )
        assert detect_type(".zzz", other) == "unknown"  # predicate said no…
        assert calls == [other]  # …but it ran: reading is opt-in by registration
    finally:
        _restore(snapshot)


def test_unreadable_predicate_path_degrades(tmp_path: Path) -> None:
    snapshot = list(_CONTENT_DETECTORS)
    try:
        def _boom(path: Path) -> bool:
            raise OSError("unreadable")

        register_file_type(".zzz", "x", predicate=_boom)
        assert detect_type(".zzz", tmp_path / "a.zzz") == "unknown"
    finally:
        _restore(snapshot)
