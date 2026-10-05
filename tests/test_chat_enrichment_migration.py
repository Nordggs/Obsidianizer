"""TICKET-009 — identity migration across Enrichment / ChatIndex / topics.

The single Enrichment→Source link is the frontmatter ``source_id`` written by
the pipeline (008A) from the SourceIndex. ChatIndex and topic membership key
on that id; ``rel`` stays only the read/display address. These tests pin the
migration contract (not the internal representation).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from obsidianizer.chat_detect import make_chat_filter
from obsidianizer.config import Settings
from obsidianizer.emit import atomic_write
from obsidianizer.enrich import build_card, build_frontmatter, compose
from obsidianizer.md_processor import MdProcessor
from obsidianizer.nav_index import frontmatter_of
from obsidianizer.pipeline import run as run_pipeline
from obsidianizer.registry import ProcessorRegistry
from obsidianizer.search import ChatIndex
from obsidianizer.source_index import make_source_id
from obsidianizer.topics import create_topic, get_topic, list_topics, topic_hash_of

FIXTURES = Path(__file__).parent / "fixtures" / "md"
NS = "test-ns"
CHAT_REL = "deepseek/deepseek_09_nextcloudobsidian_d78923c8.md"
CHAT2_REL = "chatgpt/chatgpt_00_в4--оригами-держатель-для_3e3487f2.md"


class _LLM:
    def analyze_topic(self, payload):  # noqa: ARG002
        return {"name": "Тема", "summary": "s", "decisions": [], "key_facts": [], "artifacts": []}


def _settings(source: Path, target: Path) -> Settings:
    s = Settings()
    s.source = source
    s.target = target
    s.enriched = target.parent / "enriched"
    s.llm_enabled = False
    s.source_namespace = NS
    return s


def _run(source: Path, target: Path):
    reg = ProcessorRegistry(filter_fn=make_chat_filter(chat_only=True))
    reg.register(".md", MdProcessor)
    settings = _settings(source, target)
    return run_pipeline(reg, settings), settings


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    target = tmp_path / "processed"
    shutil.copytree(FIXTURES, source)
    return source, target


def _index(target: Path) -> ChatIndex:
    return ChatIndex(target, target.parent / "enriched" / ".obsidianizer", llm=None, embed_model="")


# ── one id across every layer ───────────────────────────────────────────────


def test_single_source_id_across_pipeline_search_topics(tmp_path):
    source, target = _prepare(tmp_path)
    report, _ = _run(source, target)
    assert report.processed == 2

    expected = make_source_id(NS, CHAT_REL)
    meta = frontmatter_of(target / CHAT_REL)
    assert meta["source_id"] == expected  # pipeline writes the Index id

    idx = _index(target)
    idx.refresh()
    res = idx.search("Nextcloud Obsidian")
    assert res
    assert any(c.source_id == expected for c in res)

    enriched = target.parent / "enriched"
    create_topic(target, enriched, [CHAT_REL], _LLM())
    topic = get_topic(enriched, list_topics(enriched)[0]["topic_id"])
    assert topic["source_ids"] == [expected]


def test_rel_is_address_not_identity(tmp_path):
    source, target = _prepare(tmp_path)
    _run(source, target)
    old_id = make_source_id(NS, CHAT_REL)

    # Move the produced note to a new rel (address changes, id does not).
    moved_rel = "renamed/deepseek_09_nextcloudobsidian_d78923c8.md"
    moved = target / moved_rel
    moved.parent.mkdir(parents=True)
    shutil.move(target / CHAT_REL, moved)

    assert frontmatter_of(moved)["source_id"] == old_id

    idx = _index(target)
    idx.refresh()
    res = idx.search("Nextcloud Obsidian")
    assert res
    top = res[0]
    assert top.source_id == old_id  # identity survives the address change
    assert top.rel == moved_rel  # rel is only the current address


def test_topic_membership_is_source_id_list(tmp_path):
    source, target = _prepare(tmp_path)
    _run(source, target)
    enriched = target.parent / "enriched"
    create_topic(target, enriched, [CHAT_REL, CHAT2_REL], _LLM())

    topic = list_topics(enriched)[0]
    assert set(topic["source_ids"]) == {
        make_source_id(NS, CHAT_REL),
        make_source_id(NS, CHAT2_REL),
    }
    # rels remain available as the display/address projection
    assert set(topic["chats"]) == {CHAT_REL, CHAT2_REL}


def test_topic_hash_tracks_content_not_identity():
    # topic_hash is a version marker over source_hash — independent of ids and
    # order, and it changes when the underlying content hash changes.
    a = [{"meta": {"source_hash": "A"}}, {"meta": {"source_hash": "B"}}]
    b = [{"meta": {"source_hash": "A"}}, {"meta": {"source_hash": "C"}}]
    assert topic_hash_of(a) == topic_hash_of(list(reversed(a)))
    assert topic_hash_of(a) != topic_hash_of(b)


# ── notes without an id: skip, never collide ────────────────────────────────


def _note_without_id(root: Path, rel: str, source_hash: str, body: str) -> None:
    meta = {"title": rel, "service": "test", "messages": {"total": 1}}
    atomic_write(
        root / rel,
        compose(build_frontmatter(meta, "", [], source_hash), build_card(meta, "", []), body),
    )


def test_idless_notes_skipped_without_none_collision(tmp_path):
    processed = tmp_path / "processed"
    _note_without_id(processed, "a.md", "H1", "хостинг hetzner")
    _note_without_id(processed, "b.md", "H2", "хостинг wireguard")

    idx = ChatIndex(processed, tmp_path / "idx", llm=None, embed_model="")
    stats = idx.refresh()

    assert stats["added"] == 0
    assert stats["skipped_no_id"] == 2  # no ``None`` key collision, both skipped
    assert idx.search("хостинг") == []
