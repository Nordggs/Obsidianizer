"""Registry tests — extension dispatch and batch resilience."""

from pathlib import Path

import pytest

from obsidianizer.base import Processor
from obsidianizer.models import SourceFile
from obsidianizer.registry import ProcessorRegistry


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