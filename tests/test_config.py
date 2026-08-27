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
            {"source": "custom_src", "target": "custom_tgt", "enriched": "custom_enc",
             "ollama": {"model": "qwen2.5"}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    s = Settings.load(cfg)
    assert str(s.source) == "custom_src"
    assert str(s.target) == "custom_tgt"
    assert str(s.enriched) == "custom_enc"
    assert s.ollama["model"] == "qwen2.5"
    # deep merge keeps other defaults
    assert s.ollama["endpoint"].startswith("http")


def test_default_enriched_is_sibling_default():
    s = Settings()
    assert str(s.enriched) == "enriched" or s.enriched == Path("./enriched")


def test_cli_overrides_config(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        yaml.safe_dump({"source": "cfg_src", "target": "cfg_tgt"}),
        encoding="utf-8",
    )
    s = Settings.load(cfg).apply_cli(source="cli_src", target=None, enriched="cli_enc")
    assert str(s.source) == "cli_src"
    assert str(s.target) == "cfg_tgt"
    assert str(s.enriched) == "cli_enc"


def test_missing_config_uses_defaults(tmp_path):
    s = Settings.load()
    assert s.source is not None


def test_disable_llm():
    s = Settings().disable_llm()
    assert s.llm_enabled is False


def test_load_reads_flags(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        yaml.safe_dump(
            {"prune": True, "prune_enriched": True, "dry_run": True, "llm_enabled": False}
        ),
        encoding="utf-8",
    )
    s = Settings.load(cfg)
    assert s.prune is True
    assert s.prune_enriched is True
    assert s.dry_run is True
    assert s.llm_enabled is False


def test_save_roundtrip(tmp_path):
    cfg = tmp_path / "config.yml"
    s = Settings()
    s.source = Path("custom_src")
    s.target = Path("custom_tgt")
    s.enriched = Path("custom_enc")
    s.llm_enabled = False
    s.prune = True
    s.prune_enriched = True
    s.dry_run = True
    s.ollama["model"] = "qwen2.5"
    s.save(cfg)

    t = Settings.load(cfg)
    assert str(t.source) == "custom_src"
    assert str(t.target) == "custom_tgt"
    assert str(t.enriched) == "custom_enc"
    assert t.llm_enabled is False
    assert t.prune is True
    assert t.prune_enriched is True
    assert t.dry_run is True
    assert t.ollama["model"] == "qwen2.5"
    # deep merge keeps other defaults
    assert t.ollama["endpoint"].startswith("http")


def test_save_merges_foreign_keys(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        yaml.safe_dump({"source": "old_src", "custom_key": "keep_me"}),
        encoding="utf-8",
    )
    s = Settings.load(cfg)
    s.source = Path("new_src")
    s.save(cfg)

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["source"] == "new_src"
    assert data["custom_key"] == "keep_me"
    assert Settings.load(cfg).source == Path("new_src")


def test_save_writes_atomically(tmp_path):
    cfg = tmp_path / "config.yml"
    Settings().save(cfg)
    assert cfg.exists()
    assert not cfg.with_name(cfg.name + ".tmp").exists()


def test_save_pins_config_path(tmp_path):
    cfg = tmp_path / "config.yml"
    s = Settings()
    s.save(cfg)
    assert s.config_path == cfg