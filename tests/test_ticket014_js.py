"""TICKET-014 — executable JS contract for the sel_tree pure block.

Runs ``tests/js/det_tree_smoke.js`` under node against the real
``src/obsidianizer/web/app.js``: DTO unwrapping (C1 regression), preorder
collection, branch ops including ``rel == ""``. Skipped when node is absent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "obsidianizer" / "web" / "app.js"
SMOKE = Path(__file__).resolve().parent / "js" / "det_tree_smoke.js"


def test_sel_tree_pure_block_smoke():
    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("node not available")
    proc = subprocess.run(
        [node, str(SMOKE), str(APP_JS)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"sel_tree smoke failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "smoke passed" in proc.stdout
