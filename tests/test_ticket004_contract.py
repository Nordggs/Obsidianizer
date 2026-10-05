"""TICKET-004 contract tests — Chat Processing filter / scan / selection wiring.

These are *static contract* checks, not runtime browser tests:

* Block A parses the real ``app.html`` markup with ``html.parser`` and verifies
  the element hierarchy (``btnScan`` must not live inside the hidden
  ``scanPreview``).
* Block B is an explicit static-source contract over ``app.js`` (regexp over the
  source text). It protects the mandatory wiring from accidental deletion —
  it does NOT prove JavaScript execution. Real behaviour is confirmed by the
  packaged smoke-check (see the ticket plan).
* Block C checks the i18n key tables.
* Block D checks packaged build integrity: **a missing build is skipped, a
  stale build FAILS** — a forgotten rebuild must be visible, not silently
  ignored.

Paths are defined locally (no conftest.py on purpose).
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "obsidianizer" / "web"
APP_HTML = WEB / "app.html"
APP_JS = WEB / "app.js"
I18N_JS = WEB / "i18n.js"

BUILD_TOC = ROOT / "build" / "Obsidianizer" / "PYZ-00.toc"
DIST_WEB = ROOT / "dist" / "Obsidianizer" / "_internal" / "obsidianizer" / "web"


# ── helpers ─────────────────────────────────────────────────────────────────

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img",
    "input", "link", "meta", "param", "source", "track", "wbr",
}


class _HtmlTree(HTMLParser):
    """Minimal DOM-ish tree: id -> {tag, class, attrs, parents}."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str, str]] = []  # (tag, id, class)
        self.nodes: dict[str, dict] = {}

    def handle_starttag(self, tag, attrs):
        mapping = dict(attrs)
        el_id = mapping.get("id") or ""
        el_class = mapping.get("class") or ""
        if el_id:
            self.nodes[el_id] = {
                "tag": tag,
                "class": el_class,
                "attrs": mapping,
                "parents": list(self.stack),
            }
        if tag not in _VOID_TAGS:
            self.stack.append((tag, el_id, el_class))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _tree() -> _HtmlTree:
    parser = _HtmlTree()
    parser.feed(APP_HTML.read_text(encoding="utf-8"))
    return parser


def _parents(tree: _HtmlTree, el_id: str) -> list[tuple[str, str, str]]:
    assert el_id in tree.nodes, f"{el_id} not found in app.html"
    return tree.nodes[el_id]["parents"]


def _parent_ids(tree: _HtmlTree, el_id: str) -> list[str]:
    return [p[1] for p in _parents(tree, el_id)]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _i18n_section(js: str, name: str, end: str) -> str:
    m = re.search(rf"\n\s*{name}:\s*\{{", js)
    assert m, f"i18n section {name!r} not found"
    start = m.start()
    if end.startswith("re:"):
        m2 = re.search(end[3:], js)
        assert m2, f"end marker for {name!r} not found"
        return js[start:m2.start()]
    idx = js.index(end, start)
    return js[start:idx]


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def i18n_js() -> str:
    return I18N_JS.read_text(encoding="utf-8")


# ── Block A: HTML structure (parsed, not regexp) ─────────────────────────────


def test_a1_chat_only_checkbox_defaults_checked():
    tree = _tree()
    assert "chatOnly" in tree.nodes, "chatOnly checkbox missing"
    assert "checked" in tree.nodes["chatOnly"]["attrs"], "chatOnly must default to checked"


def test_a2_scan_preview_starts_hidden():
    tree = _tree()
    assert "scanPreview" in tree.nodes, "scanPreview container missing"
    assert "hidden" in tree.nodes["scanPreview"]["class"].split(), "scanPreview must start hidden"


def test_a3_btn_scan_not_inside_scan_preview():
    tree = _tree()
    assert "scanPreview" not in _parent_ids(tree, "btnScan"), (
        "btnScan must not be inside the hidden scanPreview (chicken-and-egg)"
    )


def test_a4_scan_file_list_inside_scan_preview():
    tree = _tree()
    assert "scanPreview" in _parent_ids(tree, "scanFileList"), (
        "scanFileList must be a child of scanPreview"
    )


