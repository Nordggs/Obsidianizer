"""AI post-processing stage — reads the plain notes and writes an *enriched*
vault, physically separated from the import result:

    raw -> import (fast, no LLM) -> processed/ -> AI enrichment -> enriched/

The stage never modifies ``processed``: ``target_root`` (enriched) is the only
write destination. Obsidian points at ``enriched`` — the final knowledge base.

Incrementality is driven by two markers:
- ``source_hash`` (in the processed note) is the content identity of the raw file;
- ``ai_hash`` (in the enriched note) remembers which source_hash was analyzed.

A note is skipped when the enriched copy exists **and**
``enriched.ai_hash == processed.source_hash``. Import recreating a note clears
``ai_hash`` in processed, but enriched keeps its old marker until the AI pass
repairs it, so a plain re-import can never destroy AI results.

Order of operations per file:
    read processed -> split_file -> (meta, body)
    -> enriched copy fresh (ai_hash == source_hash)? skip
    -> analyze body -> write enriched (atomic) -> copy local media

After the loop: prune orphans (optional), rebuild ``enriched/_index.md``.
A failed call never writes the marker, so the next run simply retries.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from .emit import atomic_write
from .enrich import build_card, build_frontmatter, compose
from .events import Event, EventType
from .i18n import tr
from .nav_index import build_index_from_dir, frontmatter_of
from .llm import LLMClient

logger = logging.getLogger("obsidianizer.postprocess")

EventCallback = Callable[[Event], None] | None
CancelCheck = Callable[[], bool] | None

_RE_MEDIA = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


@dataclass
class EnrichReport:
    processed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    cancelled: bool = False
    critical_error: str = ""  # "" = no fatal run-level error
    # TICKET-008C reconciliation (always-on pass after the enrich loop).
    reconciled: int = 0  # marked + reset + transfers
    retired: list[str] = field(default_factory=list)
    transfers: list[str] = field(default_factory=list)  # "old_rel -> new_id"
    resumed: list[str] = field(default_factory=list)  # retired→active resets
    reconcile_skipped: bool = False  # True when no SourceIndex was provided


@dataclass
class ReconcileReport:
    """Result of one always-on reconciliation pass over enriched notes."""

    marked: list[str] = field(default_factory=list)  # newly retired rels
    reset: list[str] = field(default_factory=list)  # retired→active rels
    transfers: list[str] = field(default_factory=list)  # "old_rel -> new_id"
    skipped: bool = False  # True when no SourceIndex was provided


def split_file(text: str) -> tuple[dict, str] | None:
    """Resolve a produced note into (frontmatter dict, body text).

    Returns ``None`` for anything that is not an Obsidianizer-produced file
    (missing/empty frontmatter). The body separator is the first standalone
    ``---`` line after the frontmatter block — the generated card never
    contains one, so this is unambiguous.
    """

    if not text.startswith("---\n"):
        return None
    fm_end = text.find("\n---", 4)
    if fm_end < 0:
        return None
    block = text[4:fm_end]
    try:
        meta = yaml.safe_load(block)
    except Exception:  # noqa: BLE001 - tolerate malformed notes
        return None
    if not isinstance(meta, dict):
        return None

    rest = text[fm_end + 4 :]
    sep = rest.find("\n---\n")
    if sep < 0:
        return None
    body = rest[sep + 4 :].strip("\n")
    return meta, body


def _finish_message(report: EnrichReport) -> str:
    counts = (
        f"{tr('ai.processed', n=report.processed)}, {tr('fin.skipped', n=report.skipped)}, "
        f"{tr('fin.errors', n=len(report.failed))}"
    )
    if report.pruned:
        counts += f", {tr('ai.pruned', n=len(report.pruned))}"
    if report.cancelled:
        return f"{tr('ai.cancelled')} ({counts})"
    if report.critical_error:
        return f"{tr('ai.critical', err=report.critical_error)} ({counts})"
    return counts


def _finish_data(report: EnrichReport) -> dict:
    """Machine-readable AI_FINISHED payload — no message parsing in the UI."""

    return {
        "mode": "ai",
        "cancelled": bool(report.cancelled),
        "critical": report.critical_error or "",
        "processed": report.processed,
        "skipped": report.skipped,
        "errors": len(report.failed),
        "pruned": len(report.pruned),
    }


def enrich(
    source_root: Path,
    target_root: Path,
    llm: LLMClient,
    *,
    prune: bool = False,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
    exclusion_filter=None,
    source_index=None,
) -> EnrichReport:
    """Enrich every note in ``source_root`` (processed) into ``target_root``
    (enriched). Never touches ``source_root``; never touches foreign files
    (no ``source_hash`` in processed frontmatter).

    ``prune`` removes enriched orphans — notes whose pair no longer exists in
    ``source_root`` — together with any media only they referenced. Marked by
    ``ai_hash`` ownership, so foreign vault files are safe.

    ``exclusion_filter`` (TICKET-006): excluded rels are skipped (not
    processed) but retained in ``current`` so ``prune`` never deletes their
    enriched copies just because an exclusion appeared.

    ``source_index`` (TICKET-008C): live ``SourceIndex`` built by the owner
    from settings. Feeds the always-on reconciliation pass (marks dead ids
    ``retired``/``superseded``, resets revived ones) and the id-based prune
    with Pair Guard. Without it the pass is skipped with a report flag and
    pruning falls back to the legacy rel-based behaviour.

    ``on_event`` mirrors the pipeline event contract; ``cancel_check`` is
    polled between notes and preserves already-written results (and skips the
    reconcile/prune/index pass on cancel).
    """

    report = EnrichReport()
    try:
        notes = sorted(
            (p for p in source_root.rglob("*.md") if p.name != "_index.md"),
            key=lambda p: p.as_posix(),
        )
        total = len(notes)
        emit = _make_emitter(on_event, total)
        emit(EventType.AI_SCAN_STARTED, path=str(source_root), message=str(total))

        current: set[str] = set()
        for index, note in enumerate(notes, start=1):
            if cancel_check is not None:
                try:
                    cancelled = bool(cancel_check())
                except Exception:  # noqa: BLE001 - broken checker must not kill the batch
                    cancelled = False
                if cancelled:
                    report.cancelled = True
                    break

            rel = note.relative_to(source_root).as_posix()
            if exclusion_filter is not None and exclusion_filter.excluded(rel):
                # TICKET-006: not processed, but retained so prune keeps
                # the enriched copy.
                current.add(rel)
                continue
            current.add(rel)
            emit(EventType.AI_FILE_STARTED, path=rel, index=index)

            try:
                text = note.read_text(encoding="utf-8", errors="replace")
                parsed = split_file(text)
                if parsed is None:
                    continue  # foreign file — untouched, not counted in skip totals
                meta, body = parsed

                source_hash = meta.get("source_hash")
                if not source_hash:
                    continue  # foreign note (no ownership marker)

                out_path = target_root / rel
                if _is_fresh(out_path, source_hash):
                    report.skipped += 1
                    emit(EventType.AI_FILE_SKIPPED, path=rel, index=index)
                    continue

                result = llm.analyze(body)
                if not result["summary"] and not result["tags"]:
                    report.failed.append(f"{rel}: {tr('ai.empty_reply')}")
                    emit(
                        EventType.AI_FILE_ERROR,
                        path=rel,
                        index=index,
                        message=tr("ai.empty_reply"),
                    )
                    continue

                enriched = dict(meta)
                enriched["summary"] = result["summary"]
                enriched["tags"] = result["tags"]
                enriched["topic"] = result["topic"]
                enriched["type"] = result["type"]
                enriched["ai_hash"] = source_hash
                # TICKET-008C groundwork: persist the processed note's
                # size/mtime for future non-md continuity matching (the
                # pipeline only handles .md today; harmless metadata).
                try:
                    _st = note.stat()
                    enriched["source_size"] = _st.st_size
                    enriched["source_mtime"] = _st.st_mtime_ns
                except OSError:
                    pass

                frontmatter = build_frontmatter(
                    enriched, result["summary"], result["tags"], source_hash
                )
                card = build_card(
                    enriched,
                    result["summary"],
                    result["tags"],
                    topic=result["topic"],
                    ai_type=result["type"],
                )
                atomic_write(out_path, compose(frontmatter, card, body))
                _copy_media(source_root, note, target_root, body)
                report.processed += 1
                emit(EventType.AI_FILE_DONE, path=rel, index=index)
            except Exception as exc:  # noqa: BLE001 - one bad note must not kill the batch
                report.failed.append(f"{rel}: {exc}")
                emit(
                    EventType.AI_FILE_ERROR,
                    path=rel,
                    index=index,
                    message=str(exc),
                )
                logger.error(tr("ai.file_error", path=rel, err=exc))

        if not report.cancelled:
            # TICKET-008C: always-on reconciliation after the enrich loop,
            # outside the prune branch, on every run.
            rec = reconcile_enriched(
                target_root, index=source_index, exclusion_filter=exclusion_filter
            )
            report.reconcile_skipped = rec.skipped
            report.retired = rec.marked
            report.transfers = rec.transfers
            report.resumed = rec.reset
            report.reconciled = len(rec.marked) + len(rec.reset) + len(rec.transfers)
            if prune:
                report.pruned = _prune_orphans(
                    source_root, target_root, current,
                    exclusion_filter=exclusion_filter,
                    source_index=source_index,
                )
            if report.processed > 0 or report.pruned or report.reconciled:
                # Summaries or lifecycle statuses may have changed: rebuild
                # the navigation index (reconcile can mark retired without
                # processing or pruning anything — the index must follow).
                index_md = build_index_from_dir(target_root, exclusion_filter)
                if index_md:
                    atomic_write(target_root / "_index.md", index_md)
    except Exception as exc:  # noqa: BLE001 - fatal stage failure must still emit AI_FINISHED
        report.critical_error = str(exc)
        logger.error(tr("ai.critical_log", err=exc))
    finally:
        emit = _make_emitter(on_event, 0)
        emit(
            EventType.AI_FINISHED,
            index=report.processed,
            message=_finish_message(report),
            data=_finish_data(report),
        )

    return report


def _is_fresh(out_path: Path, source_hash: str) -> bool:
    """True when the enriched copy exists and its ai_hash matches the current
    source_hash (the note is already analyzed, nothing to do)."""

    if not out_path.is_file():
        return False
    meta = frontmatter_of(out_path)
    return bool(meta) and meta.get("ai_hash") == source_hash


def _copy_media(source_root: Path, note: Path, target_root: Path, body: str) -> None:
    """Copy each locally-referenced media file of ``body`` into ``target_root``
    under the same relative layout, keeping Obsidian image links valid inside
    the enriched vault. Never follows http/data/# refs and never escapes roots."""

    note_dir = note.parent.relative_to(source_root).as_posix()
    src_root = _normalized(source_root)
    tgt_resolved = target_root.resolve()
    for m in _RE_MEDIA.finditer(body):
        ref = m.group(1)
        if ref.lower().startswith(("http://", "https://", "//", "data:", "#")):
            continue
        src = _resolve_media(source_root, note_dir, ref, src_root)
        if src is None:
            continue
        dest_rel = f"{note_dir}/{ref}" if note_dir else ref
        dest = target_root / dest_rel
        try:
            if _escapes(tgt_resolved, dest.resolve()):
                continue
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
        except OSError:
            continue


