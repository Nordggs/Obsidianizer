"""Obsidian integration layer: Templater template installation and status.

Two inputs — one engine: the GUI and the Obsidian hotkey both run the same
generator through the CLI. This module makes connecting them a one-button
operation:

- ``resolve_cli_command()``  — the CLI entry to embed into the template
  (the frozen EXE proxies CLI subcommands; a source checkout uses
  ``obsidianizer-cli.bat``).
- ``detect_templater_folder(vault)`` — where Templater looks for templates
  (reads the plugin's ``data.json``, falls back to ``<vault>/templates``).
- ``install_obsidian_integration(vault, …)`` — copies the update AND the
  rebuild templates with the real CLI path substituted, never overwriting
  without ``repair``.
- ``integration_status(vault)`` — ✓/✗ snapshot for the GUI help window.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .i18n import tr

TEMPLATE_NAME = "Obsidianizer Update.md"
# TICKET-003: second Templater command — forced structure migration.
# Update is for changes; Rebuild is for old-structure cards.
REBUILD_TEMPLATE_NAME = "Obsidianizer Rebuild.md"
TEMPLATER_PLUGIN_DIR = "templater-obsidian"

# Canonical update template. ``{{CLI_PATH}}`` is substituted at install time
# with the resolved CLI entry (absolute, JS-escaped).
TEMPLATE_TEXT = """<%*
// Obsidianizer: обновить карточку текущей папки (Templater-шаблон).
// Путь к CLI подставлен установщиком Obsidianizer автоматически.
try {
  const tFile = tp.config.target_file;
  const base = app.vault.adapter.getBasePath();
  const rel = tFile.parent.path;
  const full = base + "/" + rel;
  const { exec } = require("child_process");
  const fs = require("fs");
  const cli = "{{CLI_PATH}}";
  exec(
    `"${cli}" folders --path "${full}" --no-recursive --adopt --vault-root "${base}" --rel "${rel}"`,
    (error) => {
      if (error) {
        new Notice("Obsidianizer: ошибка обновления — " + error.message);
        return;
      }
      try {
        const text = fs.readFileSync(full + "/" + tFile.name, "utf8");
        const g = text.includes("## Gallery") ? "✓" : "✗";
        const i = text.includes("## Images") ? "✓" : "✗";
        new Notice("Obsidianizer: обновлено · Gallery " + g + " · Images " + i);
      } catch (e) {
        new Notice("Obsidianizer: карточка обновлена");
      }
    }
  );
} catch (e) {
  new Notice("Obsidianizer: " + e.message);
}
%>"""

# Canonical rebuild template: same flow as the update one, but forces
# regeneration of our cards with the current renderer (``--rebuild``).
# Foreign notes are never overwritten — they stay a conflict.
REBUILD_TEMPLATE_TEXT = """<%*
 // Obsidianizer: принудительно пересобрать карточку текущей папки
 // (Rebuild — миграция старой структуры, Templater-шаблон).
 // Путь к CLI подставлен установщиком Obsidianizer автоматически.
