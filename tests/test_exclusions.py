"""TICKET-006: Source boundary exclusions — predicate, prune, composition."""

from __future__ import annotations

from pathlib import Path

from obsidianizer.chat_detect import make_chat_filter
from obsidianizer.config import Settings
from obsidianizer.exclusions import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_EXCLUDE_MASKS,
    ExclusionFilter,
    build_exclusion,
    build_filter,
    parse_list,
)
from obsidianizer.registry import ProcessorRegistry
from obsidianizer.md_processor import MdProcessor


def test_defaults_match_ticket() -> None:
    assert ".git" in DEFAULT_EXCLUDE_DIRS
    assert "node_modules" in DEFAULT_EXCLUDE_DIRS
    assert ".obsidian" in DEFAULT_EXCLUDE_DIRS
    assert "*.tmp" in DEFAULT_EXCLUDE_MASKS
    assert "*.log" in DEFAULT_EXCLUDE_MASKS


def test_parse_list_csv_and_repeatable() -> None:
    assert parse_list(None) == []
    assert parse_list("a, b,,c") == ["a", "b", "c"]
    assert parse_list(["a", "b,c"]) == ["a", "b", "c"]


def test_predicate_table() -> None:
    f = ExclusionFilter([".git", "node_modules"], ["*.tmp", "*.bak", "*.log"])
    assert f.excluded(".git/x")
    assert f.excluded("src/.git")
    assert f.excluded("a/node_modules/b")
    assert f.excluded("a/b/.git/c.md")
    assert f.excluded("f.tmp")
    assert f.excluded("sub/f.bak")
    assert f.excluded("sub/f.log")
    # fnmatch boundaries
    assert not f.excluded("gitkeep.md")
    assert not f.excluded("notes.md")
    assert not f.excluded("a/b/c.md")
    # service allowlist is never cut by user masks
    fall = ExclusionFilter([], ["*.md"])
    assert not fall.excluded("_index.md")
    assert not fall.excluded(".obsidianizer-manifest.json")
    assert not fall.excluded(".obsidianizer/index.json")
    assert not fall.excluded("topics/Name.md")


def test_is_excluded_dir_and_prune() -> None:
    f = ExclusionFilter([".git", "node_modules"], [])
    assert f.is_excluded_dir(".git")
    assert f.is_excluded_dir("a/.git")
    assert f.is_excluded_dir("a/node_modules/b")
    assert not f.is_excluded_dir("src")
    assert not f.is_excluded_dir("topics")
    dirs = ["src", ".git", "node_modules", "docs"]
    f.prune_dirnames("", dirs)
    assert dirs == ["docs", "src"]


def test_build_filter_composition_with_chat(tmp_path: Path) -> None:
    chat = tmp_path / "chat.md"
    chat.write_text(
        "<!-- schema_version: 1 -->\n**Источник:** test\n#### 👤 Вы\nx\n",
        encoding="utf-8",
    )
    plain = tmp_path / "plain.md"
    plain.write_text("# just notes\n", encoding="utf-8")
    logged = tmp_path / "skip.log"
    logged.write_text("x", encoding="utf-8")

    s = Settings()
    s.chat_only = True
    s.exclude_dirs = []
    s.exclude_masks = ["*.log"]
    fn = build_filter(s, apply_chat=True)
    assert fn(chat, "chat.md", ".md") is True
    assert fn(plain, "plain.md", ".md") is False  # chat filter
    assert fn(logged, "skip.log", ".log") is False  # exclusion wins

    preview = build_filter(s, apply_chat=False)
    assert preview(plain, "plain.md", ".md") is True  # TICKET-004 protection
    assert preview(logged, "skip.log", ".log") is False


def test_build_exclusion_from_settings() -> None:
    s = Settings()
    f = build_exclusion(s)
    assert isinstance(f, ExclusionFilter)
    assert f.exclude_dirs == list(s.exclude_dirs)


def test_scan_integration_with_nested_excluded(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / ".git" / "nested").mkdir(parents=True)
    (src / ".git" / "nested" / "a.md").write_text("# x\n", encoding="utf-8")
    (src / "node_modules" / "pkg").mkdir(parents=True)
    (src / "node_modules" / "pkg" / "b.md").write_text("# x\n", encoding="utf-8")
    (src / "keep").mkdir(parents=True)
    (src / "keep" / "c.md").write_text("# keep\n", encoding="utf-8")
    (src / "keep" / "d.log").write_text("x", encoding="utf-8")

    s = Settings()
    s.chat_only = False
    reg = ProcessorRegistry(
        filter_fn=build_filter(s, apply_chat=False),
        exclusion_filter=build_exclusion(s),
    )
    reg.register(".md", MdProcessor)
    rels = sorted(c.rel_path for c in reg.scan(src))
    assert rels == ["keep/c.md"]


