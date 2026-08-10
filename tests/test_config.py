"""Config tests — precedence CLI > config > defaults."""

from pathlib import Path

import yaml

from obsidianizer.config import Settings


def test_defaults():
    s = Settings()
    assert str(s.source) == "raw" or s.source == Path("./raw")
    assert s.llm_enabled is True
    assert s.ollama["model"]


def test_load_from_config(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        yaml.safe_dump(
            {"source": "custom_src", "target": "custom_tgt",
             "ollama": {"model": "qwen2.5"}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    s = Settings.load(cfg)
    assert str(s.source) == "custom_src"
    assert str(s.target) == "custom_tgt"
    assert s.ollama["model"] == "qwen2.5"
    # deep merge keeps other defaults
    assert s.ollama["endpoint"].startswith("http")


def test_cli_overrides_config(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        yaml.safe_dump({"source": "cfg_src", "target": "cfg_tgt"}),
        encoding="utf-8",
    )
    s = Settings.load(cfg).apply_cli(source="cli_src", target=None)
    assert str(s.source) == "cli_src"
    assert str(s.target) == "cfg_tgt"


def test_missing_config_uses_defaults(tmp_path):
    s = Settings.load()
    assert s.source is not None


def test_disable_llm():
    s = Settings().disable_llm()
    assert s.llm_enabled is False