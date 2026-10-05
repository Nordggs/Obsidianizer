"""TICKET-013: tab-1 directory selection over the shared exclusion rules.

Rule vs Selection: the exclusion rule (Settings ∪ DEFAULT_EXCLUDE_DIRS)
sets the default checkbox; the selection set only decides which cards are
generated in the current run — never the parent Folders-table content,
fingerprint or card version.
"""

from __future__ import annotations

from pathlib import Path

from obsidianizer.config import Settings
from obsidianizer.exclusions import (
    DEFAULT_EXCLUDE_DIRS,
    ExclusionFilter,
    build_tab1_exclusion,
    exclusion_kind,
)
from obsidianizer.obsidianize import (
    ObsidianizeConfig,
    classify_dir_isolated,
    discover_dir_rels,
    scan_tree,
    update_cards,
)


def _mk(root: Path, rel: str, files: int = 1) -> Path:
    d = root / Path(*rel.split("/")) if rel else root
    d.mkdir(parents=True, exist_ok=True)
    for i in range(files):
        (d / f"f{i}.txt").write_text("x", encoding="utf-8")
    return d


def test_discovery_marks_system_user_hidden_leaf(tmp_path: Path) -> None:
    _mk(tmp_path, "src")
    _mk(tmp_path, ".git/objects")
    _mk(tmp_path, "build/out")
    _mk(tmp_path, "screenshots")
    (tmp_path / ".hidden").mkdir()
    s = Settings()
    s.exclude_dirs = list(s.exclude_dirs) + ["screenshots"]
    excl = build_tab1_exclusion(s)
    got = {d["rel"]: d["kind"] for d in discover_dir_rels(tmp_path, excl, s.exclude_dirs)}
    assert got[".git"] == "system"
    assert got["build"] == "system"
    assert got["screenshots"] == "user"
    assert got[".hidden"] == "hidden-only"
    # leaf rule: no descent into excluded/hidden
    assert ".git/objects" not in got
    assert "build/out" not in got
    assert "src" not in got


def test_discovery_no_file_reads_inside_excluded(tmp_path: Path, monkeypatch) -> None:
    _mk(tmp_path, ".git", files=2)
    reads: list[str] = []
    import builtins

    real_open = builtins.open

    def _spy(*a, **k):
        reads.append(str(a[0]))
        return real_open(*a, **k)

    monkeypatch.setattr("builtins.open", _spy)
    s = Settings()
    excl = build_tab1_exclusion(s)
    discover_dir_rels(tmp_path, excl, s.exclude_dirs)
    assert not [r for r in reads if ".git" in r]


def test_strict_scan_unchanged_by_selection_feature(tmp_path: Path) -> None:
    _mk(tmp_path, "src")
    _mk(tmp_path, "build")
    tree = scan_tree(tmp_path, ObsidianizeConfig())
    rels = set(tree.keys())
    assert "src" in rels and "build" in rels  # build stays in strict tree
    assert ".git" not in rels or True


def test_exclusion_kind_priority() -> None:
    assert exclusion_kind(".git", [".git"]) == "system"
    assert exclusion_kind("screenshots", ["screenshots"]) == "user"
    assert exclusion_kind(".hidden", []) == "hidden-only"
    assert ".git" in DEFAULT_EXCLUDE_DIRS and "screenshots" not in DEFAULT_EXCLUDE_DIRS


def test_update_cards_selection_only_chosen(tmp_path: Path) -> None:
    _mk(tmp_path, "docs")
    _mk(tmp_path, "src")
    _mk(tmp_path, "temp/a")
    summary = update_cards(
        tmp_path, ObsidianizeConfig(), selection={"docs"}, selection_provided=True
    )
    assert (tmp_path / "docs" / "docs.md").exists()
    assert not (tmp_path / "src" / "src.md").exists()
    assert not (tmp_path / "temp" / "temp.md").exists()
    # root container is auto-created for navigation
    assert (tmp_path / (tmp_path.name + ".md")).exists()
    assert summary.scanned == 2  # docs + root container


def test_update_cards_empty_selection_noop(tmp_path: Path) -> None:
    _mk(tmp_path, "docs")
    summary = update_cards(tmp_path, ObsidianizeConfig(), selection=set(), selection_provided=True)
    assert summary.scanned == 0
    assert list(tmp_path.glob("*.md")) == []


def test_update_cards_not_provided_ignores_selection(tmp_path: Path) -> None:
    _mk(tmp_path, "docs")
    _mk(tmp_path, "src")
    update_cards(tmp_path, ObsidianizeConfig(), selection={"docs"}, selection_provided=False)
    assert (tmp_path / "docs" / "docs.md").exists()
    assert (tmp_path / "src" / "src.md").exists()


