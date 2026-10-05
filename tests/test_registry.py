"""Registry tests — extension dispatch and batch resilience."""

from pathlib import Path

import pytest

from obsidianizer.base import Processor
from obsidianizer.chat_detect import make_chat_filter
from obsidianizer.exclusions import ExclusionFilter
from obsidianizer.models import SourceFile
from obsidianizer.registry import ProcessorRegistry
from obsidianizer.source_index import build_source_index

CHAT_MD = (
    "<!-- schema_version: 1 -->\n**Источник:** deepseek\n**Stable ID:**\n"
    "abc123\n<!-- hash: a1b2c3 title: t chat_order: 0 -->\n"
    "#### 👤 Вы\nпривет\n#### 🤖 AI\nответ\n"
)


class FakeProcessor(Processor):
    extensions = frozenset({".md"})

    def parse(self, src: SourceFile) -> dict:
        return {"title": src.name}

    def body(self, src: SourceFile) -> str:
        return "body"


def _make_fixture(tmp_path):
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plain text", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("# C", encoding="utf-8")
    return tmp_path


def test_register_and_dispatch():
    reg = ProcessorRegistry()
    reg.register(".md", FakeProcessor)
    proc = reg.processor_for(".md")
    assert proc is not None
    assert reg.processor_for(".txt") is None
    assert reg.processor_for("missing") is None


def test_scan_only_finds_registered_extensions(tmp_path):
    reg = ProcessorRegistry()
    reg.register(".md", FakeProcessor)
    files = reg.scan(_make_fixture(tmp_path))
    rels = sorted(f.rel_path for f in files)
    assert rels == ["a.md", "sub/c.md"]


def test_unknown_extension_does_not_break_batch(tmp_path):
    reg = ProcessorRegistry()
    reg.register(".md", FakeProcessor)
    files = reg.scan(_make_fixture(tmp_path))
    for f in files:
        assert reg.processor_for(f.ext) is not None
    # .txt is simply absent from results — the batch continues
    assert all(f.ext == ".md" for f in files)


def test_scan_filter_fn_filters_files(tmp_path):
    fixture = _make_fixture(tmp_path)
    # Filter that rejects "sub/c.md"
    def reject_sub(path, rel, ext):
        return rel != "sub/c.md"

    reg = ProcessorRegistry(filter_fn=reject_sub)
    reg.register(".md", FakeProcessor)
    files = reg.scan(fixture)
    rels = sorted(f.rel_path for f in files)
    assert rels == ["a.md"]


def test_scan_no_filter_fn_passes_all(tmp_path):
    fixture = _make_fixture(tmp_path)
    reg = ProcessorRegistry()
    reg.register(".md", FakeProcessor)
    files = reg.scan(fixture)
    rels = sorted(f.rel_path for f in files)
    assert rels == ["a.md", "sub/c.md"]


# ── TICKET-009: projection gating (single walk) ─────────────────────────────


def _projection_fixture(tmp_path: Path) -> Path:
    (tmp_path / "deepseek").mkdir()
    (tmp_path / "deepseek" / "chat.md").write_text(CHAT_MD, encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "plain.md").write_text("# обычная заметка\n", encoding="utf-8")
    (tmp_path / "notes" / "raw.txt").write_text("plain text\n", encoding="utf-8")
    (tmp_path / "notes" / "backup.bak").write_text("junk\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "chat.md").write_text(CHAT_MD, encoding="utf-8")
    return tmp_path


def test_project_matches_scan(tmp_path):
    """projection(Index, filter) == registry.scan() — gating for single walk."""

    root = _projection_fixture(tmp_path)
    excl = ExclusionFilter([".git"], ["*.bak"])
    reg = ProcessorRegistry(
        filter_fn=make_chat_filter(chat_only=True), exclusion_filter=excl
    )
    reg.register(".md", FakeProcessor)

    index = build_source_index(root, "ns", excl)
    scanned = sorted(f.rel_path for f in reg.scan(root))
    projected = sorted(f.rel_path for f in reg.project(index, root))

    assert projected == scanned
    # chat_only keeps the chat, drops the plain note; exclusions prune .git/.bak
    assert projected == ["deepseek/chat.md"]


def test_project_carries_source_id(tmp_path):
    root = _projection_fixture(tmp_path)
    excl = ExclusionFilter([".git"], ["*.bak"])
    reg = ProcessorRegistry(
        filter_fn=make_chat_filter(chat_only=True), exclusion_filter=excl
    )
    reg.register(".md", FakeProcessor)

    index = build_source_index(root, "ns", excl)
    for sf in reg.project(index, root):
        item = index.get_by_path(sf.rel_path)
        assert item is not None
        assert sf.source_id == item.source_id
        assert sf.source_id


def test_project_matches_scan_with_selection_override(tmp_path):
    """The manual-selection filter is projected 1:1 as well (TICKET-004)."""

    root = _projection_fixture(tmp_path)
    excl = ExclusionFilter([".git"], ["*.bak"])
    reg = ProcessorRegistry(
        filter_fn=make_chat_filter(
            chat_only=True,
            selection={"notes/plain.md"},
            selection_provided=True,
        ),
        exclusion_filter=excl,
    )
    reg.register(".md", FakeProcessor)

    index = build_source_index(root, "ns", excl)
    scanned = sorted(f.rel_path for f in reg.scan(root))
    projected = sorted(f.rel_path for f in reg.project(index, root))
    assert projected == scanned
    assert projected == ["notes/plain.md"]  # selection overrides chat detection