"""Topic builder tests — merging several chats into one topic note.

The topic stage reads selected processed notes, runs a single LLM call over the
assembled payload and writes ``enriched/topics/<Name>.md``. Incrementality is
driven by ``topic_hash`` (a hash of the selected chats' ``source_hash`` values).
The auto-grouping stage (``group_all``) clusters the whole collection the same
way without a manual selection.
"""

import re
from pathlib import Path

from obsidianizer.emit import atomic_write
from obsidianizer.enrich import build_card, build_frontmatter, compose
from obsidianizer.llm import parse_topic_map, parse_topic_response
from obsidianizer.topics import (
    TOPIC_LIMIT_CHARS,
    build_map_payload,
    build_payload,
    build_topic_doc,
    build_topic_map,
    chats_without_topic,
    collect_catalog,
    create_topic,
    delete_topic,
    get_topic,
    group_all,
    list_topics,
    rename_topic,
    sanitize_name,
    topic_hash_of,
    update_topic,
)


class _FakeTopicLLM:
    def __init__(self, result=None, *, fail=False):
        self.result = result if result is not None else {
            "name": "Архитектура Obsidianizer",
            "summary": "В разговорах обсуждалась архитектура проекта и AI-этап.",
            "decisions": ["Двухэтапный pipeline", "Хранить AI-результат отдельно"],
            "key_facts": ["source_hash → ai_hash"],
            "artifacts": ["pipeline.py", "config.py"],
        }
        self.fail = fail
        self.calls = 0

    def analyze_topic(self, payload):  # noqa: ARG002
        self.calls += 1
        if self.fail:
            return {
                "name": "",
                "summary": "",
                "decisions": [],
                "key_facts": [],
                "artifacts": [],
            }
        return dict(self.result)


def _note(root: Path, rel: str, source_hash: str, body: str = "тело заметки", service: str = "test", title: str | None = None):
    meta = {
        "title": title or rel,
        "service": service,
        "messages": {"total": 4, "user": 2, "assistant": 2},
        "branches": 1,
    }
    atomic_write(
        root / rel,
        compose(build_frontmatter(meta, "", [], source_hash), build_card(meta, "", []), body),
    )


# ── response parsing (llm contract) ────────────────────────────────────────


def test_parse_topic_response_full():
    text = (
        "NAME: Архитектура\n\n"
        "SUMMARY: Обсуждали архитектуру.\n\n"
        "DECISIONS:\n- первое\n- второе\n\n"
        "KEY_FACTS:\n- факт\n\n"
        "ARTIFACTS:\n- a.py\n- b.py\n"
    )
    r = parse_topic_response(text)
    assert r["name"] == "Архитектура"
    assert r["summary"] == "Обсуждали архитектуру."
    assert r["decisions"] == ["первое", "второе"]
    assert r["key_facts"] == ["факт"]
    assert r["artifacts"] == ["a.py", "b.py"]


def test_parse_topic_response_degrades_on_missing_blocks():
    r = parse_topic_response("мусор без контракта")
    assert r == {"name": "", "summary": "", "decisions": [], "key_facts": [], "artifacts": []}


# ── sanitize / hashing ──────────────────────────────────────────────────────


def test_sanitize_name_strips_invalid_characters():
    assert sanitize_name('Архитектура / Obsidianizer: v2?') == "Архитектура Obsidianizer v2"


def test_sanitize_name_falls_back_to_tema():
    assert sanitize_name("") == "Тема"
    assert sanitize_name("   ") == "Тема"


def test_sanitize_name_truncates_long_names():
    assert len(sanitize_name("а" * 200)) <= 40


def test_topic_hash_stable_and_order_independent():
    chats = [{"meta": {"source_hash": "A"}}, {"meta": {"source_hash": "B"}}]
    assert topic_hash_of(chats) == topic_hash_of(list(reversed(chats)))


def test_topic_hash_changes_with_selection():
    a = [{"meta": {"source_hash": "A"}}, {"meta": {"source_hash": "B"}}]
    b = [{"meta": {"source_hash": "A"}}, {"meta": {"source_hash": "C"}}]
    assert topic_hash_of(a) != topic_hash_of(b)


# ── payload assembly ────────────────────────────────────────────────────────


