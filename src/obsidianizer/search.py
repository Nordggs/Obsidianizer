"""Search layer over the processed collection (deterministic + semantic).

The AI chat is NOT a search engine — this module owns retrieval. A persistent
index (lexical tokens + optional embeddings) is refreshed incrementally by
``source_hash`` (only changed notes are re-read / re-embedded), and returns
top-K candidate chat cards with snippets. The LLM only acts as an analyst over
these candidates.

Index layout (under ``enriched/.obsidianizer/``):
- ``index.json``       — note metadata (title/tags/summary + source_hash)
- ``embeddings.json``  — ``rel -> {hash, vec}`` for the semantic layer

Neither file ever lives inside ``processed`` (the source of truth); both are
derived, rebuildable views.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .postprocess import split_file
from .topics import collect_chat_cards

logger = logging.getLogger("obsidianizer.search")

INDEX_VERSION = 2
INDEX_FILE = "index.json"
EMBEDDINGS_FILE = "embeddings.json"

MIN_TOKEN = 2  # ignore tokens shorter than this
_CHUNK_CHARS = 1800  # note body is split into chunks; each chunk is embedded
_MAX_EMBED_CHUNKS = 8  # per note: evenly sampled chunks keep embedding cost sane
_EMBED_BATCH = 20
_SNIPPET_CHARS = 700

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)  # unicode words only

# Question/service words that must not shape relevance.
_STOP = {
    "каких", "какие", "какой", "какая", "какое", "каком", "который", "которые",
    "которых", "которой", "где", "когда", "чем", "чём", "что", "чтобы", "как",
    "обсуждал", "обсуждали", "обсуждалось", "обсуждать", "обсуждается",
    "искать", "найди", "найдите", "найти", "покажи", "покажите", "написать",
    "упоминал", "упоминалось", "говорил", "говорили", "писать", "скажи",
    "мне", "меня", "мой", "мои", "моих", "моём", "моем", "наш", "наши", "наших",
    "этот", "это", "эта", "эти", "этих", "этом", "есть", "был", "была", "были",
    "будет", "быть", "для", "при", "без", "после", "все", "всё", "всей", "весь",
    "или", "либо", "также", "тот", "тут", "здесь", "там", "очень", "совсем",
    "через", "помоги", "помогите", "помощь", "можно", "нельзя", "сделать",
    "выбрали", "выбрать", "выбирали", "выбирать", "выбирается", "итоге", "итог",
    "решили", "решил", "решать",
    "мы", "мне", "меня", "нас", "наши", "наше", "мой", "моя", "могу", "надо",
    "вы", "ты", "тебе", "вас", "ваш", "ваша", "это", "этом", "этой", "этих",
    "этот", "эта", "такой", "такие", "какой", "какие", "какого", "какая",
    "именно", "самый", "самое", "самые", "сами", "ли", "бы", "же", "вот",
    "чат", "чате", "чаты", "переписки", "разговор", "разговоре",
}

# Lexical weights: title ≫ tags/summary > body substring.
W_TITLE = 5
W_TAGS = 3
W_SUMMARY = 3
W_BODY = 1

# Combined ranking blend (only when embeddings are available).
W_LEX = 0.55
W_COS = 0.45


@dataclass
class SearchCandidate:
    """One search hit ready for the UI and the analyst prompt."""

    rel: str
    title: str
    service: str
    date: str
    score: float
    snippet: str
    fragments: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    partial: bool = False  # True when only some query tokens matched
    full: bool = False  # True when the whole note text is attached to the LLM


Progress = Callable[[int, int, str], None]


def tokenize(text: str) -> list[str]:
    """Unicode words, lowercased, stop-word / length filtered."""

    return [
        t
        for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= MIN_TOKEN and t not in _STOP
    ]


def _variants(token: str) -> list[str]:
    """Morphology stems of a token, without any dictionary.

    A plain substring match misses inflections ("фабрики" vs "фабриками",
    "китайские" vs "китайских"). We therefore also probe the token truncated
    by 1–2 trailing chars (never below length 3) — a cheap, dictionary-free
    stemmer that covers the common Russian/English endings.
    """

    out = [token]
    for cut in (1, 2):
        stem = token[: len(token) - cut]
        if len(stem) >= 3:
            out.append(stem)
    return out


def _text_has(text: str | None, token: str) -> bool:
    """True when the token or any of its stems occurs in ``text``."""

    if not text:
        return False
    low = text.lower()
    return any(v in low for v in _variants(token))


def _chunk_text(text: str, chunk_chars: int = _CHUNK_CHARS) -> list[str]:
    """Split ``text`` into chunks of ~``chunk_chars`` at line/word boundaries.

    Regular lines accumulate into a chunk until the limit; a single line longer
    than the limit is additionally split by words so that no chunk blows past
    the embedding context.
    """

    lines = (text or "").splitlines(keepends=True)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("".join(cur))
            cur = []
            cur_len = 0

    for ln in lines:
        while len(ln) > chunk_chars and chunk_chars > 0:
            cut = ln.rfind(" ", 0, chunk_chars)
            if cut <= 0:
                cut = chunk_chars
            flush()
            chunks.append(ln[:cut])
            ln = ln[cut:]
        if cur and cur_len + len(ln) > chunk_chars:
            flush()
        cur.append(ln)
        cur_len += len(ln)
    flush()
    return chunks or [""]


def _chunk_repr(note: dict, chunk: str) -> str:
    """Compact embedding representation of one chunk (title keeps its context)."""

    parts = [str(note.get("title") or note["rel"])]
    if note.get("summary"):
        parts.append(str(note["summary"]))
    parts.append(chunk or "")
    return "\n".join(parts)


def _pick_embed_chunks(chunks: list[str]) -> list[str]:
    """Evenly sample up to ``_MAX_EMBED_CHUNKS`` chunks across the whole body.

    A long dialog's decisive part is usually near the end (the final choice),
    so the sample spans head → tail instead of taking only the head.
    """

    n = len(chunks)
    if n <= _MAX_EMBED_CHUNKS:
        return chunks
    idxs = sorted({round(i * (n - 1) / (_MAX_EMBED_CHUNKS - 1)) for i in range(_MAX_EMBED_CHUNKS)})
    return [chunks[i] for i in idxs]


def _snippet_window(body: str, tokens: list[str]) -> str:
    """The densest ~700-char window around query-token hits in the body.

    Simple "first hit" windows often land on the exporter card (source/URL
    headers) or on unrelated lines. Collecting every hit and picking the window
    that covers the most hits yields a fragment where the topic is actually
    discussed.
    """

    text = (body or "").lower()
    hits: list[int] = []
    for tok in tokens:
        for v in _variants(tok):
            start = 0
            while True:
                pos = text.find(v, start)
                if pos < 0:
                    break
                hits.append(pos)
                start = pos + len(v)
    if not hits:
        return _clean_snippet((body or "")[: _SNIPPET_CHARS])
    hits.sort()
    best_start, best_score = 0, -1
    for h in hits:
        win_start = max(0, h - 150)
        win_end = h + 250
        score = sum(1 for p in hits if win_start <= p <= win_end)
        if score > best_score:
            best_score = score
            best_start = win_start
    return _clean_snippet((body or "")[best_start : best_start + _SNIPPET_CHARS])


def _clean_snippet(raw: str) -> str:
    """Strip markdown / exporter noise from a snippet line block."""

    out = raw.replace("\r", "")
    out = re.sub(r"<!--[^>]*-->", "", out)  # hash / dedup comments
    out = re.sub(r"👤\s*Вы[^\n]*", "", out)  # exporter role markers
    out = re.sub(r"🤖\s*AI", "", out)
    out = re.sub(r">\s*\[!\w+\][^\n]*", "", out)  # callout headers
    out = re.sub(r"#{1,6}\s*", "", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", out)  # [t](url) -> t
    out = out.replace("**", "").replace("##", "").replace("\n\n", "\n")
    return out.strip()


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity over equal-length float vectors."""

    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import numpy as np

        va = np.asarray(a, dtype=np.float64)
        vb = np.asarray(b, dtype=np.float64)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:  # noqa: BLE001 - numpy is best-effort
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


