"""Pipeline tests — incrementality, LLM degradation, prune safety."""

import shutil
from pathlib import Path

import pytest

from obsidianizer.config import PROCESS_VERSION, Settings
from obsidianizer.llm import LLMClient
from obsidianizer.md_processor import MdProcessor
from obsidianizer.manifest import read_manifest
from obsidianizer.pipeline import run as run_pipeline
from obsidianizer.registry import ProcessorRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "md"


def _prepare(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "processed"
    shutil.copytree(FIXTURES, source)
    return source, target


def _runner(source: Path, target: Path, llm=None, **kw):
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    settings = Settings()
    settings.source = source
    settings.target = target
    settings.llm_enabled = False
    return run_pipeline(reg, settings, llm, **kw)


def test_basic_run_writes_everything(tmp_path):
    source, target = _prepare(tmp_path)
    report = _runner(source, target)
    assert report.failed == []
    assert report.processed == 2
    assert report.skipped == 0

    note = target / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md"
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert "source_hash:" in text
    assert "tags:" in text
    assert "Nextcloud+Obsidian" in text
    # media was copied preserving relative layout
    assert (target / "deepseek" / "media" / "01010101_abc.png").exists()
    # navigation index compiled from the produced notes
    index = target / "_index.md"
    assert index.exists()
    index_text = index.read_text(encoding="utf-8")
    assert "Пока нет записей" not in index_text
    assert "Nextcloud+Obsidian" in index_text
    # ownership manifest written last
    assert read_manifest(target) >= {
        "deepseek/deepseek_09_nextcloudobsidian_d78923c8.md",
        "deepseek/media/01010101_abc.png",
        "chatgpt/chatgpt_00_в4--оригами-держатель-для_3e3487f2.md",
        "_index.md",
    }


def test_second_run_is_incremental(tmp_path):
    source, target = _prepare(tmp_path)
    first = _runner(source, target)
    assert first.processed == 2

    second = _runner(source, target)
    assert second.skipped == 2
    assert second.processed == 0


def test_changed_source_is_reprocessed(tmp_path):
    source, target = _prepare(tmp_path)
    assert _runner(source, target).processed == 2

    note = source / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nДополнение", encoding="utf-8")
    report = _runner(source, target)
    assert report.processed == 1
    assert report.skipped == 1


def test_llm_unavailable_degrades_gracefully(tmp_path):
    source, target = _prepare(tmp_path)
    dead_llm = LLMClient(
        endpoint="http://127.0.0.1:1",
        model="anything",
        timeout=1,
        limit_chars=6000,
        prompt="{content}",
    )
    report = _runner(source, target, llm=dead_llm)
    assert report.failed == []
    assert report.processed == 2
    note = target / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md"
    assert note.exists()


def test_malformed_file_does_not_kill_batch(tmp_path):
    source, target = _prepare(tmp_path)
    bad = source / "deepseek" / "broken.md"
    bad.write_bytes(b"\xff\xfe\x00 not valid utf8 body")
    report = _runner(source, target)
    assert report.failed  # the broken file is reported
    assert report.processed == 2  # the rest of the batch succeeded
    assert (target / "chatgpt").exists()


def test_prune_removes_only_owned_files(tmp_path):
    source, target = _prepare(tmp_path)
    _runner(source, target)

    # user places a foreign note in the target
    foreign = target / "my_notes" / "personal.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("my personal vault note", encoding="utf-8")

    # one source chat disappears (deleted upstream)
    shutil.rmtree(source / "deepseek")

    report = _runner(source, target, prune=True)
    assert "deepseek/deepseek_09_nextcloudobsidian_d78923c8.md" in report.pruned
    assert "deepseek/media/01010101_abc.png" in report.pruned
    # current results survive
    assert (target / "chatgpt").exists()
    # foreign file is untouched
    assert foreign.exists()


def test_new_manifest_recorded_after_prune(tmp_path):
    source, target = _prepare(tmp_path)
    _runner(source, target)
    shutil.rmtree(source / "deepseek")
    report = _runner(source, target, prune=True)
    assert report.pruned
    manifest = read_manifest(target)
    assert "deepseek/deepseek_09_nextcloudobsidian_d78923c8.md" not in manifest
    assert "chatgpt/chatgpt_00_в4--оригами-держатель-для_3e3487f2.md" in manifest


def test_dry_run_writes_nothing(tmp_path):
    source, target = _prepare(tmp_path)
    report = _runner(source, target, dry_run=True)
    assert report.processed == 2
    assert target.exists() is False


def test_navigation_section_written(tmp_path):
    source, target = _prepare(tmp_path)
    _runner(source, target)
    note = target / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md"
    text = note.read_text(encoding="utf-8")
    assert "## Навигация" in text
    assert "### Сообщения" in text
    assert "1. 👤 Вы — 2026-07-01 12:00" in text
    # structural counters are in the frontmatter, the index is not
    assert f"process_version: {PROCESS_VERSION}" in text
    assert "roles:" in text
    assert "links:" in text
    assert "message_index" not in text.split("---")[1]
    # the verbatim body still follows the card separator unchanged
    assert "#### 👤 Вы (2026-07-01 12:00)" in text
    assert "Мне нужен контроль версий для чертежей DWG" in text


def test_older_format_version_is_reprocessed(tmp_path):
    source, target = _prepare(tmp_path)
    assert _runner(source, target).processed == 2

    note = target / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md"
    stale = note.read_text(encoding="utf-8").replace(
        f"process_version: {PROCESS_VERSION}", "process_version: 1"
    )
    note.write_text(stale, encoding="utf-8")

    report = _runner(source, target)
    assert report.processed == 1  # only the stale note is rewritten
    assert report.skipped == 1  # the fresh note stays untouched
    assert f"process_version: {PROCESS_VERSION}" in note.read_text(encoding="utf-8")