"""UI bridge tests — headless (no webview window)."""

import shutil
from pathlib import Path

from obsidianizer.config import Settings
from obsidianizer.events import EventType
from obsidianizer.ui import UIApp

FIXTURES = Path(__file__).parent / "fixtures" / "md"


def _prepare(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "processed"
    shutil.copytree(FIXTURES, source)
    return source, target


def _app(source: Path, target: Path) -> UIApp:
    app = UIApp()
    app.settings.source = source
    app.settings.target = target
    app.settings.llm_enabled = False
    return app


def test_defaults_expose_settings():
    app = UIApp()
    d = app.defaults()
    assert "version" in d
    assert "source" in d and "target" in d
    assert "model" in d
    assert "llm_enabled" in d


def test_set_paths_accepts_disjoint(tmp_path):
    app = UIApp()
    r = app.set_paths(str(tmp_path / "src"), str(tmp_path / "dst"))
    assert r["ok"] is True
    assert app.settings.source.resolve() == (tmp_path / "src").resolve()


def test_set_paths_rejects_overlap(tmp_path):
    app = UIApp()
    src = tmp_path / "src"
    tgt = src / "sub"
    r = app.set_paths(str(src), str(tgt))
    assert r["ok"] is False
    assert "error" in r


def test_scan_reports_candidates(tmp_path):
    source, _ = _prepare(tmp_path)
    app = _app(source, tmp_path / "processed")
    r = app.scan()
    assert r["ok"] is True
    assert r["total"] == 2


def test_run_now_processes_and_collects_events(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    report = app.run_now(app.settings)
    assert report.processed == 2
    assert report.failed == []
    assert report.cancelled is False

    kinds = [e.type for e in app.events]
    assert kinds[0] is EventType.SCAN_STARTED
    assert kinds[-1] is EventType.FINISHED
    assert kinds.count(EventType.FILE_DONE) == 2


def test_cancel_before_run_stops_without_processing(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    app.cancel()
    report = app.run_now(app.settings)
    assert report.cancelled is True
    assert report.processed == 0
    assert app.events[-1].type is EventType.FINISHED
    assert "отменено" in app.events[-1].message


def test_can_run_without_window(tmp_path):  # noqa: ARG001
    # Windowless bridge: _on_event must not raise even with window=None.
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    assert app._window is None
    report = app.run_now(app.settings)
    assert report.processed == 2


class _FakeWindow:
    """Minimal window stand-in: pywebview 6 returns a tuple from the dialog."""

    def __init__(self, result) -> None:
        self.result = result

    def create_file_dialog(self, _dialog_type):  # noqa: ARG002
        return self.result


def test_choose_folder_unpacks_tuple_path():
    app = UIApp()
    app._window = _FakeWindow(("D:\\raw",))
    assert app.choose_folder() == "D:\\raw"


def test_choose_folder_handles_list_path():
    app = UIApp()
    app._window = _FakeWindow(["D:\\raw"])
    assert app.choose_folder() == "D:\\raw"


def test_choose_folder_returns_none_on_cancel():
    app = UIApp()
    app._window = _FakeWindow(None)
    assert app.choose_folder() is None


def test_choose_folder_none_without_window():
    app = UIApp()
    assert app._window is None
    assert app.choose_folder() is None