class ChatIndex:
    """Persistent, incrementally-refreshed search index over ``target``.

    ``index_root`` is the cache directory (typically ``enriched/.obsidianizer``);
    ``llm`` is optional — without it (or when unreachable) the index falls back
    to the deterministic lexical layer only.
    """

    def __init__(
        self,
        target: Path,
        index_root: Path,
        llm=None,
        embed_model: str = "nomic-embed-text:latest",
        top_k: int = 30,
    ) -> None:
        self.target = target.resolve()
        self.index_root = index_root
        self.llm = llm
        self.embed_model = embed_model
        self.top_k = max(1, int(top_k))
        self._lock = threading.Lock()
        self._notes: list[dict] = []          # index.json entries
        self._bodies: dict[str, str] = {}     # rel -> body (cached)
        self._chunks: dict[str, list[str]] = {}  # rel -> chunked body (cached)
        self._vectors: dict[str, dict] = {}   # rel -> {"hash": str, "v": [[...], ...]}
        self._ready = False
        self._index_path = self.index_root / INDEX_FILE
        self._embeddings_path = self.index_root / EMBEDDINGS_FILE

    # ── persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load the on-disk index into memory (best-effort)."""

        try:
            if self._index_path.is_file():
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION and data.get("target") == str(self.target):
                    self._notes = data.get("notes", [])
        except (OSError, ValueError):
            logger.warning("Не удалось прочитать индекс %s — будет перестроен", self._index_path)
            self._notes = []
        try:
            if self._embeddings_path.is_file():
                data = json.loads(self._embeddings_path.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION and data.get("target") == str(self.target):
                    self._vectors = data.get("vectors", {}) or {}
        except (OSError, ValueError):
            logger.warning("Не удалось прочитать эмбеддинги %s — будут перестроены", self._embeddings_path)
            self._vectors = {}

    def _save_index(self) -> None:
        try:
            self.index_root.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {"version": INDEX_VERSION, "target": str(self.target), "notes": self._notes},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._index_path)
        except OSError as exc:
            logger.warning("Не удалось сохранить поисковый индекс: %s", exc)

    def _save_embeddings(self) -> None:
        try:
            self.index_root.mkdir(parents=True, exist_ok=True)
            tmp = self._embeddings_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "version": INDEX_VERSION,
                        "target": str(self.target),
                        "model": self.embed_model,
                        "vectors": self._vectors,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._embeddings_path)
        except OSError as exc:
            logger.warning("Не удалось сохранить эмбеддинги: %s", exc)

    # ── index refresh ──────────────────────────────────────────────────────

    def refresh(self, progress: Progress | None = None) -> dict:
        """Scan ``target``, re-read/embed only changed notes, persist.

        Returns ``{"added": n, "updated": n, "removed": n, "embedded": n}``.
        """

        with self._lock:
            if not self._ready:
                self._load()
                self._ready = True
            return self._refresh_locked(progress)

    def _refresh_locked(self, progress: Progress | None = None) -> dict:
        if not self.target.is_dir():
            self._notes = []
            self._bodies = {}
            self._chunks = {}
            self._vectors = {}
            return {"added": 0, "updated": 0, "removed": 0, "embedded": 0}

        cards = collect_chat_cards(self.target)
        new_notes: list[dict] = []
        new_vectors: dict[str, dict] = {}
        bodies: dict[str, str] = {}
        chunks: dict[str, list[str]] = {}
        to_embed: list[tuple[dict, str]] = []  # flat (note, chunk_repr)
        added = updated = 0

        for i, card in enumerate(cards):
            rel = card["rel"]
            meta, body = self._read_note(rel)
            if meta is None:
                continue
            h = str(meta.get("source_hash") or "")
            body_text = body or ""
            bodies[rel] = body_text
            chunks[rel] = _chunk_text(body_text)
            old = self._find_note(rel)
            changed = old is None or old.get("source_hash") != h
            note = {
                "rel": rel,
                "source_hash": h,
                "title": card["title"],
                "service": card["service"],
                "date": card["date"],
                "summary": str(meta.get("summary") or "")[:200],
                "tags": [str(t) for t in (meta.get("tags") or []) if str(t)],
            }
            new_notes.append(note)
            if old is None:
                added += 1
            elif changed:
                updated += 1

            vec = self._vectors.get(rel)
            if vec and vec.get("hash") == h:
                new_vectors[rel] = vec  # unchanged — reuse
            elif self.llm is not None and self.embed_model:
                for chunk in _pick_embed_chunks(chunks[rel]):
                    if chunk.strip():
                        to_embed.append((note, _chunk_repr(note, chunk)))

            if progress and (i + 1) % 20 == 0:
                progress(i + 1, len(cards), "Индексация…")

        removed = len({n["rel"] for n in self._notes} - {n["rel"] for n in new_notes})
        embedded = 0
        if to_embed:
            embedded = self._embed_many(to_embed, new_vectors, progress)

        self._notes = new_notes
        self._bodies = bodies
        self._chunks = chunks
        self._vectors = new_vectors
        self._save_index()
        if to_embed:
            self._save_embeddings()
        if progress and cards:
            progress(len(cards), len(cards), "Индекс готов")
        logger.info(
            "Поисковый индекс: %d чатов (+%d, обновлено %d, удалено %d, эмбеддингов %d)",
            len(new_notes), added, updated, removed, embedded,
        )
        return {"added": added, "updated": updated, "removed": removed, "embedded": embedded}

    def _embed_many(self, items: list[tuple[dict, str]], out: dict, progress: Progress | None) -> int:
        """Embed note chunks in batches; failures degrade silently.

        ``items`` is a flat list of ``(note, chunk_repr)``; the vectors for one
        note accumulate into ``out[rel]["v"]`` in chunk order.
        """

        if self.llm is None:
            return 0
        done = 0
        for start in range(0, len(items), _EMBED_BATCH):
            chunk = items[start : start + _EMBED_BATCH]
            try:
                embs = self.llm.embed([repr_ for _, repr_ in chunk])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Эмбеддинги недоступны (%s) — семантика отключена", exc)
                break
            if not embs:
                logger.warning("Ollama вернул пустые эмбеддинги — семантика отключена")
                break
            for (note, _), vec in zip(chunk, embs):
                rel = note["rel"]
                entry = out.setdefault(rel, {"hash": note["source_hash"], "v": []})
                entry["v"].append(vec)
                done += 1
        if progress:
            progress(done, len(items), "Эмбеддинги…")
        return done

    def _find_note(self, rel: str) -> dict | None:
        for n in self._notes:
            if n["rel"] == rel:
                return n
        return None

    def _read_note(self, rel: str) -> tuple[dict | None, str | None]:
        path = self.target.joinpath(*rel.split("/"))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, None
        parsed = split_file(text)
        if parsed is None:
            return None, None
        meta, body = parsed
        return meta, body or ""

    # ── search ─────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int | None = None) -> list[SearchCandidate]:
        """Return the top-K candidates for ``query`` (lexical + semantic).

        Deterministic lexical scoring always runs; the semantic layer is added
        only when embeddings exist for the collection. Candidates with a zero
        score are dropped. ``partial=True`` marks results where only a subset
        of the query tokens matched.
        """

        tokens = tokenize(query)
        if not tokens:
            return []
        k = top_k or self.top_k
        with self._lock:
            if not self._ready:
                self._load()
                self._ready = True
            if not self._notes:
                self._refresh_locked()

            # A bare disk load has no bodies cached — read them up front so
            # lexical scoring (and df) works without a refresh.
            if not self._bodies:
                for note in self._notes:
                    rel = note["rel"]
                    meta, body = self._read_note(rel)
                    if body:
                        self._bodies[rel] = body
                        self._chunks[rel] = _chunk_text(body)

            df = self._token_df(tokens)
            n_notes = len(self._notes)
            boost: dict[str, float] = {}
            for tok in tokens:
                d = df.get(tok, 0)
                if d <= max(3, round(n_notes * 0.04)):
                    boost[tok] = 4.0  # rare, distinctive token — decisive
                elif d > max(20, round(n_notes * 0.35)):
                    boost[tok] = 0.6  # near-stopword — barely informative
                else:
                    boost[tok] = 1.0

            lex: list[SearchCandidate] = []
            max_lex = 0
            query_vec = None
            if self._vectors and self.llm is not None:
                try:
                    q = self.llm.embed([query])
                    query_vec = q[0] if q else None
                except Exception:  # noqa: BLE001
                    query_vec = None

            for note in self._notes:
                rel = note["rel"]
                chunks = self._chunks.get(rel)
                if not chunks:
                    chunks = _chunk_text(self._bodies.get(rel, ""))
                    self._chunks[rel] = chunks
                if not self._bodies.get(rel) or not chunks or not chunks[0]:
                    continue
                score, matched, best_idx, hit_chunks = self._lex_score(
                    note, chunks, tokens, boost
                )
                if score <= 0 and query_vec is None:
                    continue
                fragments = [_snippet_window(chunks[i], tokens)[:400] for i in hit_chunks[:5]]
                cand = SearchCandidate(
                    rel=rel,
                    title=note["title"],
                    service=note["service"],
                    date=note["date"],
                    score=float(score),
                    matched=matched,
                    snippet=fragments[0] if fragments else _snippet_window(chunks[best_idx], tokens),
                    fragments=fragments,
                    partial=len(matched) < len(tokens),
                )
                if score > 0:
                    max_lex = max(max_lex, score)
                lex.append(cand)

            if not lex:
                return []

            results: list[SearchCandidate] = []
            for cand in lex:
                final = cand.score
                if query_vec is not None:
                    vec = self._vectors.get(cand.rel)
                    if vec:
                        vecs = vec.get("v") or []
                        cos = max((_cosine(query_vec, v) for v in vecs), default=0.0)
                        lex_norm = (cand.score / max_lex) if max_lex else 0.0
                        final = W_LEX * lex_norm + W_COS * cos
                cand.score = round(final, 4)
                results.append(cand)

            results.sort(key=lambda c: c.score, reverse=True)
            return results[:k]

    def _lex_score(
        self,
        note: dict,
        chunks: list[str],
        tokens: list[str],
        token_boost: dict[str, float] | None = None,
    ) -> tuple[int, list[str], int, list[int]]:
        """Deterministic score: title ≫ tags/summary > body chunks.

        Returns ``(score, matched, best_chunk_index, hit_chunks)``. ``best_idx``
        is the chunk covering the most query tokens; ``hit_chunks`` is every
        chunk with at least one token hit, sorted by coverage — the candidate's
        fragments. ``token_boost`` scales body hits by token rarity (a rare,
        distinctive token like ``dragonhunter`` is far more decisive than a
        frequent one).
        """

        title = str(note.get("title") or note["rel"])
        tags = " ".join(note.get("tags") or [])
        summary = str(note.get("summary") or "")
        score = 0
        matched: list[str] = []
        hits = [set() for _ in chunks]
        for tok in tokens:
            if _text_has(title, tok):
                score += W_TITLE
                matched.append(tok)
            elif _text_has(tags, tok) or _text_has(summary, tok):
                score += W_TAGS
                matched.append(tok)
            else:
                found = False
                for i, ch in enumerate(chunks):
                    if _text_has(ch, tok):
                        hits[i].add(tok)
                        found = True
                if found:
                    boost = (token_boost or {}).get(tok, 1.0)
                    score += int(round(W_BODY * boost))
                    matched.append(tok)
        best_idx = 0
        hit_chunks: list[int] = []
        if chunks:
            prio = [sum((token_boost or {}).get(t, 1.0) for t in s) for s in hits]
            hit_chunks = [i for i in range(len(chunks)) if prio[i] > 0]
            # Rare/decisive tokens weigh more; ties prefer a later chunk (the
            # final decision usually sits near the end of a dialog).
            hit_chunks.sort(key=lambda i: (prio[i], i), reverse=True)
            best_idx = hit_chunks[0] if hit_chunks else 0
        return score, matched, best_idx, hit_chunks

    def _token_df(self, tokens: list[str]) -> dict[str, int]:
        """Document frequency: in how many notes each token occurs (by body)."""

        df = {t: 0 for t in tokens}
        for note in self._notes:
            body = self._bodies.get(note["rel"], "")
            if not body:
                continue
            for tok in tokens:
                if df[tok] < len(self._notes) and _text_has(body, tok):
                    df[tok] += 1
        return df

    def invalidate(self) -> None:
        """Drop in-memory state; the next call re-reads from disk."""

        with self._lock:
            self._ready = False
            self._notes = []
            self._bodies = {}
            self._chunks = {}
            self._vectors = {}