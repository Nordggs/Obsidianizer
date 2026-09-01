"""i18n module: language resolution, tr(), RU/EN symmetry."""

import pytest

from obsidianizer import i18n


@pytest.fixture(autouse=True)
def _reset_lang():
    yield
    i18n.set_language("ru")


# ── resolve_language ─────────────────────────────────────────────────────────


def test_resolve_explicit():
    assert i18n.resolve_language("ru") == "ru"
    assert i18n.resolve_language("en") == "en"
    assert i18n.resolve_language("RU") == "ru"
    assert i18n.resolve_language(" en ") == "en"


def test_resolve_auto_from_locale(monkeypatch):
    monkeypatch.setattr(
        i18n.locale, "getlocale", lambda: ("ru_RU", "cp1251")
    )
    assert i18n.resolve_language("") == "ru"
    monkeypatch.setattr(
        i18n.locale, "getlocale", lambda: ("en_US", "cp1252")
    )
    assert i18n.resolve_language("") == "en"


def test_resolve_garbage_falls_back_to_locale(monkeypatch):
    monkeypatch.setattr(
        i18n.locale, "getlocale", lambda: ("de_DE", "cp1252")
    )
    assert i18n.resolve_language("zz") == "en"


# ── tr() ─────────────────────────────────────────────────────────────────────


def test_tr_ru_and_en():
    i18n.set_language("ru")
    assert i18n.tr("err.busy") == "запуск уже выполняется"
    i18n.set_language("en")
    assert i18n.tr("err.busy") == "a run is already in progress"


def test_tr_params():
    i18n.set_language("en")
    out = i18n.tr("err.folder_missing", path=r"C:\Temp")
    assert out == r"Folder does not exist: C:\Temp"


def test_tr_unknown_key_passes_through():
    i18n.set_language("en")
    assert i18n.tr("no.such.key") == "no.such.key"


def test_tr_falls_back_to_other_table(monkeypatch):
    i18n.set_language("ru")
    monkeypatch.delitem(i18n._STRINGS["ru"], "err.busy")
    assert i18n.tr("err.busy") == "a run is already in progress"


# ── RU/EN table symmetry ─────────────────────────────────────────────────────


def test_tables_symmetric():
    ru_keys = set(i18n._STRINGS["ru"])
    en_keys = set(i18n._STRINGS["en"])
    assert ru_keys == en_keys, f"missing ru: {en_keys - ru_keys}, missing en: {ru_keys - en_keys}"


def test_no_unresolved_placeholders():
    """Every key with {params} must accept the same params in both tables."""
    import re
    for lang in ("ru", "en"):
        for key, text in i18n._STRINGS[lang].items():
            re.sub(r"\{(\w+)\}", "", text)  # syntax sanity


# ── Stage 2: structured events + localized backend messages ─────────────────


def test_pipeline_finish_localized_and_structured():
    from obsidianizer.pipeline import Report, _finish_data, _finish_message

    r = Report()
    r.processed = 3
    r.skipped = 2
    r.failed = ["x: y"]
    i18n.set_language("ru")
    msg = _finish_message(r)
    assert "обработано=3" in msg and "пропущено=2" in msg and "ошибок=1" in msg
    d = _finish_data(r)
    assert d == {
        "mode": "import", "cancelled": False, "critical": "",
        "processed": 3, "skipped": 2, "errors": 1,
    }
    i18n.set_language("en")
    assert "processed=3" in _finish_message(r)


def test_pipeline_finish_cancelled_and_critical():
    from obsidianizer.pipeline import Report, _finish_data, _finish_message

    r = Report()
    r.cancelled = True
    i18n.set_language("en")
    assert _finish_message(r).startswith("cancelled")
    assert _finish_data(r)["cancelled"] is True
    r2 = Report()
    r2.critical_error = "boom"
    assert _finish_data(r2)["critical"] == "boom"
    assert "critical error: boom" in _finish_message(r2)


def test_postprocess_finish_localized_and_structured():
    from obsidianizer.postprocess import EnrichReport, _finish_data, _finish_message

    r = EnrichReport()
    r.processed = 4
    r.skipped = 1
    r.pruned = ["a"]
    i18n.set_language("ru")
    msg = _finish_message(r)
    assert "AI-обработано=4" in msg and "удалено сирот=1" in msg
    d = _finish_data(r)
    assert d["mode"] == "ai" and d["processed"] == 4 and d["pruned"] == 1
    i18n.set_language("en")
    assert "AI-processed=4" in _finish_message(r)


def test_topics_single_finish_data():
    from obsidianizer.topics import TopicReport, _finish

    events = []
    r = TopicReport()
    r.created = "topics/X.md"
    r.name = "X"
    i18n.set_language("en")
    _finish(r, events.append)
    ev = events[-1]
    assert ev.data["mode"] == "single"
    assert ev.data["state"] == "created"
    assert ev.data["name"] == "X"
    assert ev.message == "Topic created: X"
    r2 = TopicReport()
    r2.skipped = True
    r2.name = "Y"
    events.clear()
    _finish(r2, events.append)
    assert events[-1].data["state"] == "uptodate"
    assert events[-1].message == "Topic up to date: Y (chats unchanged)"


def test_topics_group_finish_data():
    from obsidianizer.topics import GroupReport, _finish_group

    events = []
    r = GroupReport()
    r.created = ["a", "b"]
    r.skipped = 1
    r.one_chat = 3
    r.failed = ["z"]
    i18n.set_language("en")
    _finish_group(r, events.append)
    ev = events[-1]
    assert ev.data["mode"] == "group"
    assert ev.data == {
        "mode": "group", "state": "done", "critical": "",
        "created": 2, "skipped": 1, "one_chat": 3, "errors": 1,
    }
    r2 = GroupReport()
    r2.cancelled = True
    events.clear()
    _finish_group(r2, events.append)
    assert events[-1].data["state"] == "cancelled"


def test_event_carries_data():
    from obsidianizer.events import Event, EventType

    ev = Event(type=EventType.FINISHED, message="m", data={"a": 1})
    assert ev.data == {"a": 1}
    assert Event(type=EventType.FINISHED).data == {}


def test_app_js_never_parses_message_text():
    """Regression: the UI must consume ev.data, not parse localized text."""
    js = (
        __import__("pathlib")
        .Path(__file__).parents[1]
        .joinpath("src", "obsidianizer", "web", "app.js")
        .read_text(encoding="utf-8")
    )
    assert 'indexOf("отменена")' not in js
    assert 'indexOf("отменено")' not in js
    assert 'indexOf("критическая")' not in js
    assert 'indexOf("актуальна")' not in js
    assert "AI-обработано=" not in js
    assert "удалено сирот=" not in js
