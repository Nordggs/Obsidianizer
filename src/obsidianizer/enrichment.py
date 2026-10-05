"""Enrichment Entity (TICKET-008A) — pure DTO layer, no behaviour change.

ARCH-DECISION-008A-1: ``EnrichmentEntity`` is a *logical* object, not a
``.md`` file. The note body is representation; the canonical entity fields
live in frontmatter (owned metadata) inside the owned catalogs
(processed/enriched) — that reading of AC#1 («own storage space») creates
no dedicated store. ``.md`` bodies are never read as entities.

``source_id`` is the obligatory Source link and the namespace-scoped natural
key — there is no separate ``enrichment_id``. ``build_entity()`` /
``update_entity()`` are independent constructors / DTO operations without
persistence or lifecycle (full lifecycle create/update/continue/retire is a
TICKET-008C task). Ownership keys (manifest / ``ai_hash`` / topic) keep
working beside the new links; switching them over is 008C.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .emit import atomic_write
from .nav_index import frontmatter_of
from .source_index import build_source_index

logger = logging.getLogger("obsidianizer.enrichment")

NO_SOURCE_ITEM = "no_source_item"
NO_NAMESPACE = "no_namespace"
SOURCE_UNAVAILABLE = "source_unavailable"

# Owned-catalog service areas that never receive a source_id of their own:
# topic notes are aggregates (links via relations/chats), the rest is
# representation or search cache.
_SKIPPED_TOP_DIRS = ("topics", ".obsidianizer")


@dataclass(frozen=True)
class EnrichmentEntity:
    """Logical enrichment object linked to one Source object."""

    source_id: str  # obligatory, namespace-scoped natural key (no auto-id)
    data: dict[str, Any] = field(default_factory=dict)  # description/summary/topics/relations/comments/AI analysis
    provenance: dict[str, Any] = field(default_factory=dict)  # producer/stage/model
    version: dict[str, Any] = field(default_factory=dict)  # content_hash/ai_hash/process_version
    ai_metadata: dict[str, Any] = field(default_factory=dict)
    relations: list[str] = field(default_factory=list)  # other source_ids


def build_entity(
    source_id: str,
    *,
    data: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    version: dict[str, Any] | None = None,
    ai_metadata: dict[str, Any] | None = None,
    relations: list[str] | None = None,
) -> EnrichmentEntity:
    """Independent constructor: link a new entity to a Source object.

    ``source_id`` is required and never generated — an empty id raises
    instead of minting identity (identity belongs to the SourceIndex).
    """

    if not source_id or not str(source_id).strip():
        raise ValueError("EnrichmentEntity requires an explicit source_id")
    return EnrichmentEntity(
        source_id=str(source_id),
        data=dict(data or {}),
        provenance=dict(provenance or {}),
        version=dict(version or {}),
        ai_metadata=dict(ai_metadata or {}),
        relations=list(relations or []),
    )


def update_entity(
    entity: EnrichmentEntity,
    *,
    data: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    version: dict[str, Any] | None = None,
    ai_metadata: dict[str, Any] | None = None,
    relations: list[str] | None = None,
) -> EnrichmentEntity:
    """Independent DTO operation: copy with merged sections (pure, no I/O).

    Mapping sections are merged key-wise; ``relations`` are unioned
    preserving order; ``source_id`` never changes.
    """

    merged_relations = list(entity.relations)
    for rel in relations or []:
        if rel not in merged_relations:
            merged_relations.append(rel)
    return EnrichmentEntity(
        source_id=entity.source_id,
        data={**entity.data, **(data or {})},
        provenance={**entity.provenance, **(provenance or {})},
        version={**entity.version, **(version or {})},
        ai_metadata={**entity.ai_metadata, **(ai_metadata or {})},
        relations=merged_relations,
    )


def is_linked(meta: dict[str, Any]) -> bool:
    """Link predicate: does this frontmatter carry a ``source_id`` link?"""

    return bool((meta or {}).get("source_id"))


# ── lifecycle / continuity (TICKET-008C, pure operations, no I/O) ──────────

STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
STATUS_SUPERSEDED = "superseded"
_HIDDEN_STATUSES = (STATUS_RETIRED, STATUS_SUPERSEDED)


def is_visible(rel: str, meta: dict[str, Any], exclusion=None) -> bool:
    """Visibility predicate: not excluded (006) AND not retired/superseded."""

    if exclusion is not None and exclusion.excluded(rel or ""):
        return False
    return str((meta or {}).get("status") or "") not in _HIDDEN_STATUSES


def mark_retired(text: str) -> str:
    """Set ``status: retired`` in the frontmatter block (pure text edit)."""

    return _set_status_lines(text, STATUS_RETIRED, None)


def mark_superseded(text: str, new_source_id: str) -> str:
    """Set ``status: superseded`` + ``superseded_by: <new id>`` (pure)."""

    return _set_status_lines(text, STATUS_SUPERSEDED, new_source_id)


def mark_active(text: str) -> str:
    """Drop ``status``/``superseded_by`` lines: the note reads active again.

    Surgical like :func:`_surgical_insert` — every other byte is preserved,
    so ``ai_hash`` freshness (``_is_fresh``) survives the reset.
    """

    if not text.startswith("---"):
        return text
    end = text.find("\n---", 4)
    if end < 0:
        return text
    block = text[4:end]
    lines = [ln for ln in block.split("\n") if not re.match(r"^(status|superseded_by):", ln)]
    return text[:4] + "\n".join(lines) + text[end:]


def _set_status_lines(text: str, status: str, successor: str | None) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 4)
    if end < 0:
        return text
    block = text[4:end]
    lines = [ln for ln in block.split("\n") if not re.match(r"^(status|superseded_by):", ln)]
    lines.append(f"status: {status}")
    if successor:
        lines.append(f"superseded_by: {successor}")
    return text[:4] + "\n".join(lines) + text[end:]


def match_continuity(dead: dict[str, Any], live_items: list[dict[str, Any]]) -> str | None:
    """Match a dead object against live signals (pure, no I/O).

    Signal dicts carry ``source_id / kind / file_type / content_hash /
    size / mtime_ns / name / parent_id``. Ranking: exact ``content_hash``
    wins over weak ``size + mtime_ns + name``; folders match only folders
    (kind barrier) by ``name + parent_id``. Thresholds are strict equality;
    ambiguity or no signal refuses (``None`` = mint a new Enrichment).
    Self-matches (same ``source_id``) never count.
    """

    dead_id = dead.get("source_id")
    dead_kind = dead.get("kind")
    candidates = [
        live
        for live in live_items
        if live.get("kind") == dead_kind and live.get("source_id") != dead_id
    ]
    if not candidates:
        return None
    if dead_kind == "folder":
        matched = [
            c
            for c in candidates
            if c.get("name") and c.get("name") == dead.get("name")
            and c.get("parent_id") == dead.get("parent_id")
        ]
        if len(matched) == 1:
            return matched[0].get("source_id")
        return None
    strong = [
        c
        for c in candidates
        if c.get("content_hash") and c.get("content_hash") == dead.get("content_hash")
    ]
    if len(strong) == 1:
        return strong[0].get("source_id")
    if len(strong) > 1:
        return None  # duplicate content: refuse rather than guess
    weak = [
        c
        for c in candidates
        if c.get("size") is not None and c.get("size") == dead.get("size")
        and c.get("mtime_ns") is not None and c.get("mtime_ns") == dead.get("mtime_ns")
        and c.get("name") and c.get("name") == dead.get("name")
    ]
    if len(weak) == 1:
        return weak[0].get("source_id")
    return None


def continue_enrichment(entity: EnrichmentEntity, new_source_id: str) -> EnrichmentEntity:
    """Carry an entity over to a successor ``source_id`` (pure).

    Data, AI metadata and relations survive; provenance records the
    transfer. The old ``source_id`` is never reused.
    """

    if not new_source_id or not str(new_source_id).strip():
        raise ValueError("continue_enrichment requires an explicit new_source_id")
    if str(new_source_id) == entity.source_id:
        raise ValueError("continue_enrichment requires a different source_id")
    return replace(
        entity,
        source_id=str(new_source_id),
        provenance={
            **entity.provenance,
            "continued_from": entity.source_id,
        },
    )


def retire(entity: EnrichmentEntity, successor: str | None = None) -> EnrichmentEntity:
    """Record lifecycle retirement on the entity (pure, no I/O).

    Note-level marking (``status: retired|superseded`` in frontmatter) is
    done by :func:`mark_retired` / :func:`mark_superseded`; this only fixes
    the logical transition. Deletion itself happens exclusively under prune.
    """

    provenance = {**entity.provenance, "lifecycle": STATUS_RETIRED}
    if successor:
        provenance["superseded_by"] = str(successor)
    return replace(entity, provenance=provenance)


def _surgical_insert(text: str, source_id: str) -> str | None:
    """Insert ``source_id: <id>`` into the frontmatter block, nothing else.

    No YAML round-trip: every other byte of the file (key order, comments,
    body) is preserved. Returns the new text, or ``None`` when the block
    boundaries cannot be located safely.
    """

    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    block = text[4:end]
    if re.search(r"(?m)^source_id:.*$", block):
        # A falsy pre-existing key (truthy ones are skipped via is_linked):
        # repair the line in place instead of duplicating the key.
        block, _ = re.subn(
            r"(?m)^source_id:.*$", f"source_id: {source_id}", block, count=1
        )
    else:
        block = block + f"\nsource_id: {source_id}"
    return text[:4] + block + text[end:]


def backfill_source_ids(
    owned_root: Path,
    *,
    source_root: Path,
    namespace: str,
    exclusion=None,
) -> dict[str, Any]:
    """Additively backfill ``source_id`` into owned notes missing the link.

    Candidates are strictly owned notes carrying an ownership marker
    (``source_hash`` / ``ai_hash``) under *owned_root*; ``topics/``,
    ``.obsidianizer/``, ``_index.md`` and any ``type: topic`` note are
    excluded (aggregates own no ``source_id`` of their own). Each candidate
    resolves ``rel → SourceIndex → id``; misses are reported, never guessed.

    Idempotent via :func:`is_linked` (a second run changes nothing).
    Identity is never minted here: an empty *namespace* (or an unavailable
    Source) marks every candidate ``unresolved``. Source is never touched.

    Returns ``{"migrated": [rel, ...], "unresolved": [{"rel", "reason"}]}``
    with reasons ``no_source_item`` / ``no_namespace`` /
    ``source_unavailable``.
    """

    migrated: list[str] = []
    unresolved: list[dict[str, str]] = []
    if not owned_root.is_dir():
        return {"migrated": migrated, "unresolved": unresolved}

    candidates: list[tuple[Path, str, dict[str, Any]]] = []
    for path in sorted(owned_root.rglob("*.md"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(owned_root).as_posix()
        except ValueError:
            continue
        if path.name == "_index.md":
            continue
        if rel.split("/", 1)[0] in _SKIPPED_TOP_DIRS:
            continue
        try:
            meta = frontmatter_of(path)
        except OSError:
            continue
        if meta is None:
            continue  # unparseable: marker unconfirmed, not a candidate
        if meta.get("type") == "topic":
            continue  # aggregate enrichment, links via relations/chats
        if not meta.get("source_hash") and not meta.get("ai_hash"):
            continue  # foreign note: no ownership marker
        if is_linked(meta):
            continue  # already linked: idempotent skip
        candidates.append((path, rel, meta))

    if not str(namespace or ""):
        return {
            "migrated": migrated,
            "unresolved": [{"rel": rel, "reason": NO_NAMESPACE} for _, rel, _ in candidates],
        }
    if not source_root.is_dir():
        return {
            "migrated": migrated,
            "unresolved": [
                {"rel": rel, "reason": SOURCE_UNAVAILABLE} for _, rel, _ in candidates
            ],
        }

    index = build_source_index(source_root, str(namespace), exclusion)
    for path, rel, _meta in candidates:
        item = index.get_by_path(rel)
        if item is None:
            unresolved.append({"rel": rel, "reason": NO_SOURCE_ITEM})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Strict read: must never rewrite foreign bytes with replacement
            # characters (that would be data loss). frontmatter_of() reads
            # with errors="replace", so such a note passes candidate
            # selection — report it instead of crashing the whole run.
            unresolved.append({"rel": rel, "reason": NO_SOURCE_ITEM})
            continue
        new_text = _surgical_insert(text, item.source_id)
        if new_text is None or new_text == text:
            unresolved.append({"rel": rel, "reason": NO_SOURCE_ITEM})
            continue
        try:
            atomic_write(path, new_text)
        except OSError:
            logger.error("Backfill failed to write: %s", rel)
            unresolved.append({"rel": rel, "reason": NO_SOURCE_ITEM})
            continue
        migrated.append(rel)
    return {"migrated": migrated, "unresolved": unresolved}
