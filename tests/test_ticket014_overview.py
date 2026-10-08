"""TICKET-014 — Processing: detection tree, sel_tree selection, overview.

Backend contracts (headless) plus static JS contracts:
- ``scan_tree()``: nested projection of the SourceIndex (folders-in-folders,
  empty folders kept, excluded absent, deterministic order, ``no_scan``).
- ``scan()`` carries the tree additively (legacy ``files``/``aggregate``
  shapes unchanged).
- ``overview_build()``: ``no_scan`` / ``stale_scan`` / whole-index /
  root-branch / subtree / exact-file / ``empty_selection`` / ``bad_output``;
  the card lands outside the source with a name distinct from
  ``<папка>_обзор.md``.
- ``overview`` builder: known patterns, content fallback, unknown dir is not
  an error, derived card files tracked as service, file tables, wikilinks,
  atomic write, source untouched.
- JS: ``doScan`` journals to the log; the ``sel_tree`` module exists and the
  import selection (TICKET-004) still reads ``#scanFileList`` only.
"""

from __future__ import annotations

from pathlib import Path

from obsidianizer.ui import UIApp

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "obsidianizer" / "web"
APP_HTML = WEB / "app.html"
APP_JS = WEB / "app.js"


def _app(tmp_path: Path) -> UIApp:
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"  # never write the repo config
    return app


