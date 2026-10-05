"""Obsidian integration layer: templater detection, install, CLI resolve."""

import json
import sys
from pathlib import Path

import pytest


# ── detect_templater_folder ──────────────────────────────────────────────


def _make_vault(tmp_path: Path, templater: bool = False, templates_folder: str | None = None) -> Path:
    vault = tmp_path / "MyVault"
    (vault / ".obsidian" / "plugins").mkdir(parents=True)
    if templater:
        plug = vault / ".obsidian" / "plugins" / "templater-obsidian"
        plug.mkdir(parents=True)
        data: dict = {}
        if templates_folder is not None:
            data["templates_folder"] = templates_folder
        (plug / "data.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
    return vault


def test_detect_templater_missing_plugin(tmp_path):
    from obsidianizer.integration import detect_templater_folder

    vault = _make_vault(tmp_path, templater=False)
    det = detect_templater_folder(vault)
    assert det["templater_installed"] is False
    assert det["folder"] is None


def test_detect_templater_configured_folder(tmp_path):
    from obsidianizer.integration import detect_templater_folder

    vault = _make_vault(tmp_path, templater=True, templates_folder="шаблоны")
    det = detect_templater_folder(vault)
    assert det["templater_installed"] is True
    assert det["folder"] == vault / "шаблоны"


def test_detect_templater_default_folder_without_settings(tmp_path):
    from obsidianizer.integration import detect_templater_folder

    vault = _make_vault(tmp_path, templater=True, templates_folder=None)
    det = detect_templater_folder(vault)
    assert det["templater_installed"] is True
    assert det["folder"] == vault / "templates"


# ── install_obsidian_integration ─────────────────────────────────────────


def test_install_writes_template_with_cli_path(tmp_path):
    from obsidianizer.integration import install_obsidian_integration

    vault = _make_vault(tmp_path, templater=True, templates_folder="шаблоны")
    res = install_obsidian_integration(vault)
    assert res["ok"] is True

    target = vault / "шаблоны" / "Obsidianizer Update.md"
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "{{CLI_PATH}}" not in text
    # the resolved dev CLI must be embedded, backslashes JS-escaped
    cli = str(Path(__file__).resolve().parents[1] / "obsidianizer-cli.bat")
    assert cli.replace("\\", "\\\\") in text


def test_install_writes_both_update_and_rebuild_templates(tmp_path):
    """TICKET-003: one install must drop both the Update and the Rebuild
    Templater commands (the Rebuild one runs ``folders --rebuild``)."""
    from obsidianizer.integration import install_obsidian_integration

    vault = _make_vault(tmp_path, templater=True, templates_folder="шаблоны")
    res = install_obsidian_integration(vault)
    assert res["ok"] is True

    cli = str(Path(__file__).resolve().parents[1] / "obsidianizer-cli.bat")
    for name in ("Obsidianizer Update.md", "Obsidianizer Rebuild.md"):
        target = vault / "шаблоны" / name
        assert target.is_file()
        text = target.read_text(encoding="utf-8")
        assert "{{CLI_PATH}}" not in text
        assert cli.replace("\\", "\\\\") in text
    rebuild = (vault / "шаблоны" / "Obsidianizer Rebuild.md").read_text(
        encoding="utf-8"
    )
    assert "--rebuild" in rebuild


def test_install_repair_adds_missing_rebuild_template(tmp_path):
    """A vault with only the legacy Update template gets the Rebuild one
    on repair; without repair nothing is touched."""
    from obsidianizer.integration import install_obsidian_integration

    vault = _make_vault(tmp_path, templater=True, templates_folder="templates")
    first = install_obsidian_integration(vault)
    assert first["ok"] is True
    (vault / "templates" / "Obsidianizer Rebuild.md").unlink()

    second = install_obsidian_integration(vault, repair=False)
    assert second["ok"] is False
    assert second["exists"] is True
    assert not (vault / "templates" / "Obsidianizer Rebuild.md").exists()

    third = install_obsidian_integration(vault, repair=True)
    assert third["ok"] is True
    assert (vault / "templates" / "Obsidianizer Rebuild.md").is_file()
    assert (vault / "templates" / "Obsidianizer Update.md").is_file()


def test_status_reports_rebuild_template(tmp_path):
    from obsidianizer.integration import integration_status

    vault = _make_vault(tmp_path, templater=True, templates_folder="шаблоны")
    st = integration_status(vault)
    assert st["template_installed"] is False
    assert st["rebuild_template_installed"] is False

    from obsidianizer.integration import install_obsidian_integration

    assert install_obsidian_integration(vault)["ok"] is True
    st = integration_status(vault)
    assert st["template_installed"] is True
    assert st["rebuild_template_installed"] is True

    (vault / "шаблоны" / "Obsidianizer Rebuild.md").unlink()
    st = integration_status(vault)
    assert st["template_installed"] is True
    assert st["rebuild_template_installed"] is False


def test_repo_rebuild_templates_are_not_empty():
    """TICKET-003 regression: the shipped Rebuild templates (manual install
    + extract_templates source) must carry the real ``--rebuild`` command,
    not an empty stub — the GUI-install path is covered by other tests."""
    repo = Path(__file__).resolve().parents[1]
    for rel in (
        Path("integration") / "Obsidianizer Rebuild Templater.md",
        Path("obsidian") / "templater" / "Obsidianizer Rebuild.md",
    ):
        text = (repo / rel).read_text(encoding="utf-8")
        assert len(text) > 500, f"{rel} is empty"
        assert "--rebuild" in text, f"{rel} has no --rebuild command"
        assert "{{CLI_PATH}}" in text, f"{rel} has no {{{{CLI_PATH}}}} placeholder"
        assert "folders" in text


def test_install_refuses_overwrite_without_repair(tmp_path):
    from obsidianizer.integration import install_obsidian_integration

    vault = _make_vault(tmp_path, templater=True, templates_folder="templates")
    first = install_obsidian_integration(vault)
    assert first["ok"] is True

    (vault / "templates" / "Obsidianizer Update.md").write_text(
        "custom", encoding="utf-8"
    )
    second = install_obsidian_integration(vault, repair=False)
    assert second["ok"] is False
    assert second["exists"] is True
    assert (
        vault / "templates" / "Obsidianizer Update.md"
    ).read_text(encoding="utf-8") == "custom"  # untouched

    third = install_obsidian_integration(vault, repair=True)
    assert third["ok"] is True
    assert (
        vault / "templates" / "Obsidianizer Update.md"
    ).read_text(encoding="utf-8") != "custom"


def test_install_reports_missing_templater(tmp_path):
    from obsidianizer.integration import install_obsidian_integration

    vault = _make_vault(tmp_path, templater=False)
    res = install_obsidian_integration(vault)
    assert res["ok"] is False
    assert res["templater_missing"] is True


def test_install_rejects_non_vault(tmp_path):
    from obsidianizer.integration import install_obsidian_integration

    plain = tmp_path / "NotAVault"
    plain.mkdir()
    res = install_obsidian_integration(plain)
    assert res["ok"] is False


# ── resolve_cli_command ──────────────────────────────────────────────────


def test_resolve_cli_command_dev_uses_bat():
    from obsidianizer.integration import resolve_cli_command

    cli = resolve_cli_command()
    if getattr(sys, "frozen", False):
        pytest.skip("frozen build")
    assert cli.endswith("obsidianizer-cli.bat")
    assert Path(cli).is_file()


# ── launcher CLI proxy ───────────────────────────────────────────────────


def test_launcher_proxies_cli_subcommand():
    """Obsidianizer.py folders --help must proxy into cli (SystemExit 0)."""
    import os

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    old_cwd = os.getcwd()
    os.chdir(repo_root)
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "obsidianizer_launcher", repo_root / "Obsidianizer.py"
        )
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)

        with pytest.raises(SystemExit) as exc:
            launcher.main(["folders", "--help"])
        assert exc.value.code == 0
    finally:
        os.chdir(old_cwd)
        sys.path.remove(str(repo_root))
