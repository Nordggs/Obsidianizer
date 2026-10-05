"""TICKET-011 — UI reorganization contracts.

Static structure checks over the real ``app.html``/``chat.html`` markup plus
headless bridge checks (no webview window): the six processing sub-tabs, the
Enrichment/AI-settings/AI-Chat separation, the exclusion fields, the additive
``scan`` aggregate and the AI Chat model editor in its own window.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from obsidianizer.config import Settings
from obsidianizer.ui import UIApp

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "obsidianizer" / "web"
APP_HTML = WEB / "app.html"
APP_JS = WEB / "app.js"
CHAT_HTML = WEB / "chat.html"
CHAT_JS = WEB / "chat.js"

_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img",
    "input", "link", "meta", "param", "source", "track", "wbr",
}

SUBTABS = ("source", "detected", "enrichment", "ai", "review", "tools")


class _Tree(HTMLParser):
    """Minimal DOM-ish tree: id -> {tag, class, attrs, parents}."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str, str]] = []  # (tag, id, class)
        self.nodes: dict[str, dict] = {}

    def handle_starttag(self, tag, attrs):
        mapping = dict(attrs)
        el_id = mapping.get("id") or ""
        if el_id:
            self.nodes[el_id] = {
                "tag": tag,
                "class": mapping.get("class") or "",
                "attrs": mapping,
                "parents": list(self.stack),
            }
        if tag not in _VOID:
            self.stack.append((tag, el_id, mapping.get("class") or ""))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _tree() -> _Tree:
    parser = _Tree()
    parser.feed(APP_HTML.read_text(encoding="utf-8"))
    return parser


def _parent_ids(tree: _Tree, el_id: str) -> list[str]:
    assert el_id in tree.nodes, f"{el_id} not found in app.html"
    return [p[1] for p in tree.nodes[el_id]["parents"]]


def _app(tmp_path: Path) -> UIApp:
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"  # never write the repo config
    return app


# ── static structure ────────────────────────────────────────────────────────


def test_top_nav_is_obsidianize_and_proc_only():
    tree = _tree()
    assert "tabNavObs" in tree.nodes
    assert "tabNavProc" in tree.nodes
    assert tree.nodes["tabNavProc"]["attrs"].get("data-page") == "proc"
    assert "tabNavChat" not in tree.nodes
    assert "tabNavAi" not in tree.nodes


def test_six_subtabs_and_pages_present():
    html = APP_HTML.read_text(encoding="utf-8")
    for sub in SUBTABS:
        assert f'data-sub="{sub}"' in html, f"missing subtab {sub}"
        assert f'id="sub-{sub}"' in html, f"missing sub-page {sub}"


def test_controls_live_in_their_sections():
    tree = _tree()
    assert "sub-source" in _parent_ids(tree, "source")
    assert "sub-source" in _parent_ids(tree, "btnScan")
    assert "sub-source" in _parent_ids(tree, "excludeDirs")
    assert "sub-source" in _parent_ids(tree, "excludeMasks")
    # scan preview stays inside the "Detected" section (Block A contract)
    assert "scanPreview" in _parent_ids(tree, "scanFileList")
    assert "sub-detected" in _parent_ids(tree, "scanFileList")
    # Enrichment holds the run controls and the outputs
    assert "sub-enrichment" in _parent_ids(tree, "btnRun")
    assert "sub-enrichment" in _parent_ids(tree, "btnStop")
    assert "sub-enrichment" in _parent_ids(tree, "btnAi")
    assert "sub-enrichment" in _parent_ids(tree, "target")
    assert "sub-enrichment" in _parent_ids(tree, "enriched")
    assert "sub-enrichment" in _parent_ids(tree, "chatOnly")
    # AI settings hold the enrichment model + the AI checkbox
    assert "sub-ai" in _parent_ids(tree, "ai")
    assert "sub-ai" in _parent_ids(tree, "model")
    # folder review
    assert "sub-review" in _parent_ids(tree, "btnAiScan")
    assert "sub-review" in _parent_ids(tree, "btnAiRun")
    # tools
    assert "sub-tools" in _parent_ids(tree, "btnTopics")
    assert "sub-tools" in _parent_ids(tree, "btnGroupAll")
    assert "sub-tools" in _parent_ids(tree, "btnChat")
    assert "sub-tools" in _parent_ids(tree, "btnOpen")
    assert "sub-tools" in _parent_ids(tree, "btnOpenEnriched")