try {
  const tFile = tp.config.target_file;
  const base = app.vault.adapter.getBasePath();
  const rel = tFile.parent.path;
  const full = base + "/" + rel;
  const { exec } = require("child_process");
  const fs = require("fs");
  const cli = "{{CLI_PATH}}";
  exec(
    `"${cli}" folders --path "${full}" --no-recursive --rebuild --vault-root "${base}" --rel "${rel}"`,
    (error) => {
      if (error) {
        new Notice("Obsidianizer: ошибка пересборки — " + error.message);
        return;
      }
      try {
        const text = fs.readFileSync(full + "/" + tFile.name, "utf8");
        const g = text.includes("## Gallery") ? "✓" : "✗";
        const i = text.includes("## Images") ? "✓" : "✗";
        new Notice("Obsidianizer: пересобрано · Gallery " + g + " · Images " + i);
      } catch (e) {
        new Notice("Obsidianizer: карточка пересобрана");
      }
    }
  );
} catch (e) {
  new Notice("Obsidianizer: " + e.message);
}
%>"""


def resolve_cli_command() -> str:
    """Absolute path of the CLI entry to embed into the Templater template.

    - Frozen (PyInstaller EXE): the exe itself proxies CLI subcommands
      (``Obsidianizer.exe folders …``).
    - Source checkout: ``obsidianizer-cli.bat`` in the repo root.
    """

    if getattr(sys, "frozen", False):
        return str(Path(sys.executable))
    bat = Path(__file__).resolve().parents[2] / "obsidianizer-cli.bat"
    return str(bat)


def detect_templater_folder(vault: Path) -> dict:
    """Locate the Templater templates folder inside ``vault``.

    Returns ``{"templater_installed": bool, "folder": Path | None}``. When
    the plugin exists but its settings carry no folder, the sensible default
    ``<vault>/templates`` is returned (the user may need to select it in the
    Templater settings — the installer reports that).
    """

    plugin = vault / ".obsidian" / "plugins" / TEMPLATER_PLUGIN_DIR
    if not plugin.is_dir():
        return {"templater_installed": False, "folder": None}

    folder: Path | None = None
    data_file = plugin / "data.json"
    if data_file.is_file():
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
            tf = str(data.get("templates_folder") or "").strip().strip("/")
            if tf:
                folder = vault / tf
        except (ValueError, OSError):
            folder = None
    if folder is None:
        folder = vault / "templates"  # Templater's common default
    return {"templater_installed": True, "folder": folder}


def integration_status(vault: Path) -> dict:
    """✓/✗ snapshot of the whole integration for the GUI help window."""

    vault = Path(vault)
    is_vault = vault.is_dir() and (vault / ".obsidian").is_dir()
    det = (
        detect_templater_folder(vault)
        if is_vault
        else {"templater_installed": False, "folder": None}
    )
    cli = resolve_cli_command()
    template_installed = bool(
        det["folder"] and (det["folder"] / TEMPLATE_NAME).is_file()
    )
    rebuild_template_installed = bool(
        det["folder"] and (det["folder"] / REBUILD_TEMPLATE_NAME).is_file()
    )
    return {
        "vault": str(vault),
        "vault_found": is_vault,
        "templater_installed": det["templater_installed"],
        "templates_folder": str(det["folder"]) if det["folder"] else None,
        "template_installed": template_installed,
        "rebuild_template_installed": rebuild_template_installed,
        "cli_path": cli,
        "cli_exists": Path(cli).exists(),
    }


def _write_template(folder: Path, name: str, text: str, cli: str) -> Path:
    """Atomically write one Templater template with the CLI substituted."""
    target = folder / name
    rendered = text.replace("{{CLI_PATH}}", str(cli).replace("\\", "\\\\"))
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, target)
    return target


def install_obsidian_integration(vault: Path, repair: bool = False) -> dict:
    """Install (or repair) the Templater templates into ``vault``.

    Installs BOTH commands: ``Obsidianizer Update`` (changes) and
    ``Obsidianizer Rebuild`` (TICKET-003, old-structure migration).
    The real CLI path is substituted for ``{{CLI_PATH}}`` (backslashes are
    JS-escaped). An existing template is never overwritten unless
    ``repair=True`` — the caller reports "exists" so the user can decide.
    Response keys are backward compatible (``target`` is the Update
    template); ``targets`` lists both installed files.
    """

    vault = Path(vault)
    if not vault.is_dir() or not (vault / ".obsidian").is_dir():
        return {
            "ok": False,
            "error": tr("int.not_vault"),
        }

    det = detect_templater_folder(vault)
    if not det["templater_installed"]:
        return {
            "ok": False,
            "templater_missing": True,
                "error": tr("int.templater_missing", plugin=TEMPLATER_PLUGIN_DIR),
        }

    folder: Path = det["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    templates = (
        (TEMPLATE_NAME, TEMPLATE_TEXT),
        (REBUILD_TEMPLATE_NAME, REBUILD_TEMPLATE_TEXT),
    )

    if not repair:
        for name, _text in templates:
            target = folder / name
            if target.exists():
                return {
                    "ok": False,
                    "exists": True,
                    "target": str(target),
                    "error": tr("int.exists"),
                }

    cli = resolve_cli_command()
    written = [_write_template(folder, name, text, cli) for name, text in templates]
    return {
        "ok": True,
        "target": str(written[0]),
        "targets": [str(p) for p in written],
        "cli": cli,
        "hint": tr("int.hint"),
    }