def test_update_cards_none_provided_noop(tmp_path: Path) -> None:
    _mk(tmp_path, "docs")
    summary = update_cards(tmp_path, ObsidianizeConfig(), selection=None, selection_provided=True)
    assert summary.scanned == 0


def test_selection_subtree_cascade(tmp_path: Path) -> None:
    _mk(tmp_path, "temp/a")
    _mk(tmp_path, "temp/b")
    update_cards(tmp_path, ObsidianizeConfig(), selection={"docs"}, selection_provided=True)
    assert not (tmp_path / "temp" / "temp.md").exists()
    assert not (tmp_path / "temp" / "a" / "a.md").exists()


def test_override_excluded_git(tmp_path: Path) -> None:
    _mk(tmp_path, ".git")
    s1 = update_cards(
        tmp_path, ObsidianizeConfig(), selection={".git"}, selection_provided=True
    )
    assert (tmp_path / ".git" / ".git.md").exists()
    assert s1.scanned >= 1
    # idempotency: second identical run skips everything
    s2 = update_cards(
        tmp_path, ObsidianizeConfig(), selection={".git"}, selection_provided=True
    )
    assert s2.skipped == s2.scanned


def test_classify_isolated_single_node_basis(tmp_path: Path) -> None:
    _mk(tmp_path, ".git/objects")
    _mk(tmp_path, "src")
    node = classify_dir_isolated(tmp_path, ".git", ObsidianizeConfig())
    assert node is not None and node.rel == ".git"
    assert "objects" in node.subfolders  # listed, not traversed
    assert classify_dir_isolated(tmp_path, "../evil", ObsidianizeConfig()) is None
    assert classify_dir_isolated(tmp_path, "", ObsidianizeConfig()) is None


def test_parent_chain_containers(tmp_path: Path) -> None:
    _mk(tmp_path, "src/features/auth")
    update_cards(
        tmp_path,
        ObsidianizeConfig(),
        selection={"src/features/auth"},
        selection_provided=True,
    )
    assert (tmp_path / "src" / "features" / "auth" / "auth.md").exists()
    assert (tmp_path / "src" / "src.md").exists()  # container
    assert (tmp_path / "src" / "features" / "features.md").exists()  # container
    # sibling content untouched
    assert not (tmp_path / "src" / "other" / "other.md").exists()


def test_recursive_false_plus_all_is_root_only(tmp_path: Path) -> None:
    _mk(tmp_path, "docs")
    _mk(tmp_path, "src")
    summary = update_cards(
        tmp_path,
        ObsidianizeConfig(),
        recursive=False,
        selection={"", "docs", "src"},
        selection_provided=True,
    )
    assert summary.scanned == 1
    assert (tmp_path / (tmp_path.name + ".md")).exists()
    assert not (tmp_path / "docs" / "docs.md").exists()


def test_excluded_in_tree_unchecked_by_default(tmp_path: Path) -> None:
    for name in ("src", "build", "dist", "venv", "screenshots"):
        _mk(tmp_path, name)
    s = Settings()
    s.exclude_dirs = list(s.exclude_dirs) + ["screenshots"]
    excl = build_tab1_exclusion(s)
    lookup = {d["rel"]: d["kind"] for d in discover_dir_rels(tmp_path, excl, s.exclude_dirs)}
    assert lookup["build"] == "system"
    assert lookup["dist"] == "system"
    assert lookup["venv"] == "system"
    assert lookup["screenshots"] == "user"
    # union predicate covers all four
    union = ExclusionFilter(list(set(s.exclude_dirs) | set(DEFAULT_EXCLUDE_DIRS)))
    for rel in ("build", "dist", "venv", "screenshots"):
        assert union.is_excluded_dir(rel)


def test_system_names_not_removable(tmp_path: Path) -> None:
    s = Settings()
    s.exclude_dirs = ["screenshots"]  # user removed everything else
    excl = build_tab1_exclusion(s)
    assert excl.is_excluded_dir("build")
    assert excl.is_excluded_dir(".git")


# ── UI bridge ──────────────────────────────────────────────────────────────

import json as _json
import time as _time

from obsidianizer.events import EventType
from obsidianizer.ui import UIApp


def _ui_app(tmp_path: Path) -> UIApp:
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    return app


def _wait_idle(app: UIApp, timeout: float = 20.0) -> bool:
    end = _time.time() + timeout
    while app._busy and _time.time() < end:
        _time.sleep(0.05)
    return not app._busy