def _resolve_media(
    source_root: Path, note_dir: str, ref: str, source_root_norm: str
) -> Path | None:
    """Find a local media file referenced by a note, or None. Safe against
    traversal: never resolves outside the source root."""

    for directory in (f"{note_dir}/{ref}", ref):
        parts = directory.split("/")
        candidate = source_root.joinpath(*parts)
        try:
            candidate_norm = _normalized(candidate)
        except OSError:
            continue
        if not candidate_norm.startswith(source_root_norm + os.sep):
            continue
        if candidate.is_file():
            return candidate
    return None


def reconcile_enriched(
    target_root: Path, *, index=None, exclusion_filter=None
) -> ReconcileReport:
    """Always-on reconciliation of enriched notes against a live SourceIndex.

    Runs after the enrich loop, outside the prune branch, on every run.
    Status is a pure function of the live Index (live id → ``active``,
    dead without match → ``retired``), so no history means no flapping.
    Rename with a confirmed match transfers (old note ``superseded`` with
    the successor id); deletion itself happens exclusively under prune.
    Without an Index the pass is skipped with a report flag, never failing.
    """

    from .enrichment import (
        STATUS_RETIRED,
        STATUS_SUPERSEDED,
        mark_active,
        mark_retired,
        mark_superseded,
        match_continuity,
    )

    report = ReconcileReport()
    if index is None:
        report.skipped = True
        return report
    live_ids = {item.source_id for item in index.items.values()}
    live_files = [item for item in index.items.values() if item.kind == "file"]
    try:
        source_root = Path(str(index.root))
    except OSError:
        source_root = None
    hash_cache: dict[str, str | None] = {}

    def _live_hash(item) -> str | None:
        if item.rel_posix in hash_cache:
            return hash_cache[item.rel_posix]
        digest: str | None = None
        if source_root is not None and item.rel_posix.lower().endswith(".md"):
            try:
                digest = hashlib.sha1(
                    source_root.joinpath(*item.rel_posix.split("/")).read_bytes()
                ).hexdigest()
            except OSError:
                digest = None
        hash_cache[item.rel_posix] = digest
        return digest

    notes = sorted(
        (p for p in target_root.rglob("*.md") if p.name != "_index.md"),
        key=lambda p: p.as_posix(),
    )
    for path in notes:
        try:
            rel = path.relative_to(target_root).as_posix()
        except ValueError:
            continue
        if rel.split("/", 1)[0] == "topics":
            continue  # aggregates own no source_id (008B)
        if exclusion_filter is not None and exclusion_filter.excluded(rel):
            continue  # TICKET-006 retention: excluded derivatives untouched
        meta = frontmatter_of(path)
        if meta is None or not meta.get("ai_hash"):
            continue  # not ours
        if meta.get("type") == "topic":
            continue
        sid = meta.get("source_id")
        if not sid:
            continue  # pre-backfill residue: backfill's job, not reconcile's
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # never rewrite foreign bytes; leave for the operator
        status = str(meta.get("status") or "")
        if sid in live_ids:
            if status in (STATUS_RETIRED, STATUS_SUPERSEDED):
                new_text = mark_active(text)
                if new_text != text:
                    try:
                        atomic_write(path, new_text)
                    except OSError:
                        continue
                    report.reset.append(rel)
            continue
        dead = {
            "source_id": sid,
            "kind": "file",
            "file_type": None,
            "content_hash": meta.get("source_hash"),
            "size": None,
            "mtime_ns": None,
            "name": rel.rsplit("/", 1)[-1],
            "parent_id": None,
        }
        live_signals = [
            {
                "source_id": item.source_id,
                "kind": "file",
                "file_type": item.file_type,
                "content_hash": _live_hash(item),
                "size": item.size,
                "mtime_ns": item.mtime_ns,
                "name": item.rel_posix.rsplit("/", 1)[-1],
                "parent_id": item.parent_source_id,
            }
            for item in live_files
        ]
        successor = match_continuity(dead, live_signals)
        if successor is not None:
            new_text = mark_superseded(text, successor)
            if new_text != text:
                try:
                    atomic_write(path, new_text)
                except OSError:
                    continue
                report.transfers.append(f"{rel} -> {successor}")
        elif status not in (STATUS_RETIRED, STATUS_SUPERSEDED):
            new_text = mark_retired(text)
            if new_text != text:
                try:
                    atomic_write(path, new_text)
                except OSError:
                    continue
                report.marked.append(rel)
    return report


