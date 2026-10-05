"""TICKET-010 — AI Enrichment / AI Chat separation.

Pins the three-level configuration contract: shared ``ai.*`` infrastructure +
generation defaults, and two independent runtime contracts
(``ai_enrichment``/``ai_chat``) with a ``group.param or ai.param`` cascade.
Each factory reads only its own group; the two never share a runtime object.
"""

from __future__ import annotations

from pathlib import Path

from obsidianizer.config import (
    DEFAULT_CHAT_PROMPT,
    DEFAULT_FOLDERS_ANALYZE_PROMPT,
    Settings,
)
from obsidianizer.llm import make_chat_client, make_enrichment_client
from obsidianizer.ui import UIApp


# ── configuration isolation ─────────────────────────────────────────────────


def test_models_are_independent():
    s = Settings()
    s.ai_enrichment.model = "enr"
    s.ai_chat.model = "chat"
    assert make_enrichment_client(s).model == "enr"
    assert make_chat_client(s).model == "chat"


def test_chat_model_change_does_not_touch_enrichment():
    s = Settings()
    before = s.ai_enrichment.model
    s.ai_chat.model = "other"
    assert s.ai_enrichment.model == before
    assert make_enrichment_client(s).model == before


def test_factories_off_when_llm_disabled():
    s = Settings()
    s.llm_enabled = False
    assert make_enrichment_client(s) is None
    assert make_chat_client(s) is None


# ── generation cascade ──────────────────────────────────────────────────────


def test_generation_cascade_inherits_from_ai():
    s = Settings()
    s.ai.timeout = 100
    s.ai.temperature = 0.1
    s.ai.num_ctx = 2048
    s.ai.limit_chars = 500
    for client in (make_enrichment_client(s), make_chat_client(s)):
        assert client.timeout == 100
        assert client.temperature == 0.1
        assert client.num_ctx == 2048
        assert client.limit_chars == 500


def test_generation_overrides_are_per_contract():
    s = Settings()
    s.ai.timeout = 100
    s.ai.temperature = 0.1
    s.ai.num_ctx = 2048
    s.ai.limit_chars = 500
    s.ai_enrichment.timeout = 7
    s.ai_enrichment.temperature = 0.9
    s.ai_chat.num_ctx = 64
    s.ai_chat.limit_chars = 42

    e = make_enrichment_client(s)
    c = make_chat_client(s)
    # overridden in the group
    assert e.timeout == 7 and e.temperature == 0.9
    assert c.num_ctx == 64 and c.limit_chars == 42
    # not overridden → inherited from ai.*
    assert e.num_ctx == 2048 and e.limit_chars == 500
    assert c.timeout == 100 and c.temperature == 0.1


def test_enrichment_client_has_no_chat_prompt():
    s = Settings()
    s.ai_chat.chat_prompt = "CHAT {content}"
    e = make_enrichment_client(s)
    assert e.chat_prompt_template == ""
    assert e.embed_model == ""  # embedding layer belongs to AI Chat


def test_chat_client_has_no_enrichment_prompts():
    s = Settings()
    s.ai_enrichment.prompt = "ENR {content}"
    s.ai_enrichment.ai_prompt = "AI {content}"
    c = make_chat_client(s)
    assert c.prompt_template == "" and c.ai_prompt_template == ""
    assert c.topic_prompt_template == "" and c.map_prompt_template == ""


def test_embed_model_belongs_to_chat():
    s = Settings()
    s.ai_chat.embed_model = "emb-x"
    assert make_chat_client(s).embed_model == "emb-x"


# ── prompt modal exposure / isolation ───────────────────────────────────────


def test_prompts_expose_chat_and_folders():
    app = UIApp()
    p = app.get_prompts()
    assert p["chat_prompt"] == DEFAULT_CHAT_PROMPT
    assert p["folders_prompt"] == DEFAULT_FOLDERS_ANALYZE_PROMPT
    assert p["default_chat_prompt"] == DEFAULT_CHAT_PROMPT
    assert p["default_folders_prompt"] == DEFAULT_FOLDERS_ANALYZE_PROMPT


def test_set_prompt_routes_to_the_right_group(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"

    app.set_prompt("folders_prompt", "F {content}")
    app.set_prompt("chat_prompt", "C {content}")

    t = Settings.load(tmp_path / "config.yml")
    assert t.ai_enrichment.folders_prompt == "F {content}"
    assert t.ai_chat.chat_prompt == "C {content}"
    # isolation: neither leaks into the other group
    assert t.ai_chat.chat_prompt != "F {content}"
    assert t.ai_enrichment.folders_prompt != "C {content}"


def test_reset_prompt_new_kinds(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_prompt("chat_prompt", "x")
    assert app.reset_prompt("chat_prompt")["value"] == DEFAULT_CHAT_PROMPT
    app.set_prompt("folders_prompt", "x")
    assert app.reset_prompt("folders_prompt")["value"] == DEFAULT_FOLDERS_ANALYZE_PROMPT


# ── folder review runs as Enrichment (declared change) ──────────────────────


class _ReviewLLM:
    """Enrichment client stand-in: records the system prompt, names the model."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.chat_model = ""
        self.systems: list[str] = []

    def chat(self, messages, system, **kwargs):  # noqa: ARG002
        self.systems.append(system)
        return "обзор готов"


def _wait_idle(app: UIApp, timeout: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while app._busy and time.monotonic() < deadline:
        time.sleep(0.02)
    return not app._busy


def test_review_uses_enrichment_model_and_folders_prompt(monkeypatch, tmp_path):
    root = tmp_path / "Проект"
    (root / "Чертежи").mkdir(parents=True)
    (root / "Чертежи" / "план.dwg").write_text("x", encoding="utf-8")

    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"  # never write the repo config
    app.settings.llm_enabled = True
    app.settings.ai_enrichment.model = "ENR-MODEL"
    app.settings.ai_enrichment.folders_prompt = "FOLDERS {content}"

    fake = _ReviewLLM(model="ENR-MODEL")
    monkeypatch.setattr("obsidianizer.ui._make_enrichment_llm", lambda s: fake)

    r = app.review_run({"path": str(root), "rels": [""], "include_text": False})
    assert r["ok"] is True
    assert _wait_idle(app)

    review = root / "Проект_обзор.md"
    assert review.is_file()
    text = review.read_text(encoding="utf-8")
    assert "model: ENR-MODEL" in text  # declared change: was chat_model before 010
    assert fake.systems and fake.systems[0] == "FOLDERS {content}"


# ── AI Chat is not Enrichment ───────────────────────────────────────────────


class _ChatLLM:
    def __init__(self) -> None:
        self.model = "CHAT-MODEL"
        self.chat_model = ""

    def chat(self, messages, system, **kwargs):  # noqa: ARG002
        return "ответ"

    def embed(self, texts):  # noqa: ARG002
        return None


def test_chat_run_creates_no_enrichment_notes(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    enriched = tmp_path / "enriched"

    app = UIApp()
    app.settings.source = tmp_path / "source"
    app.settings.target = processed
    app.settings.enriched = enriched
    app.settings.llm_enabled = True

    monkeypatch.setattr("obsidianizer.ui._make_chat_llm", lambda s: _ChatLLM())

    app.run_chat_now(app.settings, "привет", [])

    # chat writes no notes into enriched (only its derived search index dir)
    notes = [p for p in enriched.rglob("*.md")] if enriched.exists() else []
    assert notes == []