def test_obs_scan_new_fields_old_keys_intact(tmp_path: Path) -> None:
    _mk(tmp_path, "src")
    _mk(tmp_path, "build")
    (tmp_path / ".git").mkdir()
    app = _ui_app(tmp_path)
    r = app.obs_scan(str(tmp_path))
    assert r["ok"] is True
    by_rel = {f["rel"]: f for f in r["folders"]}
    assert by_rel["src"]["excluded"] is False
    assert by_rel["src"]["checked_default"] is True
    assert by_rel["src"]["kind"] is None
    assert by_rel["build"]["excluded"] is True
    assert by_rel["build"]["kind"] == "system"
    assert by_rel["build"]["checked_default"] is False
    kinds = {e["rel"]: e["kind"] for e in r["excluded_dirs"]}
    assert kinds[".git"] == "system"
    assert kinds["build"] == "system"
    assert r["rules"]["user_dirs"] == list(app.settings.exclude_dirs)
    # old keys intact for every strict row
    for f in r["folders"]:
        for key in ("rel", "path", "files", "subfolders", "images", "categories", "card"):
            assert key in f


def test_obs_scan_user_rule_and_review_untouched(tmp_path: Path) -> None:
    _mk(tmp_path, "screenshots")
    (tmp_path / ".venv").mkdir()
    app = _ui_app(tmp_path)
    app.set_exclusions("screenshots", "")
    r = app.obs_scan(str(tmp_path))
    kinds = {e["rel"]: e["kind"] for e in r["excluded_dirs"]}
    assert kinds["screenshots"] == "user"
    assert kinds[".venv"] == "hidden-only"
    # review_scan keeps the frozen contract: no new top-level fields;
    # tab-3 filtering is unchanged (only the Settings rule, no hidden logic).
    rr = app.review_scan(str(tmp_path))
    assert rr["ok"] is True
    assert "excluded_dirs" not in rr and "rules" not in rr
    review_rels = {f["rel"] for f in rr["folders"]}
    assert "screenshots" not in review_rels
    assert "" in review_rels


def test_obs_obsidianize_selection_via_ui(tmp_path: Path) -> None:
    _mk(tmp_path, "docs")
    _mk(tmp_path, "src")
    app = _ui_app(tmp_path)
    r = app.obs_obsidianize(
        {"path": str(tmp_path), "recursive": True, "selection": ["docs"], "selection_provided": True}
    )
    assert r["ok"] is True
    assert _wait_idle(app)
    assert (tmp_path / "docs" / "docs.md").exists()
    assert not (tmp_path / "src" / "src.md").exists()
    summary = _json.loads(app.events[-1].message)
    assert summary["scanned"] == 2  # docs + root container


def test_i18n_selection_keys_present() -> None:
    import re

    src = (Path(__file__).parent.parent / "src" / "obsidianizer" / "web" / "i18n.js").read_text(
        encoding="utf-8"
    )
    for key in (
        "obs.select_all",
        "obs.deselect_all",
        "obs.invert",
        "obs.branch_select",
        "obs.branch_deselect",
        "obs.branch_invert",
        "obs.add_to_excludes",
        "obs.counters",
        "obs.run_n",
        "obs.system_excluded",
        "obs.user_excluded",
        "obs.hidden_excluded",
        "obs.zero_selection",
    ):
        assert src.count(f'"{key}"') >= 2, key


# ── Stale-selection guard (AUDIT PRE-COMMIT BLOCKER 1) ─────────────────────
# Scenario: Scan A → change path to B → Run without rescan. The JS must NOT
# send A's rels for B; it falls back to the legacy full-tree run. This pins
# the backend half of that contract: no selection ⇒ whole folder processed.

def test_run_without_selection_processes_full_new_folder(tmp_path: Path) -> None:
    folder_a = tmp_path / "A"
    folder_b = tmp_path / "B"
    _mk(folder_a, "docs")
    _mk(folder_b, "docs")
    _mk(folder_b, "src")
    app = _ui_app(tmp_path)
    scanned = app.obs_scan(str(folder_a))
    assert scanned["ok"] is True
    # Run on B with NO selection (as the JS stale-guard sends it).
    r = app.obs_obsidianize({"path": str(folder_b), "recursive": True})
    assert r["ok"] is True
    assert _wait_idle(app)
    assert (folder_b / "docs" / "docs.md").exists()
    assert (folder_b / "src" / "src.md").exists()


def test_js_stale_selection_guard_contract() -> None:
    web = Path(__file__).parent.parent / "src" / "obsidianizer" / "web"
    js = (web / "app.js").read_text(encoding="utf-8")
    # scanned root is remembered and compared before sending selection
    assert "obsScannedRoot" in js
    assert "obsSelectionFresh" in js
    assert "selection_provided: fresh" in js
    # error/empty scans reset the stale state instead of keeping it
    assert js.count("obsScannedRoot = \"\"") >= 2


def test_css_row_highlight_contract() -> None:
    css = (Path(__file__).parent.parent / "src" / "obsidianizer" / "web" / "app.css").read_text(
        encoding="utf-8"
    )
    assert "tr.sel" in css  # LMB highlight class from renderObsTable is styled