def _prune_orphans(
    source_root: Path, target_root: Path, current: set[str],
    exclusion_filter=None, source_index=None,
) -> list[str]:
    """Delete enriched notes that no longer have a pair in ``source_root``,
    plus media only they reference. Only files carrying the ``ai_hash``
    ownership marker are ever removed — foreign vault files are safe.

    TICKET-006: enriched copies whose rel matches the exclusion filter are
    retained even when absent from ``current``.

    TICKET-008C: with a live ``source_index``, notes prune by id with Pair
    Guard — a dead id is deleted only while its processed twin is also
    gone; otherwise the note is kept (reconcile marks it retired).

    TICKET-012 identity contract: a note WITHOUT a ``source_id`` — or any
    note when no ``source_index`` is provided — is NEVER deleted by rel.
    Identity unverifiable means keep; run backfill instead.
    """

    removed: list[str] = []
    live_ids = (
        {item.source_id for item in source_index.items.values()}
        if source_index is not None
        else None
    )
    notes = [p for p in target_root.rglob("*.md") if p.name != "_index.md"]
    for path in notes:
        meta = frontmatter_of(path)
        if meta is None or not meta.get("ai_hash"):
            continue  # not ours
        rel = path.relative_to(target_root).as_posix()
        if rel in current:
            continue
        if exclusion_filter is not None and exclusion_filter.excluded(rel):
            continue  # retention: excluded derivatives are never pruned
        sid = meta.get("source_id")
        if live_ids is None or not sid:
            continue  # TICKET-012: unverifiable identity — keep, never rel-delete
        if sid in live_ids:
            continue  # live id: safety net beyond the rel check
        # Dead id: Pair Guard — keep while the processed twin exists
        # (its next enrich would otherwise recreate what prune deletes).
        try:
            if source_root.joinpath(*rel.split("/")).is_file():
                continue
        except OSError:
            continue  # unverifiable: keep rather than delete
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs = [m.group(1) for m in _RE_MEDIA.finditer(text)]
        path.unlink()
        removed.append(rel)
        logger.info(tr("ai.orphan_removed", path=rel))
        for ref in refs:
            if ref.lower().startswith(("http://", "https://", "//", "data:", "#")):
                continue
            _remove_unreferenced(target_root, ref)
    return removed


def _remove_unreferenced(target_root: Path, ref: str) -> None:
    """Delete ``target_root``'s copy of ``ref`` if no other note still links it."""

    bodies = ""
    try:
        for other in target_root.rglob("*.md"):
            if other.name == "_index.md":
                continue
            bodies += other.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if ref in bodies:
        return
    shallow = ref.split("/")
    target = target_root.joinpath(*shallow)
    try:
        if target.is_file() and not _escapes(target_root.resolve(), target.resolve()):
            target.unlink()
    except OSError:
        return


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _escapes(root: Path, candidate: Path) -> bool:
    return root not in candidate.parents and candidate != root


def _make_emitter(on_event: EventCallback, total: int) -> Callable[..., None]:
    """Return a safe emitter bound to the batch total. A broken listener must
    never stop the stage."""

    def emit(type_: EventType, path: str = "", index: int = 0, message: str = "", data: dict | None = None) -> None:
        if on_event is None:
            return
        try:
            on_event(
                Event(type=type_, path=path, index=index, total=total, message=message, data=data or {})
            )
        except Exception:  # noqa: BLE001 - listener errors must not kill the batch
            logger.debug("Ошибка обработчика событий", exc_info=True)

    return emit