def test_build_payload_includes_every_chat():
    chats = [
        {"rel": "chatgpt/a.md", "meta": {"title": "Первый", "service": "chatgpt", "first_ts": "2026-01-01T10:00:00"}, "body": "разговор про AI"},
        {"rel": "deepseek/b.md", "meta": {"title": "Второй", "service": "deepseek", "first_ts": "2026-02-02T10:00:00"}, "body": "разговор про экспорт"},
    ]
    payload = build_payload(chats, limit_chars=100000)
    assert "## Файл" in payload
    assert "**Название:** Первый" in payload
    assert "**Источник:** chatgpt" in payload
    assert "**Дата:** 2026-01-01" in payload
    assert "разговор про экспорт" in payload


def test_build_payload_respects_global_budget():
    chats = [{"rel": "a.md", "meta": {"title": "A", "service": "s", "first_ts": ""}, "body": "x" * 2000}]
    payload = build_payload(chats, limit_chars=600)
    assert len(payload) <= 600 + 400  # header overhead tolerance


# ── document composition ────────────────────────────────────────────────────


def test_build_topic_doc_has_blocks_and_source_links():
    chats = [
        {"rel": "chatgpt/a.md", "meta": {"title": "Первый чат", "service": "chatgpt", "first_ts": "2026-01-01T10:00:00", "messages": {"total": 7}}, "body": ""},
        {"rel": "deepseek/b.md", "meta": {"title": "Второй чат", "service": "deepseek", "first_ts": "2026-01-02T10:00:00", "messages": {"total": 3}}, "body": ""},
    ]
    result = {
        "name": "Архитектура",
        "summary": "Суть темы.",
        "decisions": ["Первое решение"],
        "key_facts": ["Факт"],
        "artifacts": [],
    }
    doc = build_topic_doc("Архитектура", result, chats, "HASH123")
    assert "type: topic" in doc
    assert "topic_hash: HASH123" in doc
    assert "## Суть" in doc and "Суть темы." in doc
    assert "## Решения" in doc and "- Первое решение" in doc
    assert "## Ключевые факты" in doc and "- Факт" in doc
    assert "## Артефакты" not in doc, "empty artifacts block must be omitted"
    assert "### chatgpt" in doc
    assert "[[chatgpt/a|Первый чат]]" in doc
    assert "7 сообщений" in doc
    assert "[[deepseek/b|Второй чат]]" in doc


def test_build_topic_doc_filters_no_placeholder():
    result = {"name": "Т", "summary": "S", "decisions": ["- Нет", "решение"], "key_facts": [], "artifacts": []}
    doc = build_topic_doc("Т", result, [], "H")
    assert "- Нет" not in doc
    assert "- решение" in doc


# ── create_topic ────────────────────────────────────────────────────────────


def test_create_topic_writes_topic_note(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "chatgpt/a.md", "H1", service="chatgpt", title="Первый")
    _note(processed, "deepseek/b.md", "H2", service="deepseek", title="Второй")

    report = create_topic(
        processed, enriched, ["chatgpt/a.md", "deepseek/b.md"], _FakeTopicLLM()
    )

    assert report.created == "topics/Архитектура Obsidianizer.md"
    out = enriched / "topics" / "Архитектура Obsidianizer.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "topic_hash:" in text
    assert "chats:" in text
    assert "chatgpt/a.md" in text
    assert "[[chatgpt/a|Первый]]" in text
    assert "Первый" in text and "Второй" in text
    assert "pipeline.py" in text