def test_make_chat_filter_still_usable_alone(tmp_path: Path) -> None:
    fn = make_chat_filter(chat_only=False)
    p = tmp_path / "x.md"
    p.write_text("# x\n", encoding="utf-8")
    assert fn(p, "x.md", ".md") is True


# ── TICKET-006 AC coverage ───────────────────────────────────────────────

CHAT_BODY = (
    "<!-- schema_version: 1 -->\n**Источник:** test\n**Stable ID:**\nabc\n"
    "<!-- hash: a1b2c3 title: t chat_order: 0 -->\n#### 👤 Вы\nпривет\n"
)


def _write(path: Path, text: str = CHAT_BODY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_settings_exclusions_roundtrip(tmp_path: Path) -> None:
    from obsidianizer.config import Settings as S

    cfg = tmp_path / "config.yml"
    s = S()
    s.exclude_dirs = [".git", "custom"]
    s.exclude_masks = ["*.tmp"]
    s.save(cfg)
    loaded = S.load(cfg)
    assert loaded.exclude_dirs == [".git", "custom"]
    assert loaded.exclude_masks == ["*.tmp"]
    # defaults survive a fresh load
    fresh = S()
    assert ".obsidian" in fresh.exclude_dirs
    assert "*.log" in fresh.exclude_masks


def test_cli_exclude_flags_extend_settings() -> None:
    from obsidianizer.cli import build_parser

    args = build_parser().parse_args(
        ["--exclude-dir", ".git,custom", "--exclude-dir", "extra",
         "--exclude-mask", "*.tmp"]
    )
    assert args.exclude_dir == [".git,custom", "extra"]
    assert args.exclude_mask == ["*.tmp"]


def test_index_hides_excluded_but_keeps_files(tmp_path: Path) -> None:
    from obsidianizer.nav_index import build_index_from_dir

    target = tmp_path / "t"
    _write(target / "keep.md", "---\ntitle: keep\n---\nbody\n")
    _write(target / "skip.tmp", "---\ntitle: skip\n---\nbody\n")
    f = ExclusionFilter([], ["*.tmp"])
    md = build_index_from_dir(target, f)
    assert "keep" in md
    assert "skip" not in md
    assert (target / "skip.tmp").exists()  # representation-only, no deletion


def test_collect_cards_and_catalog_skip_excluded(tmp_path: Path) -> None:
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter
    from obsidianizer.topics import collect_catalog, collect_chat_cards

    target = tmp_path / "processed"
    for rel in ("keep/a.md", ".git/b.md", "x/skip.md"):
        meta = {"title": rel, "service": "t", "messages": {"total": 1}}
        atomic_write(
            target / rel,
            build_frontmatter(meta, "", [], "h-" + rel)
            + "\n" + build_card(meta, "", []) + "\n---\nbody\n",
        )
    f = ExclusionFilter([".git"], ["skip.*"])
    cards = collect_chat_cards(target, f)
    catalog = collect_catalog(target, f)
    assert {c["rel"] for c in cards} == {"keep/a.md"}
    assert {c["rel"] for c in catalog} == {"keep/a.md"}
    # unfiltered legacy behaviour unchanged
    assert len(collect_chat_cards(target)) == 3


def test_create_topic_reports_excluded(tmp_path: Path) -> None:
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter
    from obsidianizer.topics import create_topic

    target = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    for rel in ("keep/a.md", "skip/b.tmp"):
        meta = {"title": rel, "service": "t", "messages": {"total": 1}}
        atomic_write(
            target / rel,
            build_frontmatter(meta, "", [], "h-" + rel, source_id="sid:" + rel)
            + "\n" + build_card(meta, "", []) + "\n---\nbody\n",
        )

    class _LLM:
        def analyze_topic(self, payload):
            return {"name": "T", "summary": "s", "decisions": [],
                    "key_facts": [], "artifacts": []}

    f = ExclusionFilter([], ["*.tmp"])
    report = create_topic(target, enriched, ["keep/a.md", "skip/b.tmp"], _LLM(),
                          exclusion_filter=f)
    assert report.excluded == ["skip/b.tmp"]
    assert report.created  # batch itself does not fail


def test_review_payload_predicate_before_read(tmp_path: Path) -> None:
    from obsidianizer.obsidianize import ObsidianizeConfig, scan_tree
    from obsidianizer.review import collect_payload

    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "notes.txt").write_text("секрет", encoding="utf-8")
    (root / "sub" / "debug.log").write_text("лог", encoding="utf-8")
    tree = scan_tree(root, ObsidianizeConfig())
    folder = tree["sub"]
    # default call without predicate behaves as before
    full = collect_payload(folder, include_text=False)
    assert any(f["name"] == "debug.log" for f in full["files"])
    # with predicate the masked file is neither listed nor read
    f = ExclusionFilter([], ["*.log"])
    payload = collect_payload(folder, include_text=True, exclusion_filter=f)
    assert all(f_["name"] != "debug.log" for f_ in payload["files"])
    assert all(t["name"] != "debug.log" for t in payload["texts"])