def test_btn_run_stays_in_actions_section():
    tree = _tree()
    parents = tree.nodes["btnRun"]["parents"]
    assert any(
        tag == "section" and "actions" in cls.split() for tag, _id, cls in parents
    ), "btnRun must live inside section.actions"


def test_chat_model_input_removed_from_main_panel():
    tree = _tree()
    assert "chatModel" not in tree.nodes
    # a read-only enrichment-model label is shown in Folder review instead
    assert "aiReviewModel" in tree.nodes
    assert "sub-review" in _parent_ids(tree, "aiReviewModel")


def test_placeholder_checkboxes_disabled():
    tree = _tree()
    for eid in ("enrichLinks", "enrichAiAnalysis"):
        assert eid in tree.nodes, f"{eid} placeholder missing"
        assert "disabled" in tree.nodes[eid]["attrs"], f"{eid} must be disabled"
    # informational flow rows are read-only too
    for eid in ("enrichCards", "enrichDescriptions"):
        assert "disabled" in tree.nodes[eid]["attrs"]


def test_js_uses_subtab_class_and_scan_aggregate():
    js = APP_JS.read_text(encoding="utf-8")
    assert "function showSubTab" in js
    assert "set_exclusions" in js
    assert "renderDetected" in js
    assert 'querySelectorAll(".subtab")' in js
    # chat model is no longer pushed from the main panel
    assert "chatModel" not in js


def test_chat_window_has_model_editor():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'id="chatModelInput"' in html
    assert "chatModelLabel" not in html
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "set_chat_model" in js


# ── headless bridges ────────────────────────────────────────────────────────


def test_set_chat_model_persists(tmp_path):
    app = _app(tmp_path)
    app.settings.ai_chat.model = "chat-A"
    r = app.set_chat_model("chat-B")
    assert r["ok"] is True and r["chat_model"] == "chat-B"
    assert Settings.load(tmp_path / "config.yml").ai_chat.model == "chat-B"


def test_set_llm_two_args_does_not_clobber_chat_model(tmp_path):
    app = _app(tmp_path)
    app.settings.ai_chat.model = "keep-chat"
    app.set_llm(True, "enr-x")
    assert app.settings.ai_chat.model == "keep-chat"
    assert app.settings.ai_enrichment.model == "enr-x"


def test_set_exclusions_persists(tmp_path):
    app = _app(tmp_path)
    r = app.set_exclusions(".git, out", "*.tmp")
    assert r["exclude_dirs"] == [".git", "out"]
    assert r["exclude_masks"] == ["*.tmp"]
    t = Settings.load(tmp_path / "config.yml")
    assert t.exclude_dirs == [".git", "out"]
    assert t.exclude_masks == ["*.tmp"]


def test_defaults_expose_exclusions_and_models(tmp_path):
    app = _app(tmp_path)
    app.settings.ai_enrichment.model = "ENR"
    app.settings.ai_chat.model = "CHAT"
    d = app.defaults()
    assert d["model"] == "ENR" and d["chat_model"] == "CHAT"
    assert isinstance(d["exclude_dirs"], list)
    assert isinstance(d["exclude_masks"], list)


def test_scan_aggregate_is_additive(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.md").write_text("# a\n", encoding="utf-8")
    (src / "b.png").write_text("x", encoding="utf-8")

    app = _app(tmp_path)
    app.settings.source = src
    app.settings.target = tmp_path / "processed"
    r = app.scan()

    assert r["ok"] is True
    # legacy fields preserved
    assert "files" in r and "total" in r
    agg = r["aggregate"]
    assert set(agg) >= {
        "chats", "documents", "images", "cad", "tables", "folders", "total",
    }
    assert agg["images"] == 1
    assert agg["folders"] >= 2  # src + sub
    assert r["detected_total"] == agg["total"]


def test_workflow_source_scan_detected_run(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "note.md").write_text("# hello\n", encoding="utf-8")

    app = _app(tmp_path)
    app.settings.source = src
    app.settings.target = tmp_path / "processed"
    app.settings.enriched = tmp_path / "enriched"
    app.settings.chat_only = False
    app.settings.llm_enabled = False

    scan = app.scan()
    assert scan["ok"] and scan["detected_total"] >= 1
    assert any(f["path"] == "note.md" for f in scan["files"])

    report = app.run_now(app.settings)
    assert report.processed == 1
    assert (tmp_path / "processed" / "note.md").is_file()
