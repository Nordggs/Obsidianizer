"""Chat detection tests — marker counting, service detection, filter factory."""

from pathlib import Path

import pytest

from obsidianizer.chat_detect import (
    detect_chat_service,
    is_chat_export,
    make_chat_filter,
    read_head,
)

FIXTURES = Path(__file__).parent / "fixtures" / "md"


# ── Positive: exported chats ────────────────────────────────────────────────


def test_deepseek_export():
    text = (FIXTURES / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md").read_text(
        encoding="utf-8"
    )
    assert is_chat_export(text) is True


def test_chatgpt_export():
    text = (FIXTURES / "chatgpt" / "chatgpt_00_в4--оригами-держатель-для_3e3487f2.md").read_text(
        encoding="utf-8"
    )
    assert is_chat_export(text) is True


def test_claude_export():
    text = (
        "<!-- schema_version: 3 -->\n"
        "# Test\n\n"
        "**Источник:** Claude\n\n"
        "**Stable ID:**\nabcd1234\n\n"
        "#### 👤 Вы (2026-01-01)\nHello\n\n"
        "#### 🤖 AI\nHi there\n"
    )
    assert is_chat_export(text) is True


def test_gemini_export():
    text = (
        "<!-- schema_version: 2 -->\n"
        "# Gemini Chat\n\n"
        "**Источник:** Gemini\n\n"
        "**Stable ID:**\nbeef1234\n\n"
        "<!-- hash: abc123 title: test chat_order: 0 -->\n\n"
        "#### 👤 Вы\nQuestion\n\n"
        "#### 🤖 AI\nAnswer\n"
    )
    assert is_chat_export(text) is True


def test_qwen_export():
    text = (
        "<!-- schema_version: 2 -->\n"
        "# Qwen Chat\n\n"
        "**Источник:** Qwen\n\n"
        "**Stable ID:**\nc0de5678\n\n"
        "#### 👤 Вы\nHi\n\n"
        "#### 🤖 AI\nHello\n"
    )
    assert is_chat_export(text) is True


# ── Single marker rejection ─────────────────────────────────────────────────


def test_single_marker_rejected():
    # Only schema_version — not enough
    text = "<!-- schema_version: 3 -->\n# Just a markdown file\n"
    assert is_chat_export(text) is False


def test_single_marker_source_only():
    text = "# Notes\n\n**Источник:** DeepSeek\n\nSome text\n"
    assert is_chat_export(text) is False


# ── Negative: non-chat files ────────────────────────────────────────────────


def test_regular_markdown():
    text = "# My Notes\n\nThis is a regular markdown file.\n"
    assert is_chat_export(text) is False


def test_obsidianizer_card():
    text = (
        "---\n"
        "obsidianizer: true\n"
        "title: My Folder\n"
        "---\n\n"
        "## 📇 My Folder\n\nSome content\n"
    )
    assert is_chat_export(text) is False


def test_readme():
    text = "# Project\n\nREADME with instructions.\n"
    assert is_chat_export(text) is False


def test_index_md():
    text = "# Index\n\n- [[file1]]\n- [[file2]]\n"
    assert is_chat_export(text) is False


def test_topic_false_positive():
    text = (
        "---\n"
        "type: topic\n"
        "title: Some Topic\n"
        "---\n\n"
        "**Источник:** DeepSeek\n\n"
        "#### 👤 Вы\nHello\n\n"
        "#### 🤖 AI\nHi\n"
    )
    assert is_chat_export(text) is False


# ── Edge cases ──────────────────────────────────────────────────────────────


def test_empty_file():
    assert is_chat_export("") is False


def test_broken_utf8(tmp_path):
    bad = tmp_path / "broken.md"
    bad.write_bytes(b"\xff\xfe\x00 not valid utf8")
    head = read_head(bad)
    assert is_chat_export(head) is False


# ── read_head ───────────────────────────────────────────────────────────────


def test_read_head_respects_max_lines(tmp_path):
    f = tmp_path / "big.md"
    lines = [f"line {i}\n" for i in range(200)]
    f.write_text("".join(lines), encoding="utf-8")
    head = read_head(f, max_lines=10)
    assert head.count("\n") == 10


def test_read_head_handles_missing_file(tmp_path):
    head = read_head(tmp_path / "nonexistent.md")
    assert head == ""


# ── detect_chat_service ─────────────────────────────────────────────────────


def test_detect_chat_service_deepseek():
    text = (FIXTURES / "deepseek" / "deepseek_09_nextcloudobsidian_d78923c8.md").read_text(
        encoding="utf-8"
    )
    assert detect_chat_service(text) == "deepseek"


def test_detect_chat_service_chatgpt():
    text = (FIXTURES / "chatgpt" / "chatgpt_00_в4--оригами-держатель-для_3e3487f2.md").read_text(
        encoding="utf-8"
    )
    assert detect_chat_service(text) == "chatgpt"


def test_detect_chat_service_none():
    assert detect_chat_service("# Just a file\n") is None


# ── make_chat_filter ────────────────────────────────────────────────────────


def test_filter_chat_only_true_filters_non_chats(tmp_path):
    chat = tmp_path / "chat.md"
    chat.write_text(
        "<!-- schema_version: 3 -->\n**Источник:** DeepSeek\n**Stable ID:**\nabc1234\n"
        "#### 👤 Вы\nHello\n#### 🤖 AI\nHi\n",
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n\nREADME\n", encoding="utf-8")

    f = make_chat_filter(chat_only=True)
    assert f(chat, "chat.md", ".md") is True
    assert f(readme, "README.md", ".md") is False


def test_filter_chat_only_false_passes_all(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n", encoding="utf-8")

    f = make_chat_filter(chat_only=False)
    assert f(readme, "README.md", ".md") is True


def test_filter_non_md_always_passes(tmp_path):
    txt = tmp_path / "data.txt"
    txt.write_text("data", encoding="utf-8")

    f = make_chat_filter(chat_only=True)
    assert f(txt, "data.txt", ".txt") is True


def test_filter_selection_override(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n", encoding="utf-8")

    f = make_chat_filter(
        chat_only=True,
        selection={"README.md"},
        selection_provided=True,
    )
    assert f(readme, "README.md", ".md") is True


def test_filter_selection_excludes_unselected(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("# Other\n", encoding="utf-8")

    f = make_chat_filter(
        chat_only=True,
        selection={"README.md"},
        selection_provided=True,
    )
    assert f(readme, "README.md", ".md") is True
    assert f(other, "other.md", ".md") is False


def test_filter_empty_selection_processes_nothing(tmp_path):
    chat = tmp_path / "chat.md"
    chat.write_text(
        "<!-- schema_version: 3 -->\n**Источник:** DeepSeek\n",
        encoding="utf-8",
    )

    f = make_chat_filter(
        chat_only=True,
        selection=set(),
        selection_provided=True,
    )
    assert f(chat, "chat.md", ".md") is False
