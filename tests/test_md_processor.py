"""MdProcessor tests — metadata parsing and body preservation."""

from pathlib import Path

from obsidianizer.md_processor import MdProcessor
from obsidianizer.models import SourceFile

FIXTURES = Path(__file__).parent / "fixtures" / "md"
PROC = MdProcessor()


def _src(rel: str) -> SourceFile:
    p = (FIXTURES / rel).resolve()
    return SourceFile(abs_path=p, rel_path=rel, ext=".md")


def test_parses_deepseek_header():
    meta = PROC.parse(_src("deepseek/deepseek_09_nextcloudobsidian_d78923c8.md"))
    assert meta["title"] == "Nextcloud+Obsidian"
    assert meta["service"] == "deepseek"
    assert meta["chat_id"] == "d78923c8"
    assert meta["source_url"].startswith("https://chat.deepseek.com")
    assert meta["export_date"] == "2026-08-08 04:19"
    assert meta["messages"] == {"total": 81, "user": 40, "assistant": 41}
    assert meta["attachments"] == 2
    assert meta["content_hash"] == "abc254"
    assert meta["chat_order"] == 13
    assert meta["branches"] == 1
    assert meta["first_ts"] == "2026-07-01 12:00"
    assert meta["last_ts"] == "2026-07-01 12:06"


def test_parses_branched_chatgpt_header():
    meta = PROC.parse(_src("chatgpt/chatgpt_00_в4--оригами-держатель-для_3e3487f2.md"))
    assert meta["service"] == "chatgpt"
    assert meta["messages"]["total"] == 9
    assert meta["branches"] == 2
    assert meta["chat_id"] == "3e3487f2"


def test_body_is_verbatim():
    src = _src("deepseek/deepseek_09_nextcloudobsidian_d78923c8.md")
    body = PROC.body(src)
    assert body == src.abs_path.read_text(encoding="utf-8")
    assert "Nextcloud+Obsidian" in body


def test_media_refs_are_relative():
    refs = PROC.media_refs(_src("deepseek/deepseek_09_nextcloudobsidian_d78923c8.md"))
    assert refs == ["media/01010101_abc.png"]


def test_service_fallback_to_folder():
    from pathlib import Path as P
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        folder = P(d)
        raw_folder = folder / "qwen"
        raw_folder.mkdir()
        f = raw_folder / "qwen_01_abc.md"
        f.write_text("# Тема\n\n#### 👤 Вы\nпривет", encoding="utf-8")
        src = SourceFile(abs_path=f, rel_path="qwen/qwen_01_abc.md", ext=".md")
        meta = PROC.parse(src)
        assert meta["service"] == "qwen"
        assert meta["branches"] == 1


def test_parses_structural_metadata():
    meta = PROC.parse(_src("deepseek/deepseek_09_nextcloudobsidian_d78923c8.md"))
    assert meta["schema_version"] == 2
    assert meta["roles"] == ["assistant", "user"]
    assert len(meta["message_index"]) == 4
    assert meta["message_index"][0] == {"role": "user", "ts": "2026-07-01 12:00"}
    assert meta["message_index"][-1] == {"role": "assistant", "ts": "2026-07-01 12:06"}
    assert meta["first_ts"] == "2026-07-01 12:00"
    assert meta["last_ts"] == "2026-07-01 12:06"
    assert meta["links"] == 1  # the "Оригинал:" URL
    assert meta["attachments"] == 2  # header value wins


def test_message_index_ignores_noise_and_counts_blocks():
    from pathlib import Path as P
    import tempfile

    body = (
        "# Тема\n\n"
        "#### 👤 Вы (2026-07-01 12:00)\n"
        "hello\n\n"
        "#### 🤖 AI\n"
        "```python\n"
        "print(1)\n"
        "```\n"
        "> цитата\n"
        "> > вложенная\n\n"
        "#### UI:\n"
        "не сообщение\n\n"
        "#### 👤 Вы (2026-07-01 12:01)\n"
        "https://example.com/а\n"
        "https://example.com/а\n"
    )
    with tempfile.TemporaryDirectory() as d:
        f = P(d) / "t.md"
        f.write_text(body, encoding="utf-8")
        src = SourceFile(abs_path=f, rel_path="t.md", ext=".md")
        meta = PROC.parse(src)
        assert [m["role"] for m in meta["message_index"]] == ["user", "assistant", "user"]
        assert meta["roles"] == ["assistant", "user"]
        assert meta["code_blocks"] == 1
        assert meta["quotes"] == 2
        assert meta["links"] == 1  # deduplicated
        assert meta["first_ts"] == "2026-07-01 12:00"
        assert meta["last_ts"] == "2026-07-01 12:01"