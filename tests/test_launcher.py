"""Root launcher tests — obsidianizer from the repo root without installing."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "Obsidianizer.py"


def test_launcher_script_compiles():
    source = LAUNCHER.read_text(encoding="utf-8")
    compile(source, str(LAUNCHER), "exec")


def test_check_reports_version():
    # PYTHONPATH is cleared: package resolution must come from the launcher's
    # own src/ hook, not from an ambient import path.
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [sys.executable, str(LAUNCHER), "--check"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Obsidianizer" in proc.stdout
    assert "пакет:" in proc.stdout


def test_missing_webview_prints_guidance_and_exits(monkeypatch):
    # Force 'import webview' to fail; the launcher must explain and exit 1
    # instead of opening a window (which would raise an unrelated traceback).
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(sys, "argv", [str(LAUNCHER)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(LAUNCHER), run_name="__main__")
    assert excinfo.value.code == 1