def _project(tmp_path: Path) -> Path:
    src = tmp_path / "Project"
    (src / "src").mkdir(parents=True)
    (src / "tests").mkdir()
    (src / "docs" / "inner").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (src / "tests" / "test_main.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (src / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    (src / "README.md").write_text("# readme\n", encoding="utf-8")
    (src / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return src


def _scanned(tmp_path: Path) -> tuple[UIApp, Path]:
    src = _project(tmp_path)
    app = _app(tmp_path)
    app.settings.source = src
    app.settings.target = tmp_path / "processed"
    app.settings.enriched = tmp_path / "enriched"
    r = app.scan()
    assert r["ok"] is True
    return app, src


def _by_rel(node: dict, rel: str) -> dict | None:
    if node["rel"] == rel:
        return node
    for kid in node.get("children", []):
        found = _by_rel(kid, rel)
        if found is not None:
            return found
    return None


def _rels_preorder(node: dict) -> list[str]:
    out = [node["rel"]]
    for kid in node.get("children", []):
        out.extend(_rels_preorder(kid))
    return out


# ── scan_tree ─────────────────────────────────────────────────────────────


def test_scan_tree_nesting_empty_and_excluded(tmp_path):
    app, _src = _scanned(tmp_path)
    r = app.scan_tree()
    assert r["ok"] is True
    tree = r["tree"]
    assert tree["rel"] == ""
    assert tree["kind"] == "folder"
    # folder-in-folder nesting
    inner = _by_rel(tree, "docs/inner")
    assert inner is not None and inner["kind"] == "folder"
    # empty folders survive the projection
    empty = _by_rel(tree, "empty")
    assert empty is not None and empty["children"] == []
    # default-excluded dirs never enter the index/tree
    assert _by_rel(tree, ".git") is None


def test_scan_tree_children_sorted_casefold(tmp_path):
    app, _src = _scanned(tmp_path)
    tree = app.scan_tree()["tree"]

    def _check(node: dict) -> None:
        kids = [k["rel"] for k in node.get("children", [])]
        assert kids == sorted(kids, key=str.casefold)
        for k in node.get("children", []):
            _check(k)

    _check(tree)


def test_scan_tree_no_scan(tmp_path):
    assert _app(tmp_path).scan_tree() == {"ok": False, "error": "no_scan"}


def test_scan_response_carries_tree_additively(tmp_path):
    app, _src = _scanned(tmp_path)
    r = app.scan()
    # legacy shapes preserved
    assert "files" in r and "aggregate" in r and "detected_total" in r
    assert r["tree"] is not None and r["tree"]["tree"]["rel"] == ""
    node = _by_rel(r["tree"]["tree"], "src/main.py")
    assert node is not None and node["kind"] == "file"


# ── overview_build: guards ────────────────────────────────────────────────


def test_overview_build_no_scan(tmp_path):
    assert _app(tmp_path).overview_build(
        {"selection": [], "selection_provided": False}
    ) == {"ok": False, "error": "no_scan"}


def test_overview_build_stale_scan(tmp_path):
    app, _src = _scanned(tmp_path)
    app.settings.source = tmp_path / "elsewhere"
    assert app.overview_build(
        {"selection": [], "selection_provided": False}
    ) == {"ok": False, "error": "stale_scan"}


def test_overview_build_empty_selection(tmp_path):
    app, _src = _scanned(tmp_path)
    r = app.overview_build({"selection": ["no-such-dir"], "selection_provided": True})
    assert r == {"ok": False, "error": "empty_selection"}


def test_overview_build_bad_output_inside_source(tmp_path):
    app, _src = _scanned(tmp_path)
    app.settings.enriched = app.settings.source / "out"
    r = app.overview_build({"selection": [], "selection_provided": False})
    assert r["ok"] is False and r["error"].startswith("bad_output")


# ── overview_build: selection semantics ───────────────────────────────────


def test_overview_build_selection_semantics(tmp_path):
    app, _src = _scanned(tmp_path)
    whole = app.overview_build({"selection": [], "selection_provided": False})
    assert whole["ok"] is True
    # root branch ("") covers the whole index
    root = app.overview_build({"selection": [""], "selection_provided": True})
    assert root["ok"] is True and root["rels"] == whole["rels"]
    # subtree by prefix
    branch = app.overview_build({"selection": ["src"], "selection_provided": True})
    assert branch["ok"] is True
    assert branch["rels"] == ["src", "src/main.py"]
    # exact file rel
    single = app.overview_build({"selection": ["README.md"], "selection_provided": True})
    assert single["ok"] is True and single["rels"] == ["README.md"]
    # several branches at once
    multi = app.overview_build(
        {"selection": ["src", "tests"], "selection_provided": True}
    )
    assert multi["ok"] is True
    assert multi["rels"] == ["src", "src/main.py", "tests", "tests/test_main.py"]


# ── overview_build: the card ──────────────────────────────────────────────


def test_overview_build_writes_card_outside_source(tmp_path):
    app, src = _scanned(tmp_path)
    before = sorted(p.name for p in src.iterdir())
    r = app.overview_build({"selection": [], "selection_provided": False})
    assert r["ok"] is True
    card = Path(r["file"])
    # outside the source, inside <enriched>/overview, distinct from <dir>_обзор.md
    assert card.is_file()
    assert card.suffix == ".md"
    assert not card.name.endswith("_обзор.md")
    assert card.parent.name == "overview"
    assert card.parent.parent == app.settings.enriched.resolve()
    assert src.resolve() not in card.resolve().parents
    # source untouched
    assert sorted(p.name for p in src.iterdir()) == before
    assert not (card.parent / (card.name + ".tmp")).exists()

    md = card.read_text(encoding="utf-8")
    assert "type: overview" in md
    assert "# Project — общий обзор" in md
    assert "## Структура" in md
    assert "|Файл|Тип|Описание|" in md
    # wikilinks for files (v5 syntax family), honest code spans for folders (M6)
    assert "[[src/main.py|main.py]]" in md
    assert "`src/`" in md
    assert "[[src|src/]]" not in md
    # file tables carry type/desc by extension
    assert "|Python|" in md and "|Markdown|" in md


def test_overview_build_branch_card(tmp_path):
    app, _src = _scanned(tmp_path)
    r = app.overview_build({"selection": ["docs"], "selection_provided": True})
    # docs + docs/inner + root skeleton
    assert r["ok"] is True and r["folders"] == 3
    md = Path(r["file"]).read_text(encoding="utf-8")
    assert "main.py" not in md
    assert "guide.md" in md


def test_ragged_selection_parent_context(tmp_path):
    """M5: a chosen file with an unselected parent keeps its skeleton.

    selection=["src/main.py"] — backend keeps exactly the file, the card
    restores ``src`` + root as structural context, and the unselected
    neighbour (``neighbour.py``) is NOT pulled into the table.
    """
    from obsidianizer.overview import build_overview, render_overview_markdown

    src = tmp_path / "P"
    (src / "src").mkdir(parents=True)
    (src / "src" / "main.py").write_text("x\n", encoding="utf-8")
    (src / "src" / "neighbour.py").write_text("x\n", encoding="utf-8")
    app = _app(tmp_path)
    app.settings.source = src
    app.settings.target = tmp_path / "processed"
    app.settings.enriched = tmp_path / "enriched"
    assert app.scan()["ok"] is True

    r = app.overview_build({"selection": ["src/main.py"], "selection_provided": True})
    assert r["ok"] is True and r["rels"] == ["src/main.py"]

    ov = build_overview(app._last_source_index, r["rels"])
    by = {d.rel: d for d in ov.dirs}
    assert "" in by and "src" in by  # structural skeleton restored
    assert [f.name for f in by["src"].files] == ["main.py"]
    assert ov.total_files == 1 and ov.total_folders == 2
    # response counters agree with the card (folders = skeleton shown)
    assert r["folders"] == ov.total_folders == 2
    assert r["files"] == ov.total_files == 1

    md = render_overview_markdown(ov)
    assert "main.py" in md and "neighbour.py" not in md


# ── builder units ─────────────────────────────────────────────────────────


def test_builder_known_patterns_fallback_and_unknown(tmp_path):
    from obsidianizer.overview import dir_purpose, file_type_desc, is_derived

    # known service patterns (§11)
    assert "Git" in dir_purpose(".git", [])
    assert "Obsidian" in dir_purpose(".obsidian", [])
    assert "OpenCode" in dir_purpose(".opencode", [])
    assert "Python-модулей" in dir_purpose("__pycache__", [])
    assert "pytest" in dir_purpose(".pytest_cache", [])
    # content fallback (§12)
    assert "Python" in dir_purpose("mylib", ["text", "text", "unknown"]) or \
        "преимущественно" in dir_purpose("mylib", ["text", "text", "unknown"])
    assert "тесты" in dir_purpose("tests", ["text", "text"]).lower()
    # unknown dir is never an error
    assert isinstance(dir_purpose("mystery-xyz", []), str)
    assert isinstance(dir_purpose("mystery-xyz", ["unknown"]), str)
    # file table by name/extension/pattern (§14)
    assert file_type_desc("main.py", "text") == ("Python", "Исходный код")
    assert file_type_desc("README.md", "text") == ("Markdown", "Документация")
    assert file_type_desc("pyproject.toml", "text") == ("TOML", "Конфигурация")
    assert file_type_desc("test_api.py", "text")[0] == "Python-тест"
    # derived artifacts tracked as service files
    assert is_derived("Project_заметки.md")
    assert is_derived("Project_обзор.md")
    assert not is_derived("guide.md")


def test_builder_derived_files_are_service(tmp_path):
    from obsidianizer.overview import build_overview

    app, _src = _scanned(tmp_path)
    (app.settings.source / "Project_обзор.md").write_text("# old\n", encoding="utf-8")
    assert app.scan()["ok"] is True
    ov = build_overview(app._last_source_index, ["", "Project_обзор.md"])
    root = next(d for d in ov.dirs if d.rel == "")
    svc = next(f for f in root.files if f.name == "Project_обзор.md")
    assert svc.type == "Служебный"
    # service files do not steer the purpose heuristics
    assert "Markdown" not in root.purpose


def test_structure_lines_indented_by_depth():
    from obsidianizer.overview import OverviewDir, _structure_lines

    dirs = [
        OverviewDir(rel="", name="P", purpose="x"),
        OverviewDir(rel="docs", name="docs", purpose="x"),
        OverviewDir(rel="docs/inner", name="inner", purpose="x"),
    ]
    assert _structure_lines(dirs) == ["./", "  docs/", "    inner/"]


def test_builder_deterministic(tmp_path):
    from obsidianizer.overview import build_overview, render_overview_markdown

    app, _src = _scanned(tmp_path)
    rels = ["", "README.md", "docs", "docs/guide.md", "src", "src/main.py"]
    first = render_overview_markdown(build_overview(app._last_source_index, rels))
    second = render_overview_markdown(build_overview(app._last_source_index, rels))
    assert first == second


def test_render_explicit_stamp_is_deterministic(tmp_path):
    """Principle №8: the ``generated`` stamp is an explicit substitution point."""
    from obsidianizer.overview import build_overview, render_overview_markdown

    app, _src = _scanned(tmp_path)
    ov = build_overview(app._last_source_index, ["", "README.md"])
    pinned = render_overview_markdown(ov, now="2030-01-02 03:04")
    assert render_overview_markdown(ov, now="2030-01-02 03:04") == pinned
    other = render_overview_markdown(ov, now="2030-01-02 03:05")
    assert other != pinned
    assert other.replace("2030-01-02 03:05", "2030-01-02 03:04") == pinned


# ── static JS contracts ───────────────────────────────────────────────────


def test_det_context_menu_stops_propagation():
    """Branch ops must hit the clicked branch, not the root.

    The tree nests <li> nodes; without stopPropagation the event bubbles
    to the root li (rel="") and e.g. 'deselect branch' clears everything.
    """
    js = APP_JS.read_text(encoding="utf-8")
    idx = js.find("li.addEventListener(\"contextmenu\"")
    assert idx >= 0
    block = js[idx: idx + 300]
    assert "stopPropagation" in block
    assert "detShowCtx" in block


def test_doscan_journals_to_log():
    js = APP_JS.read_text(encoding="utf-8")
    idx = js.find("function doScan()")
    assert idx >= 0
    block = js[idx: idx + 4000]
    assert "log.scan_started" in block
    assert "log.scan_done" in block
    assert "log.scan_error" in block


def test_sel_tree_module_contract():
    js = APP_JS.read_text(encoding="utf-8")
    for fn in (
        "function detMergeScan",
        "function detSetAll",
        "function detInvert",
        "function detBranch",
        "function detShowCtx",
        "function detHideCtx",
        "function renderDetTree",
        "function getTreeSelection",
        "function runOverviewBuild",
        # C1 fix: DTO unwrapping + DOM-free pure block (see test_ticket014_js.py)
        "function detUnwrap",
        "function detCollectRels",
        "function detBranchApply",
        "TICKET-014-PURE-START",
        "TICKET-014-PURE-END",
    ):
        assert fn in js, f"missing {fn}"
    # root branch addresses the whole tree (empty-prefix bug not copied)
    idx = js.find("function detBranchApply(")
    assert idx >= 0 and 'rel === ""' in js[idx: idx + 500]
    # no stacked document.click handlers on re-render
    idx = js.find("box.appendChild(ctx);")
    assert "removeEventListener" in js[idx: idx + 300]
    # overview run: own status line, stale-guard, promise catch
    idx = js.find("function runOverviewBuild()")
    block = js[idx: idx + 3000]
    assert "status.overview_started" in block
    assert "detSelectionFresh" in block
    assert ".catch(" in block


def test_overview_started_i18n():
    from pathlib import Path as _P

    i18n = (_P(APP_JS).parent / "i18n.js").read_text(encoding="utf-8")
    assert '"status.overview_started"' in i18n


def test_import_selection_stays_frozen():
    """TICKET-004 regression guard: sel_files still reads #scanFileList only."""
    js = APP_JS.read_text(encoding="utf-8")
    for fn, need in (
        ("function getSelectedFiles()", "$(\"scanFileList\")"),
        ("function selectAllChats()", "$(\"scanFileList\")"),
        ("function deselectAll()", "$(\"scanFileList\")"),
        ("function updateScanSummary()", "$(\"scanFileList\")"),
    ):
        idx = js.find(fn)
        assert idx >= 0, f"missing {fn}"
        assert need in js[idx: idx + 500], f"{fn} must read #scanFileList"


def test_detected_containers_coexist():
    html = APP_HTML.read_text(encoding="utf-8")
    for eid in (
        'id="scanFileList"',
        'id="detectedTree"',
        'id="detSelectAll"',
        'id="detDeselectAll"',
        'id="detInvert"',
        'id="detSummary"',
        'id="btnOverviewBuild"',
        'id="overviewResult"',
    ):
        assert eid in html, f"missing {eid}"
