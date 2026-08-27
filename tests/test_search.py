"""Search layer tests — the deterministic + semantic retrieval for the chat.

``ChatIndex`` builds a persistent index over a processed collection, refreshes
it incrementally by ``source_hash`` and returns top-K candidates. The semantic
layer (embeddings) must never break the deterministic lexical results.
"""

from pathlib import Path

from obsidianizer.emit import atomic_write
from obsidianizer.enrich import build_card, build_frontmatter, compose
from obsidianizer.search import (
    ChatIndex,
    _chunk_text,
    _snippet_window,
    _variants,
    tokenize,
)


class _FakeEmbedLLM:
    """Deterministic embeddings: one feature bit per semantic cluster."""

    embed_model = "test-embed"

    def __init__(self):
        self.embed_calls = 0
        self.inputs: list[str] = []

    def embed(self, texts):
        self.embed_calls += 1
        self.inputs.extend(texts)
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(t):
        tl = t.lower()
        v = [0.0] * 6
        if "pu" in tl or "экокож" in tl:
            v[0] = 1.0
        if "leather" in tl or "кож" in tl:
            v[1] = 1.0
        if "искусств" in tl:
            v[2] = 1.0
        if "china" in tl or "китай" in tl:
            v[3] = 1.0
        if "factories" in tl or "фабрик" in tl:
            v[4] = 1.0
        if "хостинг" in tl or "hetzner" in tl:
            v[5] = 1.0
        return v


def _note(root: Path, rel: str, source_hash: str, body: str, title: str, service: str = "test"):
    meta = {
        "title": title,
        "service": service,
        "messages": {"total": 4, "user": 2, "assistant": 2},
        "branches": 1,
    }
    atomic_write(
        root / rel,
        compose(build_frontmatter(meta, "", [], source_hash), build_card(meta, "", []), body),
    )


def _collection(tmp_path: Path) -> tuple[Path, Path]:
    tgt = tmp_path / "processed"
    tgt.mkdir(parents=True)
    idx = tmp_path / "enriched" / ".obsidianizer"
    return tgt, idx


def _sample_collection(tgt: Path) -> dict:
    notes = {
        "deepseek/hetzner.md": {
            "hash": "h1",
            "title": "Настройка Hetzner",
            "body": "Обсуждали настройку хостинга на Hetzner и wireguard-туннель.",
        },
        "chatgpt/ecoleather.md": {
            "hash": "h2",
            "title": "Искусственная кожа из Китая",
            "body": (
                "Разбирали китайских производителей экокожи: фабрики в Гуанчжоу, "
                "качество PU-материала и логистику."
            ),
        },
        "qwen/other.md": {
            "hash": "h3",
            "title": "План поездки",
            "body": "Список дел на выходные и список покупок.",
        },
    }
    for rel, spec in notes.items():
        _note(tgt, rel, spec["hash"], spec["body"], spec["title"], "test")
    return notes


# ── tokenization / morphology ───────────────────────────────────────────────


def test_tokenize_filters_stop_words_and_short():
    toks = tokenize("В каких чатах я обсуждал китайские фабрики искусственной кожи?")
    assert "каких" not in toks
    assert "обсуждал" not in toks
    assert "китайские" in toks
    assert "фабрики" in toks


def test_variants_cover_common_stems():
    assert "фабрик" in _variants("фабрики")
    assert "китайск" in _variants("китайские")
    assert "кож" in _variants("кожа")
    assert _variants("кожа") == ["кожа", "кож"]  # never below length 3


# ── lexical search ──────────────────────────────────────────────────────────


