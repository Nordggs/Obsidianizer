"""UI bridge tests — headless (no webview window)."""

import json
import shutil
import time
from pathlib import Path

import httpx

from obsidianizer.config import (
    DEFAULT_AI_PROMPT,
    DEFAULT_PROMPT,
    DEFAULT_TOPIC_MAP_PROMPT,
    DEFAULT_TOPIC_PROMPT,
    Settings,
)
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


def test_defaults_expose_enriched():
    app = UIApp()
    d = app.defaults()
    assert "enriched" in d
    assert d["enriched"]
    assert app.settings.enriched == Path(d["enriched"])


def test_set_paths_accepts_disjoint(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    src = tmp_path / "src"
    tgt = tmp_path / "dst"
    enc = tmp_path / "enriched"
    r = app.set_paths(str(src), str(tgt), str(enc))
    assert r["ok"] is True
    assert app.settings.source.resolve() == src.resolve()
    assert app.settings.enriched.resolve() == enc.resolve()


def test_set_paths_defaults_enriched_to_target_sibling(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    src = tmp_path / "src"
    tgt = tmp_path / "dst"
    r = app.set_paths(str(src), str(tgt), "")
    assert r["ok"] is True
    assert app.settings.enriched.resolve() == (tgt.parent / "enriched").resolve()


def test_set_paths_rejects_overlap(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    src = tmp_path / "src"
    tgt = src / "sub"
    enc = tmp_path / "enriched"
    r = app.set_paths(str(src), str(tgt), str(enc))
    assert r["ok"] is False
    assert "error" in r


def test_set_paths_rejects_enriched_inside_target(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    src = tmp_path / "src"
    tgt = tmp_path / "dst"
    enc = tgt / "enriched"
    r = app.set_paths(str(src), str(tgt), str(enc))
    assert r["ok"] is False, "AI stage would re-read its own output"


def test_set_paths_rejects_enriched_matching_source(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    src = tmp_path / "src"
    tgt = tmp_path / "dst"
    r = app.set_paths(str(src), str(tgt), str(src))
    assert r["ok"] is False, "AI must never write into raw"


def test_run_ai_now_llm_off_reports_critical(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.settings.enriched = tmp_path / "enriched"
    report = app.run_ai_now(app.settings)
    assert report.processed == 0
    assert report.critical_error
    assert app.events[-1].type is EventType.AI_FINISHED


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
        self.directory = None

    def create_file_dialog(self, dialog_type, directory=""):  # noqa: ARG002
        self.directory = directory
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


def test_defaults_include_persistent_flags():
    app = UIApp()
    app.settings.prune = True
    app.settings.prune_enriched = True
    app.settings.dry_run = True
    d = app.defaults()
    assert d["prune"] is True
    assert d["prune_enriched"] is True
    assert d["dry_run"] is True


def test_choose_folder_uses_own_directory(tmp_path):
    app = UIApp()
    src = tmp_path / "raw"
    tgt = tmp_path / "processed"
    enc = tmp_path / "enriched"
    src.mkdir()
    tgt.mkdir()
    enc.mkdir()
    app.settings.source = src
    app.settings.target = tgt
    app.settings.enriched = enc
    w = _FakeWindow((str(tgt),))
    app._window = w
    app.choose_folder("target")
    assert Path(w.directory) == tgt
    app.choose_folder("enriched")
    assert Path(w.directory) == enc
    app.choose_folder("source")
    assert Path(w.directory) == src


def test_choose_folder_kind_defaults_to_source(tmp_path):
    app = UIApp()
    src = tmp_path / "raw"
    src.mkdir()
    app.settings.source = src
    w = _FakeWindow((str(src),))
    app._window = w
    app.choose_folder()
    assert Path(w.directory) == src


def test_choose_folder_falls_back_to_nearest_existing(tmp_path):
    app = UIApp()
    missing = tmp_path / "a" / "b" / "c"
    (tmp_path / "a").mkdir()
    app.settings.source = missing
    w = _FakeWindow(("D:\\x",))
    app._window = w
    app.choose_folder("source")
    assert Path(w.directory) == (tmp_path / "a")


def test_set_flags_persists_to_config(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_flags(True, True, True)
    t = Settings.load(tmp_path / "config.yml")
    assert t.dry_run is True
    assert t.prune is True
    assert t.prune_enriched is True


def test_set_paths_persists_to_config(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    src = tmp_path / "src"
    tgt = tmp_path / "dst"
    r = app.set_paths(str(src), str(tgt), "")
    assert r["ok"] is True
    t = Settings.load(tmp_path / "config.yml")
    assert t.source.resolve() == src.resolve()
    assert t.target.resolve() == tgt.resolve()


def test_set_llm_persists_to_config(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_llm(False, "qwen2.5")
    t = Settings.load(tmp_path / "config.yml")
    assert t.llm_enabled is False
    assert t.ollama["model"] == "qwen2.5"


class _FakeTagsResponse:
    def __init__(self, models) -> None:
        self._models = models

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return {"models": [{"name": n} for n in self._models]}


def test_list_models_returns_sorted_names(monkeypatch):
    monkeypatch.setattr(
        "obsidianizer.llm.httpx.get",
        lambda *a, **k: _FakeTagsResponse(["b:1", "a:1"]),
    )
    app = UIApp()
    r = app.list_models()
    assert r["ok"] is True
    assert r["models"] == ["a:1", "b:1"]


def test_list_models_unreachable_reports_ok_false(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("obsidianizer.llm.httpx.get", boom)
    app = UIApp()
    r = app.list_models()
    assert r["ok"] is False
    assert "error" in r


def test_get_prompts_exposes_both_templates():
    app = UIApp()
    p = app.get_prompts()
    assert "prompt" in p and "ai_prompt" in p
    assert p["prompt"] == DEFAULT_PROMPT
    assert p["ai_prompt"] == DEFAULT_AI_PROMPT
    assert p["default_prompt"] == DEFAULT_PROMPT
    assert p["default_ai_prompt"] == DEFAULT_AI_PROMPT


def test_set_prompt_persists_to_config(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    r = app.set_prompt("ai_prompt", "custom {content}")
    assert r["ok"] is True
    t = Settings.load(tmp_path / "config.yml")
    assert t.ollama["ai_prompt"] == "custom {content}"


def test_set_prompt_rejects_unknown_kind():
    app = UIApp()
    r = app.set_prompt("bogus", "x")
    assert r["ok"] is False


def test_reset_prompt_restores_builtin_default(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_prompt("ai_prompt", "custom")
    r = app.reset_prompt("ai_prompt")
    assert r["ok"] is True
    assert r["value"] == DEFAULT_AI_PROMPT
    assert Settings.load(tmp_path / "config.yml").ollama["ai_prompt"] == DEFAULT_AI_PROMPT


def test_reset_prompt_restores_import_default(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_prompt("prompt", "custom")
    r = app.reset_prompt("prompt")
    assert r["ok"] is True
    assert r["value"] == DEFAULT_PROMPT
    assert Settings.load(tmp_path / "config.yml").ollama["prompt"] == DEFAULT_PROMPT


def test_get_prompts_exposes_topic_template():
    app = UIApp()
    p = app.get_prompts()
    assert "topic_prompt" in p
    assert p["topic_prompt"] == DEFAULT_TOPIC_PROMPT
    assert p["default_topic_prompt"] == DEFAULT_TOPIC_PROMPT


def test_set_prompt_topic_persists_to_config(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    r = app.set_prompt("topic_prompt", "custom {content}")
    assert r["ok"] is True
    assert Settings.load(tmp_path / "config.yml").ollama["topic_prompt"] == "custom {content}"


def test_reset_prompt_topic_restores_default(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_prompt("topic_prompt", "custom")
    r = app.reset_prompt("topic_prompt")
    assert r["ok"] is True
    assert r["value"] == DEFAULT_TOPIC_PROMPT


def test_list_chats_lists_processed_files(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.run_now(app.settings)

    r = app.list_chats()

    assert r["ok"] is True
    assert len(r["files"]) == 2
    first = r["files"][0]
    assert first["rel"].endswith(".md")
    assert first["title"]
    assert "service" in first
    assert "messages" in first


def test_list_chats_empty_when_target_missing(tmp_path):
    app = UIApp()
    app.settings.target = tmp_path / "absent"
    r = app.list_chats()
    assert r["ok"] is True
    assert r["files"] == []


def test_run_topic_now_llm_off_reports_critical(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.settings.enriched = tmp_path / "enriched"

    report = app.run_topic_now(app.settings, ["a.md"])

    assert report.critical_error
    assert app.events[-1].type is EventType.TOPIC_FINISHED


def test_get_prompts_exposes_map_template():
    app = UIApp()
    p = app.get_prompts()
    assert "map_prompt" in p
    assert p["map_prompt"] == DEFAULT_TOPIC_MAP_PROMPT
    assert p["default_map_prompt"] == DEFAULT_TOPIC_MAP_PROMPT


def test_set_prompt_map_persists_to_config(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    r = app.set_prompt("map_prompt", "custom {content}")
    assert r["ok"] is True
    assert Settings.load(tmp_path / "config.yml").ollama["map_prompt"] == "custom {content}"


def test_reset_prompt_map_restores_default(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.set_prompt("map_prompt", "custom")
    r = app.reset_prompt("map_prompt")
    assert r["ok"] is True
    assert r["value"] == DEFAULT_TOPIC_MAP_PROMPT


def test_run_group_all_now_llm_off_reports_critical(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.settings.enriched = tmp_path / "enriched"

    report = app.run_group_all_now(app.settings)

    assert report.critical_error
    assert app.events[-1].type is EventType.TOPIC_FINISHED


# ── topic lifecycle bridge ───────────────────────────────────────────────────


class _FakeTopicLLM:
    def __init__(self):
        self.result = {"name": "Тема Бридж", "summary": "суть темы"}

    def analyze_topic(self, payload):  # noqa: ARG002
        return dict(self.result)


def _topic_fixture(tmp_path):
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter, compose
    from obsidianizer.topics import create_topic

    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    processed.mkdir()
    meta = {"title": "Чат", "service": "test", "messages": {"total": 2}, "branches": 1}
    atomic_write(
        processed / "a.md",
        compose(build_frontmatter(meta, "", [], "H1"), build_card(meta, "", []), "тело"),
    )
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())
    app = _app(tmp_path / "source", processed)
    app.settings.enriched = enriched
    return app, processed, enriched


def test_bridge_topic_lifecycle(tmp_path):
    app, _, enriched = _topic_fixture(tmp_path)

    listing = app.list_topics()
    assert listing["ok"] is True and len(listing["topics"]) == 1
    tid = listing["topics"][0]["topic_id"]

    got = app.get_topic(tid)
    assert got["ok"] is True
    assert got["topic"]["chats"] == ["a.md"]
    assert "суть темы" in got["topic"]["body"]

    rn = app.rename_topic(tid, "Переименованная")
    assert rn["ok"] is True
    assert rn["name"] == "Переименованная"
    assert (enriched / "topics" / "Переименованная.md").is_file()
    assert not (enriched / "topics" / "Тема Бридж.md").exists()

    dl = app.delete_topic(tid)
    assert dl["ok"] is True
    assert not (enriched / "topics" / "Переименованная.md").exists()
    assert app.list_topics()["topics"] == []


def test_bridge_chats_without_topic(tmp_path):
    from obsidianizer.topics import create_topic
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter, compose

    processed = tmp_path / "processed"
    enriched = tmp_path / "enriched"
    processed.mkdir()
    meta = {"title": "Чат", "service": "test", "messages": {"total": 2}, "branches": 1}
    atomic_write(
        processed / "a.md",
        compose(build_frontmatter(meta, "", [], "H1"), build_card(meta, "", []), "тело"),
    )
    atomic_write(
        processed / "b.md",
        compose(build_frontmatter(meta, "", [], "H2"), build_card(meta, "", []), "тело2"),
    )
    create_topic(processed, enriched, ["a.md"], _FakeTopicLLM())

    app = _app(tmp_path / "source", processed)
    app.settings.enriched = enriched

    orphans = app.chats_without_topic()
    assert orphans["ok"] is True
    assert [c["rel"] for c in orphans["files"]] == ["b.md"]


def test_bridge_update_topic_now_llm_off_reports_critical(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.settings.enriched = tmp_path / "enriched"

    report = app.run_update_topic_now(app.settings, "tid", ["a.md"])

    assert report.critical_error
    assert app.events[-1].type is EventType.TOPIC_FINISHED


# ── assistant chat bridge ────────────────────────────────────────────────────


class _FakeChatLLM:
    def __init__(self):
        self.systems = []
        self.messages = []

    def chat(self, messages, system, **kwargs):  # noqa: ARG002
        self.systems.append(system)
        self.messages.append(messages)
        return "ответ ассистента"


def test_chat_history_and_clear(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app._chat_history = [{"role": "user", "content": "x"}]

    assert app.chat_history()["messages"] == [{"role": "user", "content": "x"}]
    assert app.chat_clear()["ok"] is True
    assert app.chat_history()["messages"] == []


def test_chat_context_set_get_and_clear(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    assert app.chat_context()["rels"] == []
    assert app.set_chat_context(["a/1.md", "b\\2.md"])["ok"] is True
    assert app.chat_context()["rels"] == ["a/1.md", "b/2.md"]
    assert app.set_chat_context([])["ok"] is True
    assert app.chat_context()["rels"] == []


def test_chat_send_uses_bridge_context_when_not_given(monkeypatch, tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.settings.llm_enabled = True
    app._chat_context = ["a/1.md"]
    captured = {}

    def fake(self_, s, message, context):  # noqa: ARG001
        captured["context"] = context

    monkeypatch.setattr(UIApp, "_run_chat_worker", fake)
    res = app.chat_send({"message": "привет"})

    assert res["ok"] is True
    assert captured["context"] == ["a/1.md"]


def test_open_chat_window_headless_noop(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    res = app.open_chat_window()

    assert res["ok"] is True
    assert res["opened"] is False
    assert app._chat_window is None


def test_open_help_window_headless_noop(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    res = app.open_help_window("obsidianize")

    assert res["ok"] is True
    assert res["opened"] is False
    assert app._help_window is None

    res = app.close_help_window()
    assert res["ok"] is True


def test_send_chat_topic_request_headless(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    assert app.send_chat_topic_request([])["ok"] is False
    res = app.send_chat_topic_request(["a/1.md", "b\\2.md"])
    assert res["ok"] is True
    assert res["count"] == 2


def test_send_chat_context_request_headless(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    assert app.send_chat_context_request()["ok"] is True


def test_chat_run_llm_off_reports_error(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    reply = app.run_chat_now(app.settings, "привет", [])

    assert reply == ""
    assert app._chat_history == [], "failed turn must leave no trace"
    assert app.events[-1].type is EventType.CHAT_ERROR


def test_chat_worker_replies_and_keeps_history(monkeypatch, tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)
    app.settings.enriched = tmp_path / "enriched"
    app.settings.llm_enabled = True
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    reply = app.run_chat_now(app.settings, "привет", [])

    assert reply == "ответ ассистента"
    assert app._chat_history == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "ответ ассистента"},
    ]
    assert app.events[-1].type is EventType.CHAT_REPLY


def test_chat_worker_attaches_note_context(monkeypatch, tmp_path):
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter, compose

    processed = tmp_path / "processed"
    processed.mkdir()
    meta = {"title": "Контекстная", "service": "test", "messages": {"total": 2}, "branches": 1}
    atomic_write(
        processed / "ctx.md",
        compose(build_frontmatter(meta, "", [], "H1"), build_card(meta, "", []), "тело для контекста"),
    )
    app = _app(tmp_path / "source", processed)
    app.settings.enriched = tmp_path / "enriched"
    app.settings.llm_enabled = True
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    app.run_chat_now(app.settings, "что там?", ["ctx.md"])

    assert "Контекстная" in fake.systems[0]
    assert "ctx.md" in fake.systems[0]
    assert "тело для контекста" in fake.systems[0]


def _note_file(root, rel, source_hash, title, body):
    from obsidianizer.emit import atomic_write
    from obsidianizer.enrich import build_card, build_frontmatter, compose

    meta = {"title": title, "service": "test", "messages": {"total": 2}, "branches": 1}
    atomic_write(
        root / rel,
        compose(build_frontmatter(meta, "", [], source_hash), build_card(meta, "", []), body),
    )


def test_chat_search_feeds_candidates_and_emits_found(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    _note_file(
        processed,
        "chatgpt/skin.md",
        "S1",
        "Искусственная кожа",
        "обсуждали китайских производителей экокожи и фабрики в Гуанчжоу",
    )
    _note_file(
        processed,
        "deepseek/other.md",
        "S2",
        "Другое",
        "просто текст без нужных слов",
    )
    app = _app(tmp_path / "source", processed)
    app.settings.enriched = tmp_path / "enriched"
    app.settings.llm_enabled = True
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    app.run_chat_now(app.settings, "китайские фабрики искусственной кожи", [])

    assert "Результаты поиска по коллекции" in fake.systems[0]
    assert "chatgpt/skin.md" in fake.systems[0]
    assert "Искусственная кожа" in fake.systems[0]
    found_events = [e for e in app.events if e.type is EventType.CHAT_FOUND]
    assert found_events, "CHAT_FOUND must be emitted when candidates exist"
    import json

    payload = json.loads(found_events[-1].message)
    assert payload[0]["rel"] == "chatgpt/skin.md"
    assert app.chat_found()["files"][0]["rel"] == "chatgpt/skin.md"


def test_chat_search_no_hits_emits_nothing(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    _note_file(processed, "qwen/a.md", "S1", "Поездка", "список дел на выходные")
    app = _app(tmp_path / "source", processed)
    app.settings.enriched = tmp_path / "enriched"
    app.settings.llm_enabled = True
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    app.run_chat_now(app.settings, "несуществующий термин зеленая лампа", [])

    assert not [e for e in app.events if e.type is EventType.CHAT_FOUND]
    assert app.chat_found()["files"] == []
    assert "Результаты поиска по коллекции" not in fake.systems[0]


def test_chat_search_refreshes_index_once(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    _note_file(processed, "a.md", "S1", "Тема A", "фабрики кожа")
    app = _app(tmp_path / "source", processed)
    app.settings.enriched = tmp_path / "enriched"
    app.settings.llm_enabled = True
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    app.run_chat_now(app.settings, "фабрики", [])
    app.run_chat_now(app.settings, "кожа", [])

    assert app._index is not None
    assert (app.settings.enriched / ".obsidianizer" / "index.json").is_file()


def test_open_note_missing_returns_false(tmp_path):
    source, target = _prepare(tmp_path)
    app = _app(source, target)

    assert app.open_note("нет/такого/файла.md") is False


def test_open_note_existing_opens(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    _note_file(processed, "deepseek/x.md", "S1", "Чат", "тело")
    app = _app(tmp_path / "source", processed)
    opened = []
    monkeypatch.setattr("obsidianizer.ui.os.startfile", lambda p: opened.append(str(p)))

    assert app.open_note("deepseek/x.md") is True
    assert opened and opened[0].endswith("x.md")


def test_chat_attaches_full_sources_and_fragments(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    body = (
        "обычный текст " * 150
        + "\n\nв финале выбрали реликвию реки relic of rivers для спелбрейкера dragonhunter"
    )
    _note_file(processed, "deepseek/gw2.md", "S1", "GW2 билды", body)
    _note_file(processed, "qwen/other.md", "S2", "Другое", "просто текст")
    app = _app(tmp_path / "source", processed)
    app.settings.enriched = tmp_path / "enriched"
    app.settings.llm_enabled = True
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    app.run_chat_now(app.settings, "какой relic выбрали для dragonhunter", [])

    system = fake.systems[0]
    assert "Полные тексты наиболее релевантных чатов" in system
    assert "deepseek/gw2.md" in system
    assert "relic of rivers" in system.lower(), "the decisive fragment must reach the model"
    found = app.chat_found()["files"]
    assert found[0]["rel"] == "deepseek/gw2.md"
    assert found[0]["full"] is True


# ── Folder Obsidianizer (tab 1) ───────────────────────────────────────────

def _obs_folder(tmp_path: Path, name: str = "Проект") -> Path:
    root = tmp_path / name
    (root / "Арх").mkdir(parents=True)
    (root / "Чертежи").mkdir(parents=True)
    (root / "Чертежи" / "План.dwg").write_text("x", encoding="utf-8")
    (root / "Таблица.xlsx").write_text("x", encoding="utf-8")
    (root / "План_этажа.png").write_text("x", encoding="utf-8")
    return root


def _obs_app(tmp_path: Path) -> UIApp:
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    app.settings.obsidianize_dir = ""
    app.settings.obsidianize_vault_root = ""
    return app


def _wait_idle(app: UIApp, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while app._busy and time.monotonic() < deadline:
        time.sleep(0.02)
    return not app._busy


def test_defaults_expose_obsidianize_paths():
    app = UIApp()
    d = app.defaults()
    assert "obsidianize_dir" in d
    assert "obsidianize_vault_root" in d


def test_set_obsidianize_settings_persist(tmp_path):
    app = UIApp()
    app.settings.config_path = tmp_path / "config.yml"
    r = app.set_obsidianize_dir(str(tmp_path / "a"))
    assert r["ok"] is True
    r = app.set_obsidianize_vault_root(str(tmp_path / "vault"))
    assert r["ok"] is True
    loaded = Settings.load(tmp_path / "config.yml")
    assert loaded.obsidianize_dir == str(tmp_path / "a")
    assert loaded.obsidianize_vault_root == str(tmp_path / "vault")


def test_obs_scan_reports_folders_and_missing_cards(tmp_path):
    root = _obs_folder(tmp_path)
    app = _obs_app(tmp_path)
    r = app.obs_scan(str(root))
    assert r["ok"] is True
    assert r["root"] == str(root)
    rels = {f["rel"] for f in r["folders"]}
    assert rels == {"", "Арх", "Чертежи"}
    by_rel = {f["rel"]: f for f in r["folders"]}
    drafting = by_rel["Чертежи"]
    assert drafting["files"] == 1
    assert drafting["categories"]["drafting"] == 1
    assert drafting["card"] == "missing"
    root_entry = by_rel[""]
    assert root_entry["categories"]["tables"] == 1
    assert root_entry["categories"]["images"] == 1
    assert root_entry["subfolders"] == 2


def test_obs_scan_rejects_missing_dir(tmp_path):
    app = _obs_app(tmp_path)
    r = app.obs_scan(str(tmp_path / "absent"))
    assert r["ok"] is False
    r = app.obs_scan("")
    assert r["ok"] is False


def test_obs_obsidianize_creates_cards_and_events(tmp_path):
    root = _obs_folder(tmp_path)
    app = _obs_app(tmp_path)
    r = app.obs_obsidianize({"path": str(root), "recursive": True, "gallery": True})
    assert r["ok"] is True
    assert _wait_idle(app)
    assert (root / "Проект.md").exists()
    assert (root / "Чертежи" / "Чертежи.md").exists()

    kinds = [e.type for e in app.events]
    assert kinds[0] is EventType.OBS_SCAN_STARTED
    assert kinds[-1] is EventType.OBS_FINISHED
    actions = [e.message for e in app.events if e.type is EventType.OBS_FOLDER_DONE]
    assert actions.count("created") == 3
    assert actions.count("conflict") == 0

    summary = json.loads(app.events[-1].message)
    assert summary["scanned"] == 3
    assert summary["created"] == 3

    scanned = app.obs_scan(str(root))
    statuses = {f["rel"]: f["card"] for f in scanned["folders"]}
    assert statuses[""] == "ok"
    assert statuses["Чертежи"] == "ok"


def test_obs_obsidianize_second_run_skips(tmp_path):
    root = _obs_folder(tmp_path)
    app = _obs_app(tmp_path)
    app.obs_obsidianize({"path": str(root), "recursive": True, "gallery": True})
    assert _wait_idle(app)
    app.obs_obsidianize({"path": str(root), "recursive": True, "gallery": True})
    assert _wait_idle(app)
    summary = json.loads(app.events[-1].message)
    assert summary["created"] == 0
    assert summary["updated"] == 0
    assert summary["skipped"] == 3


def test_obs_obsidianize_rejects_missing_dir(tmp_path):
    app = _obs_app(tmp_path)
    r = app.obs_obsidianize({"path": str(tmp_path / "absent")})
    assert r["ok"] is False
    assert "error" in r


def test_obs_open_folder_headless_returns_error(tmp_path):
    app = _obs_app(tmp_path)
    r = app.obs_open_folder(str(tmp_path))
    assert r["ok"] is False
    assert "нет окна" in r["error"]


# ── AI folder review (tab 3) ──────────────────────────────────────────────

def _review_app(tmp_path: Path) -> UIApp:
    app = _obs_app(tmp_path)
    app.settings.llm_enabled = True
    return app


def test_review_run_creates_reviews(monkeypatch, tmp_path):
    root = _obs_folder(tmp_path)
    (root / "Пояснительная.txt").write_text(
        "насосы Wilo, давление 6 бар", encoding="utf-8"
    )
    app = _review_app(tmp_path)
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    r = app.review_run({"path": str(root), "rels": ["", "Чертежи"], "include_text": True})
    assert r["ok"] is True
    assert _wait_idle(app)

    review = root / "Проект_обзор.md"
    assert review.exists()
    assert (root / "Чертежи" / "Чертежи_обзор.md").exists()
    text = review.read_text(encoding="utf-8")
    assert "type: обзор" in text
    assert "generated:" in text
    assert "ответ ассистента" in text

    kinds = [e.type for e in app.events]
    assert kinds[0] is EventType.REVIEW_STARTED
    assert kinds[-1] is EventType.REVIEW_FINISHED
    done = [e for e in app.events if e.type is EventType.REVIEW_FOLDER_DONE]
    assert [e.message for e in done] == ["ok", "ok"]
    summary = json.loads(app.events[-1].message)
    assert summary["ok"] == 2
    assert summary["errors"] == 0
    assert len(summary["files"]) == 2
    user_msg = fake.messages[0][0]["content"]
    assert "насосы Wilo" in user_msg, "text file content must reach the model"
    assert "Проект" in user_msg


def test_review_run_skips_text_content_when_disabled(monkeypatch, tmp_path):
    root = _obs_folder(tmp_path)
    (root / "Пояснительная.txt").write_text(
        "насосы Wilo, давление 6 бар", encoding="utf-8"
    )
    app = _review_app(tmp_path)
    fake = _FakeChatLLM()
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: fake)

    app.review_run({"path": str(root), "rels": [""], "include_text": False})
    assert _wait_idle(app)
    user_msg = fake.messages[0][0]["content"]
    assert "насосы Wilo" not in user_msg
    assert "не запрашивалось" in user_msg


def test_review_run_requires_selection(tmp_path):
    app = _review_app(tmp_path)
    root = _obs_folder(tmp_path)
    r = app.review_run({"path": str(root), "rels": []})
    assert r["ok"] is False
    assert "выберите" in r["error"]


def test_review_run_llm_off_reports_error(tmp_path):
    app = _obs_app(tmp_path)
    app.settings.llm_enabled = False
    root = _obs_folder(tmp_path)
    r = app.review_run({"path": str(root), "rels": [""]})
    assert r["ok"] is False
    assert "LLM" in r["error"]


def test_review_run_unknown_rel_reports_error(monkeypatch, tmp_path):
    root = _obs_folder(tmp_path)
    app = _review_app(tmp_path)
    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: _FakeChatLLM())
    r = app.review_run({"path": str(root), "rels": ["нет-такой-папки"]})
    assert r["ok"] is True
    assert _wait_idle(app)
    assert not (root / "Проект_обзор.md").exists()
    done = [e for e in app.events if e.type is EventType.REVIEW_FOLDER_DONE]
    assert [e.message for e in done] == ["error"]
    summary = json.loads(app.events[-1].message)
    assert summary["ok"] == 0
    assert summary["errors"] == 1


def test_review_run_empty_reply_reports_error(monkeypatch, tmp_path):
    root = _obs_folder(tmp_path)
    app = _review_app(tmp_path)

    class _SilentLLM:
        def chat(self, messages, system, **kwargs):  # noqa: ARG002
            return ""

    monkeypatch.setattr("obsidianizer.ui._make_llm", lambda s: _SilentLLM())
    app.review_run({"path": str(root), "rels": [""]})
    assert _wait_idle(app)
    assert not (root / "Проект_обзор.md").exists()
    summary = json.loads(app.events[-1].message)
    assert summary["ok"] == 0
    assert summary["errors"] == 1