def test_a5_btn_run_in_actions_section_not_scan_preview():
    tree = _tree()
    parents = _parents(tree, "btnRun")
    assert any(
        tag == "section" and "actions" in cls.split() for tag, _id, cls in parents
    ), "btnRun must live inside section.actions"
    assert "scanPreview" not in [p[1] for p in parents], "btnRun must not be inside scanPreview"


# ── Block B: JS static contract (source text, not runtime) ───────────────────


def test_b1_btn_scan_binds_doscan(app_js):
    assert re.search(
        r'btnScan"\)\s*\.addEventListener\(\s*"click"\s*,\s*doScan', app_js
    ), "btnScan click handler must call doScan"


def test_b2_run_calls_start_run_with_selection(app_js):
    assert re.search(
        r"api\.start_run\(\s*\{[^}]*selection\s*:", app_js, re.DOTALL
    ), "api.start_run must receive selection"


def test_b3_run_passes_selection_provided(app_js):
    assert re.search(
        r"api\.start_run\(\s*\{[^}]*selection_provided\s*:", app_js, re.DOTALL
    ), "api.start_run must receive selection_provided"


def test_b4_doscan_populates_scan_file_list(app_js):
    body = re.search(r"function doScan\(\)\s*\{[\s\S]*?\n  \}", app_js)
    assert body, "doScan function not found"
    assert "scanFileList" in body.group(0), "doScan must populate scanFileList"


def test_b5_get_selected_files_returns_selection_shape(app_js):
    assert re.search(
        r"function getSelectedFiles\(\)[\s\S]*?return\s*\{[^}]*selection[^}]*selection_provided",
        app_js,
    ), "getSelectedFiles must return {selection, selection_provided}"


# ── Block C: i18n keys ───────────────────────────────────────────────────────

_CHAT_FILTER_KEYS = (
    "chat.filter.chat_only",
    "chat.filter.select_all",
    "chat.filter.deselect_all",
    "chat.filter.non_chat_files",
)


def test_c1_i18n_ru_has_chat_filter_keys(i18n_js):
    ru = _i18n_section(i18n_js, "ru", "re:\\n\\s*en:\\s*\\{")
    for key in _CHAT_FILTER_KEYS:
        assert f'"{key}"' in ru, f"ru table missing {key}"


def test_c2_i18n_en_has_chat_filter_keys(i18n_js):
    en = _i18n_section(i18n_js, "en", "window.I18N_LANG")
    for key in _CHAT_FILTER_KEYS:
        assert f'"{key}"' in en, f"en table missing {key}"


# ── Block D: packaged build integrity ────────────────────────────────────────
# A missing build/dist is a skip. A stale build/dist is a FAILURE: the point of
# these tests is to make a forgotten rebuild visible. Each test MUST pass after
# a build.


def test_d1_dist_web_files_match_source():
    """dist web assets equal the source tree. MUST pass after a build."""
    if not DIST_WEB.is_dir():
        pytest.skip("dist not built")
    for name in ("app.html", "app.js", "i18n.js"):
        assert _md5(WEB / name) == _md5(DIST_WEB / name), (
            f"dist web/{name} is stale — rebuild the package"
        )


def test_d2_build_toc_contains_chat_detect():
    """PyInstaller PYZ TOC lists obsidianizer.chat_detect. MUST pass after a build."""
    if not BUILD_TOC.is_file():
        pytest.skip("build not built")
    toc = BUILD_TOC.read_text(encoding="utf-8", errors="replace")
    assert "obsidianizer.chat_detect" in toc, (
        "chat_detect missing from PYZ-00.toc — rebuild the package"
    )


def test_d3_dist_app_html_contains_chat_only():
    """Packaged app.html contains the chatOnly checkbox. MUST pass after a build."""
    if not DIST_WEB.is_dir():
        pytest.skip("dist not built")
    html = (DIST_WEB / "app.html").read_text(encoding="utf-8", errors="replace")
    assert 'id="chatOnly"' in html, "stale dist app.html (no chatOnly) — rebuild"


def test_d4_dist_app_html_contains_scan_preview():
    """Packaged app.html contains scanPreview. MUST pass after a build."""
    if not DIST_WEB.is_dir():
        pytest.skip("dist not built")
    html = (DIST_WEB / "app.html").read_text(encoding="utf-8", errors="replace")
    assert 'id="scanPreview"' in html, "stale dist app.html (no scanPreview) — rebuild"