def test_create_topic_leaves_processed_untouched(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    before = (processed / "a.md").read_text(encoding="utf-8")

    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    assert (processed / "a.md").read_text(encoding="utf-8") == before


def test_create_topic_skips_unchanged_selection(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")

    llm = _FakeTopicLLM()
    first = create_topic(processed, enriched, ["a.md", "b.md"], llm)
    assert first.created

    second = create_topic(processed, enriched, ["a.md", "b.md"], llm)

    assert second.skipped is True
    assert llm.calls == 1, "unchanged selection must not call the LLM again"


def test_create_topic_regenerates_on_changed_selection(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")

    llm = _FakeTopicLLM()
    create_topic(processed, enriched, ["a.md", "b.md"], llm)
    create_topic(processed, enriched, ["a.md"], llm)

    assert llm.calls == 2, "changed selection must re-run the analysis"


def test_create_topic_fails_without_readable_chats(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    atomic_write(processed / "foreign.md", "no frontmatter here\n---\ntext")

    report = create_topic(processed, enriched, ["foreign.md"], _FakeTopicLLM())

    assert report.critical_error == "Нет читаемых чатов для объединения"
    assert not (enriched / "topics").exists()


def test_create_topic_rejects_empty_selection(tmp_path):
    report = create_topic(tmp_path / "p", tmp_path / "e", [], _FakeTopicLLM())
    assert report.critical_error == "Не выбраны файлы"


def test_create_topic_rejects_path_traversal(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")

    report = create_topic(processed, enriched, ["../secret.md"], _FakeTopicLLM())

    assert report.critical_error == "Нет читаемых чатов для объединения"


def test_create_topic_empty_response_records_failure(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")

    report = create_topic(processed, enriched, ["a.md"], _FakeTopicLLM(fail=True))

    assert report.failed == ["пустой ответ модели"]
    assert not (enriched / "topics").exists()


def test_create_topic_rebuilds_index(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")

    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    index = enriched / "_index.md"
    assert index.is_file()
    assert "Индекс" in index.read_text(encoding="utf-8")


# ── topic lifecycle ─────────────────────────────────────────────────────────


def test_create_topic_writes_topic_id(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")

    report = create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    assert report.created == "topics/Архитектура Obsidianizer.md"
    text = (enriched / "topics" / "Архитектура Obsidianizer.md").read_text(encoding="utf-8")
    match = re.search(r"topic_id:\s*(\w+)", text)
    assert match and len(match.group(1)) == 12


def test_list_topics_returns_created_topic(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    topics = list_topics(enriched)

    assert len(topics) == 1
    assert topics[0]["name"] == "Архитектура Obsidianizer"
    assert topics[0]["chats"] == ["a.md"]
    assert topics[0]["file"] == "Архитектура Obsidianizer.md"
    assert topics[0]["topic_id"]


def test_list_topics_migrates_legacy_topic(tmp_path):
    enriched = tmp_path / "enriched"
    topic_dir = enriched / "topics"
    topic_dir.mkdir(parents=True)
    legacy = topic_dir / "Старая.md"
    legacy.write_text(
        "---\ntype: topic\ntitle: Старая\nchats:\n- a.md\n---\n\n# Старая\n",
        encoding="utf-8",
    )

    topics = list_topics(enriched)

    assert len(topics) == 1
    assert topics[0]["name"] == "Старая"
    assert topics[0]["topic_id"]
    assert "topic_id" in legacy.read_text(encoding="utf-8")


def test_get_topic_returns_body_and_metadata(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    topic_id = list_topics(enriched)[0]["topic_id"]
    topic = get_topic(enriched, topic_id)

    assert topic is not None
    assert topic["name"] == "Архитектура Obsidianizer"
    assert topic["chats"] == ["a.md"]
    assert "## Суть" in topic["body"]


def test_update_topic_keeps_name_id_and_regenerates(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1", body="тело A")
    _note(processed, "b.md", "H2", body="тело B")
    llm = _FakeTopicLLM()

    create_topic(processed, enriched, ["a.md", "b.md"], llm)
    topic_id = list_topics(enriched)[0]["topic_id"]

    report = update_topic(enriched, processed, topic_id, ["a.md"], llm)

    assert report.updated is True
    assert report.name == "Архитектура Obsidianizer"
    assert llm.calls == 2
    out = enriched / "topics" / "Архитектура Obsidianizer.md"
    text = out.read_text(encoding="utf-8")
    assert "topic_id:" in text and topic_id in text
    assert "chats:" in text and "a.md" in text
    assert "b.md" not in text, "old selection member must leave the topic"
    assert "updated:" in text


def test_update_topic_skips_unchanged_selection(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")
    llm = _FakeTopicLLM()

    create_topic(processed, enriched, ["a.md", "b.md"], llm)
    topic_id = list_topics(enriched)[0]["topic_id"]

    report = update_topic(enriched, processed, topic_id, ["a.md", "b.md"], llm)

    assert report.skipped is True
    assert llm.calls == 1, "unchanged selection must not call the LLM again"


def test_update_topic_unknown_id(tmp_path):
    report = update_topic(tmp_path / "e", tmp_path / "p", "nope", ["a.md"], _FakeTopicLLM())
    assert report.critical_error == "Тема nope не найдена"


def test_rename_topic_preserves_topic_id(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())
    topic_id = list_topics(enriched)[0]["topic_id"]
    old = enriched / "topics" / "Архитектура Obsidianizer.md"

    result = rename_topic(enriched, topic_id, "Новое имя темы")

    assert result["ok"] is True
    assert result["name"] == "Новое имя темы"
    assert not old.exists()
    new = enriched / "topics" / "Новое имя темы.md"
    assert new.is_file()
    text = new.read_text(encoding="utf-8")
    assert f"topic_id: {topic_id}" in text
    assert "title: Новое имя темы" in text


def test_rename_topic_conflicts(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())
    # a second topic with its own name/topic_id
    (enriched / "topics").mkdir(parents=True, exist_ok=True)
    atomic_write(
        enriched / "topics" / "Другая.md",
        build_topic_doc(
            "Другая",
            {"summary": "s"},
            [{"rel": "a.md", "meta": {"service": "test", "title": "Чат"}}],
            "H",
            topic_id="otherid123456",
        ),
    )

    result = rename_topic(enriched, "otherid123456", "Архитектура Obsidianizer")

    assert result["ok"] is False
    assert "уже существует" in result["error"]
    assert (enriched / "topics" / "Другая.md").exists(), "conflict must not rename"


def test_delete_topic_removes_note(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())
    topic_id = list_topics(enriched)[0]["topic_id"]
    note = enriched / "topics" / "Архитектура Obsidianizer.md"

    result = delete_topic(enriched, topic_id)

    assert result["ok"] is True
    assert not note.exists()
    assert list_topics(enriched) == []


def test_delete_topic_unknown_id(tmp_path):
    result = delete_topic(tmp_path / "e", "nope")
    assert result["ok"] is False
    assert "не найдена" in result["error"]


def test_chats_without_topic_excludes_members(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1", title="В теме")
    _note(processed, "b.md", "H2", title="Без темы")
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    orphans = chats_without_topic(processed, enriched)

    assert [c["rel"] for c in orphans] == ["b.md"]


# ── auto-grouping (whole collection) ────────────────────────────────────────


class _FakeGroupLLM(_FakeTopicLLM):
    def __init__(self, map_groups=None, *, fail_map=False, **kwargs):
        super().__init__(**kwargs)
        self.map_groups = (
            map_groups
            if map_groups is not None
            else [{"name": "Тема 1", "ids": ["1", "2"]}, {"name": "Тема 2", "ids": ["3"]}]
        )
        self.fail_map = fail_map
        self.map_calls = 0
        self.map_payloads = []

    def analyze_topic_map(self, payload):  # noqa: ARG002
        self.map_calls += 1
        self.map_payloads.append(payload)
        if self.fail_map:
            return []
        return [dict(g) for g in self.map_groups]


class _ChunkingMapLLM(_FakeTopicLLM):
    """Puts every chat seen in a chunk into one topic named after the chunk."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.map_calls = 0
        self.map_payloads = []

    def analyze_topic_map(self, payload):
        self.map_calls += 1
        self.map_payloads.append(payload)
        ids = re.findall(r"\[(\d+)\]", payload)
        return [{"name": "Все вместе", "ids": ids}]


def test_parse_topic_map_full():
    text = "TOPIC: Архитектура\nIDS: 3, 7, 12\n\nTOPIC: Экспорт\nIDS: 1, 9"
    groups = parse_topic_map(text)
    assert groups == [
        {"name": "Архитектура", "ids": ["3", "7", "12"]},
        {"name": "Экспорт", "ids": ["1", "9"]},
    ]


def test_parse_topic_map_comma_space_or_semicolon_ids():
    groups = parse_topic_map("TOPIC: Т\nIDS: 1,2; 3")
    assert groups[0]["ids"] == ["1", "2", "3"]


def test_parse_topic_map_degrades_on_broken_contract():
    assert parse_topic_map("мусор") == []
    assert parse_topic_map("TOPIC: Без IDS") == []


def test_parse_topic_map_skips_empty_names():
    assert parse_topic_map("TOPIC:\nIDS: 1") == []


def test_build_map_payload_uses_global_indices_and_known_topics():
    chunk = [
        {"title": "Первый", "service": "chatgpt", "date": "2026-01-01", "snippet": "текст"},
        {"title": "Второй", "service": "deepseek", "date": "", "snippet": ""},
    ]
    payload = build_map_payload(chunk, 0, [])
    assert "[1] Первый (chatgpt, 2026-01-01) — текст" in payload
    assert "[2] Второй (deepseek)" in payload

    payload = build_map_payload(chunk, 5, ["Существующая"])
    assert "[6] Первый (chatgpt, 2026-01-01) — текст" in payload
    assert "Уже выделенные темы" in payload
    assert "- Существующая" in payload


def test_collect_catalog_lists_only_owned_notes(tmp_path):
    processed = tmp_path / "processed"
    _note(processed, "a.md", "H1", title="Первый")
    atomic_write(processed / "_index.md", "# Индекс")
    atomic_write(processed / "foreign.md", "no frontmatter\n---\ntext")

    catalog = collect_catalog(processed)

    assert len(catalog) == 1
    assert catalog[0]["rel"] == "a.md"
    assert catalog[0]["title"] == "Первый"


def test_build_topic_map_clusters_by_ids(tmp_path):
    processed = tmp_path / "processed"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")
    _note(processed, "c.md", "H3")

    clusters = build_topic_map(processed, _FakeGroupLLM())

    assert clusters == {"Тема 1": ["a.md", "b.md"], "Тема 2": ["c.md"]}


def test_build_topic_map_ignores_out_of_range_and_duplicate_ids(tmp_path):
    processed = tmp_path / "processed"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")
    llm = _FakeGroupLLM(
        map_groups=[
            {"name": "Первая", "ids": ["1", "99", "0", "-1"]},
            {"name": "Вторая", "ids": ["1", "2"]},
        ]
    )

    clusters = build_topic_map(processed, llm)

    assert clusters == {"Первая": ["a.md"], "Вторая": ["b.md"]}, (
        "a chat belongs to the first matching topic only"
    )


def test_build_topic_map_returns_empty_without_catalog(tmp_path):
    assert build_topic_map(tmp_path / "absent", _FakeGroupLLM()) == {}


def test_build_topic_map_cancel_returns_none(tmp_path):
    processed = tmp_path / "processed"
    _note(processed, "a.md", "H1")
    assert build_topic_map(processed, _FakeGroupLLM(), cancel_check=lambda: True) is None


def test_build_topic_map_chunks_large_collection(tmp_path):
    catalog = [
        {
            "rel": f"{i}.md",
            "title": f"Чат {i}",
            "service": "s",
            "date": "",
            "snippet": "x" * 500,
        }
        for i in range(25)
    ]
    llm = _ChunkingMapLLM()

    clusters = build_topic_map(tmp_path, llm, catalog=catalog)

    assert llm.map_calls == 3, "large collection must be chunked"
    assert "Уже выделенные темы" in llm.map_payloads[1], (
        "known topic names must be fed back into later chunks"
    )
    assert "- Все вместе" in llm.map_payloads[1]
    assert len(clusters["Все вместе"]) == 25


def test_group_all_creates_topics_and_skips_singletons(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")
    _note(processed, "c.md", "H3")

    report = group_all(processed, enriched, _FakeGroupLLM())

    assert len(report.created) == 1
    assert report.one_chat == 1
    assert report.total_chats == 3
    assert report.failed == []
    topic_file = enriched / "topics" / "Архитектура Obsidianizer.md"
    assert topic_file.is_file()
    text = topic_file.read_text(encoding="utf-8")
    assert "a.md" in text and "b.md" in text
    assert "c.md" not in text, "single-chat cluster must not become a topic"


def test_group_all_is_incremental(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")
    _note(processed, "c.md", "H3")
    llm = _FakeGroupLLM()

    first = group_all(processed, enriched, llm)
    second = group_all(processed, enriched, llm)

    assert len(first.created) == 1
    assert second.skipped == 1
    assert second.created == []
    assert len(llm.map_payloads) == 2, "auto-grouping re-runs the map pass"


def test_group_all_cancel_stops_without_topics(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")

    report = group_all(processed, enriched, _FakeGroupLLM(), cancel_check=lambda: True)

    assert report.cancelled is True
    assert report.created == []
    assert not (enriched / "topics").exists()


def test_group_all_empty_collection_is_critical(tmp_path):
    report = group_all(tmp_path / "absent", tmp_path / "enriched", _FakeGroupLLM())
    assert report.critical_error == "Нет обработанных чатов для авто-группировки"
    assert report.created == []


def test_group_all_never_deletes_existing_topics(tmp_path):
    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    _note(processed, "a.md", "H1")
    _note(processed, "b.md", "H2")
    _note(processed, "c.md", "H3")
    _note(processed, "d.md", "H4")
    group_all(processed, enriched, _FakeGroupLLM())
    before = sorted(p.name for p in (enriched / "topics").glob("*.md"))
    assert before, "a topic must exist before the re-run"

    report = group_all(
        processed, enriched, _FakeGroupLLM(map_groups=[{"name": "Другая", "ids": ["4"]}])
    )

    after = sorted(p.name for p in (enriched / "topics").glob("*.md"))
    assert after == before, "changed clustering must never delete prior topics"