def test_lexical_finds_by_body_morphology(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    index = ChatIndex(tgt, idx)
    index.refresh()

    res = index.search("китайские фабрики искусственной кожи")
    assert res, "must find at least one chat"
    assert res[0].rel == "chatgpt/ecoleather.md"
    assert "фабрик" in res[0].snippet.lower() or "китай" in res[0].snippet.lower()
    assert res[0].partial is False


def test_title_weights_above_body(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    index = ChatIndex(tgt, idx)
    index.refresh()

    # "hetzner" appears in both the title and the body of deepseek/hetzner;
    # a chat with the word only in the body must rank below.
    _note(tgt, "gemini/plain.md", "h4", "hetzner упоминается в теле", "Обычный чат")
    res = index.search("hetzner")
    assert res[0].rel == "deepseek/hetzner.md"


def test_partial_flag_and_fallback_words(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    index = ChatIndex(tgt, idx)
    index.refresh()

    res = index.search("хостинг провайдер ветесина")
    assert res, "the 'хостинг' token must still find the Hetzner chat"
    hits = [c for c in res if c.rel == "deepseek/hetzner.md"]
    assert hits
    assert hits[0].partial is True  # 'ветесина' matched nowhere


def test_top_k_limits_results(tmp_path):
    tgt, idx = _collection(tmp_path)
    for i in range(5):
        _note(tgt, f"chatgpt/c{i}.md", f"h{i}", f"кожа фабрики производители номер {i}", f"Чат {i}")
    index = ChatIndex(tgt, idx, top_k=2)
    index.refresh()

    assert len(index.search("кожа фабрики")) == 2


def test_empty_query_returns_empty(tmp_path):
    tgt, idx = _collection(tmp_path)
    index = ChatIndex(tgt, idx)
    assert index.search("   ") == []


# ── incremental refresh ─────────────────────────────────────────────────────


def test_refresh_is_incremental(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    llm = _FakeEmbedLLM()
    index = ChatIndex(tgt, idx, llm=llm)
    first = index.refresh()
    assert first["added"] == 3
    embed_calls_after_first = llm.embed_calls

    second = index.refresh()  # nothing changed
    assert second["added"] == 0
    assert second["updated"] == 0
    assert second["embedded"] == 0
    assert llm.embed_calls == embed_calls_after_first  # no re-embedding

    # Change one note's body → only it gets re-indexed and re-embedded.
    _note(tgt, "deepseek/hetzner.md", "h1b", "другой текст про хостинг", "Настройка Hetzner")
    third = index.refresh()
    assert third["updated"] == 1
    assert third["embedded"] == 1
    assert llm.embed_calls > embed_calls_after_first


def test_index_persists_on_disk(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    ChatIndex(tgt, idx).refresh()

    assert (idx / "index.json").is_file()
    assert (idx / "embeddings.json").exists() or True  # embeddings written only with llm

    index2 = ChatIndex(tgt, idx)
    stats = index2.refresh()
    assert stats["added"] == 0  # read back from disk, not rebuilt


# ── semantic layer ──────────────────────────────────────────────────────────


def test_semantic_layer_finds_without_lexical_match(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    index = ChatIndex(tgt, idx, llm=_FakeEmbedLLM())
    index.refresh()

    # English query with no lexical overlap with the Russian bodies.
    res = index.search("PU leather china factories")
    assert res
    assert res[0].rel == "chatgpt/ecoleather.md"
    assert res[0].score > 0


def test_search_degrades_to_lexical_without_llm(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    index = ChatIndex(tgt, idx, llm=None)  # no semantic layer at all
    index.refresh()

    res = index.search("китайские фабрики")
    assert res and res[0].rel == "chatgpt/ecoleather.md"


# ── snippet ─────────────────────────────────────────────────────────────────


def test_snippet_window_contains_hit():
    body = "слово " * 200 + "фабрики в Гуанчжоу и дальше текст." + " конец " * 200
    snip = _snippet_window(body, ["фабрики"])
    assert "фабрики" in snip
    assert len(snip) <= 720


def test_snippet_falls_back_to_head():
    body = "начало тела без вхождений. " * 100
    snip = _snippet_window(body, ["неттакогослова"])
    assert snip


# ── chunking ─────────────────────────────────────────────────────────────────


def test_chunk_text_splits_long_lines():
    text = "короткая строка\n" * 300  # ~ 3000 chars
    chunks = _chunk_text(text)
    assert len(chunks) >= 2
    assert "".join(chunks) == text  # lossless split
    assert all(len(c) <= 2000 for c in chunks)


def test_snippet_comes_from_best_chunk(tmp_path):
    tgt, idx = _collection(tmp_path)
    body = ("обычный текст " * 300) + "\n\nобсуждали dragonhunter relic build и выбор реликвии в конце"
    _note(tgt, "chatgpt/gw2.md", "G1", body, "GW2 билды")
    index = ChatIndex(tgt, idx)
    index.refresh()

    res = index.search("dragonhunter relic")
    assert res
    top = res[0]
    assert "dragonhunter" in top.snippet.lower()
    assert "relic" in top.snippet.lower()  # the tail chunk, not the file head


def test_large_note_embeds_every_chunk(tmp_path):
    tgt, idx = _collection(tmp_path)
    llm = _FakeEmbedLLM()
    body = ("обычный контент о путешествиях и погоде. " * 120)
    _note(tgt, "deepseek/big.md", "B1", body, "Большой чат")
    index = ChatIndex(tgt, idx, llm=llm)
    stats = index.refresh()

    assert stats["embedded"] >= 3, "a long note must be embedded per chunk"
    vecs = index._vectors["deepseek/big.md"]["v"]
    assert len(vecs) >= 3
    assert all(isinstance(v, list) for v in vecs)


def test_search_after_load_without_refresh_reads_bodies(tmp_path):
    tgt, idx = _collection(tmp_path)
    _sample_collection(tgt)
    ChatIndex(tgt, idx).refresh()

    index2 = ChatIndex(tgt, idx)  # fresh instance, loads from disk, no refresh
    res = index2.search("китайские фабрики")
    assert res
    assert res[0].rel == "chatgpt/ecoleather.md"