def test_review_scan_filters_and_keeps_contract(tmp_path: Path) -> None:
    from obsidianizer.ui import UIApp

    root = tmp_path / "rev"
    (root / "keep").mkdir(parents=True)
    (root / "keep" / "a.txt").write_text("x", encoding="utf-8")
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "b.txt").write_text("x", encoding="utf-8")
    app = UIApp()
    app.settings.obsidianize_dir = str(root)
    res = app.review_scan(str(root))
    assert res["ok"] is True
    rels = [f["rel"] for f in res["folders"]]
    assert "keep" in rels
    assert ".git" not in rels
    # contract with renderReviewFolders: rel/files/card present
    for f in res["folders"]:
        assert "rel" in f and "files" in f and "card" in f


def test_run_review_scan_js_contract() -> None:
    src = (Path(__file__).parent.parent / "src" / "obsidianizer"
           / "web" / "app.js").read_text(encoding="utf-8")
    idx = src.find("function runReviewScan()")
    assert idx >= 0
    block = src[idx: idx + 500]
    assert "review_scan" in block  # filtered path, not bare obs_scan
    assert "renderReviewFolders" in block


def test_pipeline_prune_retention_and_manifest_stable(tmp_path: Path) -> None:
    from obsidianizer.manifest import read_manifest, write_manifest
    from obsidianizer.pipeline import run as run_pipeline

    src = tmp_path / "src"
    tgt = tmp_path / "tgt"
    (src / "keep").mkdir(parents=True)
    _write(src / "keep" / "a.md")
    tgt.mkdir(parents=True)
    # previously imported derivative of an excluded path
    write_manifest(tgt, frozenset({"old/skip.tmp", "_index.md"}))
    (tgt / "old").mkdir(parents=True)
    (tgt / "old" / "skip.tmp").write_text("old derivative", encoding="utf-8")

    s = Settings()
    s.source = src
    s.target = tgt
    s.chat_only = False
    s.exclude_dirs = ["old"]
    reg = ProcessorRegistry(
        filter_fn=build_filter(s, apply_chat=False),
        exclusion_filter=build_exclusion(s),
    )
    reg.register(".md", MdProcessor)
    report = run_pipeline(reg, s, prune=True)
    assert report.failed == []
    # retention: excluded derivative survives prune
    assert (tgt / "old" / "skip.tmp").exists()
    manifest = read_manifest(tgt)
    assert "old/skip.tmp" in manifest
    # Source tree byte-identical (nothing inside excluded was touched)
    assert (src / "keep" / "a.md").read_text(encoding="utf-8") == CHAT_BODY


def test_postprocess_prune_retention(tmp_path: Path) -> None:
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter
    from obsidianizer.postprocess import enrich as enrich_fn

    class _LLM:
        def analyze(self, body):
            return {"summary": "s", "tags": [], "topic": "t", "type": "x"}

    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    meta = {"title": "a", "service": "t", "messages": {"total": 1}}
    atomic_write(
        processed / "keep" / "a.md",
        build_frontmatter(meta, "", [], "h-a") + "\n"
        + build_card(meta, "", []) + "\n---\nbody\n",
    )
    # stale enriched copy of an excluded rel
    atomic_write(
        enriched / "old" / "skip.tmp",
        build_frontmatter(meta, "s", [], "h-old") + "\n"
        + build_card(meta, "s", []) + "\n---\nbody\n",
    )
    # mark it owned
    em = (enriched / "old" / "skip.tmp").read_text(encoding="utf-8")
    (enriched / "old" / "skip.tmp").write_text(
        em.replace("summary:", "summary: s\nai_hash: h-old\n", 1)
        if "ai_hash" not in em else em, encoding="utf-8",
    )
    f = ExclusionFilter(["old"], [])
    report = enrich_fn(processed, enriched, _LLM(), prune=True,
                       exclusion_filter=f)
    assert report.failed == []
    assert (enriched / "old" / "skip.tmp").exists()


def test_gui_preview_keeps_non_chats_with_exclusions(tmp_path: Path) -> None:
    from obsidianizer.ui import UIApp

    src = tmp_path / "src"
    _write(src / "plain.md", "# обычные заметки\n")
    _write(src / "skip.tmp", "x")
    app = UIApp()
    app.settings.source = src
    app.settings.chat_only = True  # chat filter must NOT leak into preview
    res = app.scan()
    assert res["ok"] is True
    paths = [f["path"] for f in res["files"]]
    assert "plain.md" in paths  # TICKET-004 protection
    assert "skip.tmp" not in paths  # exclusions still apply
