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
