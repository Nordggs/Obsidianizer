"""Pipeline orchestrator — scan → extract → enrich → emit → manifest.

The core holds zero file-type logic: processors arrive via the registry.
Exit order is fixed: emit everything, collect the current manifest, (optionally)
prune OLD - CURRENT, then write the new manifest last, atomically.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import enrich as enrich_mod
from .config import PROCESS_VERSION, Settings, ensure_source_namespace
from .emit import atomic_write, copy_media, write_note
from .events import Event, EventType
from .exclusions import build_exclusion
from .nav_index import build_index, build_index_from_dir, frontmatter_of
from .manifest import read_manifest, write_manifest
from .models import ProcessedFile, SourceFile
from .registry import ProcessorRegistry
from .source_index import build_source_index
from .i18n import tr

logger = logging.getLogger("obsidianizer.pipeline")

_HASH_RE = re.compile(r"^source_hash:\s*(\w+)$", re.MULTILINE)
_VERSION_RE = re.compile(r"^process_version:\s*(\S+)$", re.MULTILINE)

EventCallback = Callable[[Event], None] | None


def _make_emitter(on_event: EventCallback, total: int) -> Callable[..., None]:
    """Return a safe emitter bound to the batch total.

    A broken listener must never stop processing, so exceptions raised by the
    callback are swallowed and logged.
    """

    def emit(type_: EventType, path: str = "", index: int = 0, message: str = "", data: dict | None = None) -> None:
        if on_event is None:
            return
        try:
            on_event(Event(type=type_, path=path, index=index, total=total, message=message, data=data or {}))
        except Exception:  # noqa: BLE001 - listener errors must not kill the batch
            logger.debug("Ошибка обработчика событий", exc_info=True)

    return emit


@dataclass
class Report:
    processed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    cancelled: bool = False
    critical_error: str = ""  # "" = no fatal run-level error
    # TICKET-007: SourceIndex built beside the scan (consumers switch to it
    # in 008/009; in 007 it is only built + available, behaviour unchanged).
    source_index: SourceIndex | None = None


def source_hash_of(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def has_matching_hash(
    out_path: Path, current_hash: str, process_version: int | None = None
) -> bool:
    """True when the produced note matches both the raw content hash and the
    current format version. A version bump forces a rewrite even though the
    ``source_hash`` did not change."""

    if not out_path.exists():
        return False
    try:
        text = out_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    m = _HASH_RE.search(text)
    if not m or m.group(1) != current_hash:
        return False
    if process_version is not None:
        v = _VERSION_RE.search(text)
        if not v or v.group(1) != str(process_version):
            return False
    return True


CancelCheck = Callable[[], bool] | None


def _finish_message(report: Report) -> str:
    """Build the human-readable summary for the single FINISHED event."""

    counts = (
        f"{tr('fin.processed', n=report.processed)}, {tr('fin.skipped', n=report.skipped)}, "
        f"{tr('fin.errors', n=len(report.failed))}"
    )
    if report.cancelled:
        return f"{tr('fin.cancelled')} ({counts})"
    if report.critical_error:
        return f"{tr('fin.critical', err=report.critical_error)} ({counts})"
    return counts


def _finish_data(report: Report) -> dict:
    """Machine-readable FINISHED payload — the UI never parses the message."""

    return {
        "mode": "import",
        "cancelled": bool(report.cancelled),
        "critical": report.critical_error or "",
        "processed": report.processed,
        "skipped": report.skipped,
        "errors": len(report.failed),
    }


def run(
    registry: ProcessorRegistry,
    settings: Settings,
    *,
    dry_run: bool = False,
    prune: bool = False,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
) -> Report:
    """Run the pipeline once.

    Import is LLM-free by contract (TICKET-012): AI enrichment happens only
    in stage 2 (``postprocess.enrich``). There is no LLM parameter or branch.

    ``on_event`` is an optional listener for progress events.
    ``cancel_check`` is polled between files (never inside a file): returning
    True stops after the current file is finished and preserves ownership of
    the files already written. A failing listener must never stop processing.
    """

    source_root = settings.source.resolve()
    target_root = settings.target.resolve()
    report = Report()
    current: set[str] = set()
    index_records: list[dict] = []
    emit: Callable[..., None] | None = None

    try:
        # TICKET-009 single walk: the SourceIndex is the only filesystem
        # traversal; the candidate list is projected from it with the same
        # filters as ``registry.scan`` (which stays for the scan preview).
        # The namespace persists only into an existing config file — never
        # into a fresh path (tests and first runs stay side-effect free).
        _excl = build_exclusion(settings)
        namespace, _ = ensure_source_namespace(
            settings,
            persist=bool(
                settings.config_path
                and Path(str(settings.config_path)).exists()
            ),
        )
        report.source_index = build_source_index(source_root, namespace, _excl)
        sources = registry.project(report.source_index, source_root)
        total = len(sources)
        logger.info(tr("pl.found_files", n=total))
        emit = _make_emitter(on_event, total)
        emit(EventType.SCAN_STARTED, path=str(source_root))

        for index, sf in enumerate(sources, start=1):
            cancelled = False
            if cancel_check is not None:
                try:
                    cancelled = bool(cancel_check())
                except Exception:  # noqa: BLE001 - a broken checker must not kill the batch
                    cancelled = False
            if cancelled:
                report.cancelled = True
                break

            try:
                out_rel = sf.rel_path
                out_path = target_root / sf.rel_path
                current_hash = source_hash_of(sf.abs_path)

                if has_matching_hash(out_path, current_hash, PROCESS_VERSION):
                    report.skipped += 1
                    current.add(out_rel)
                    emit(EventType.FILE_SKIPPED, path=out_rel, index=index)
                    continue

                emit(EventType.FILE_STARTED, path=out_rel, index=index)

                proc = _process_one(
                    registry, sf, current_hash, source_id=sf.source_id,
                )
                index_records.append(
                    {
                        "rel": proc.out_rel_path,
                        "source_id": proc.meta.get("source_id"),
                        "title": proc.meta.get("title"),
                        "file_stem": Path(proc.out_rel_path).stem,
                        "date": enrich_mod.date_from_meta(proc.meta),
                        "service": proc.meta.get("service"),
                        "messages": proc.meta.get("messages"),
                        "summary": proc.summary,
                    }
                )

                if not dry_run:
                    write_note(target_root, proc, proc.final_md)
                    current.add(out_rel)
                    for copied in copy_media(source_root, target_root, proc):
                        current.add(copied)
                report.processed += 1
                report.created.append(out_rel)
                emit(EventType.FILE_DONE, path=out_rel, index=index)
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
                report.failed.append(f"{sf.rel_path}: {exc}")
                emit(EventType.FILE_ERROR, path=sf.rel_path, index=index, message=str(exc))
                logger.error(tr("pl.file_error", path=sf.rel_path, err=exc))

        if report.cancelled:
            # Stop requested: keep ownership of what was written, never prune
            # (an unfinished scan must not delete anything) and do not publish a
            # partial navigation index. The manifest is refreshed only when
            # files were actually produced — otherwise the previous one stays.
            if not dry_run and report.processed > 0:
                write_manifest(target_root, current)
        else:
            # Generated navigation index: for real runs compiled from the
            # produced notes on disk (stable across incremental reruns); for
            # dry-runs previewed from the batch records.
            # TICKET-006: excluded rels are hidden from the representation
            # (files + manifest entries are kept).
            if not dry_run:
                index_records = [
                    r
                    for r in index_records
                    if not _excl.excluded(str(r.get("rel") or ""))
                ]
            index_md = (
                build_index_from_dir(target_root, _excl)
                if not dry_run
                else build_index(index_records)
            )
            if index_md and not dry_run:
                atomic_write(target_root / "_index.md", index_md)
                current.add("_index.md")

            # Ownership manifest, fixed order
            if not dry_run:
                old = read_manifest(target_root)
                if prune:
                    # TICKET-006 retention: derivatives of excluded rels are
                    # held in `current` so prune never deletes them just
                    # because an exclusion appeared.
                    current |= {rel for rel in old if _excl.excluded(rel)}
                    # TICKET-008C: notes prune by id (dead source_id), media
                    # stay path-based (they own no source_id).
                    report.pruned = _prune_manifest_notes_and_media(
                        old, current, target_root, report.source_index
                    )
                write_manifest(target_root, current)
    except Exception as exc:  # noqa: BLE001 - fatal run-level failure must still emit FINISHED
        report.critical_error = str(exc)
        logger.error(tr("pl.critical_log", err=exc))
    finally:
        if emit is None:
            # scan itself failed before an emitter existed — emit one anyway
            emit = _make_emitter(on_event, 0)
        emit(
            EventType.FINISHED,
            index=report.processed,
            message=_finish_message(report),
            data=_finish_data(report),
        )

    return report


def _prune_manifest_notes_and_media(
    old: frozenset[str], current: set[str], target_root: Path, source_index=None
) -> list[str]:
    """TICKET-008C prune split + TICKET-012 identity contract.

    Notes prune only by dead ``source_id``; media prune by path. Anything in
    ``current`` (incl. the 006 retention set) is kept. Notes whose id is still
    live are kept as a safety net beyond the rel check. A note WITHOUT a
    ``source_id`` — or any note when no ``SourceIndex`` is provided — is NEVER
    deleted by rel (identity unverifiable → keep; run backfill instead).
    Media own no ``source_id`` and keep the legacy path rule. Stale entries
    (file already gone) are dropped by the manifest rewrite that follows.
    """

    from .manifest import _safe_target_path

    live_ids = (
        {item.source_id for item in source_index.items.values()}
        if source_index is not None
        else None
    )
    try:
        root_resolved = target_root.resolve()
    except OSError:
        return []
    removed: list[str] = []
    for rel in sorted(set(old) - set(current)):
        resolved = _safe_target_path(target_root, rel)
        if resolved is None or resolved == root_resolved:
            continue
        try:
            if not resolved.is_file():
                continue
            meta = frontmatter_of(resolved)
        except OSError:
            continue
        is_note = bool(meta) and bool(meta.get("source_hash") or meta.get("source_id"))
        if is_note:
            # Identity contract (TICKET-012): only a dead source_id deletes a
            # note. No id — or no index to check it against — means keep.
            sid = meta.get("source_id") if meta else None
            if not sid or live_ids is None:
                continue
            if sid in live_ids:
                continue
        try:
            resolved.unlink()
            removed.append(rel)
        except OSError:
            continue
    return removed


def _process_one(
    registry: ProcessorRegistry, sf: SourceFile,
    current_hash: str, source_id: str | None = None,
) -> ProcessedFile:
    """Process one file (deterministic; TICKET-012: no in-pipeline LLM)."""

    processor = registry.processor_for(sf.ext)
    if processor is None:
        raise ValueError(tr("pl.no_processor", ext=sf.ext))

    meta = processor.parse(sf)
    meta["process_version"] = PROCESS_VERSION
    body = processor.body(sf)
    refs = processor.media_refs(sf)

    summary, tags = "", []
    proc = ProcessedFile(
        source=sf,
        meta=meta,
        body=body,
        media_refs=refs,
        summary=summary,
        tags=tags,
        source_hash=current_hash,
        out_rel_path=sf.rel_path,
    )
    frontmatter = enrich_mod.build_frontmatter(
        meta, summary, tags, current_hash, source_id=source_id
    )
    card = enrich_mod.build_card(meta, summary, tags)
    navigation = enrich_mod.build_navigation(meta)
    proc.final_md = enrich_mod.compose(frontmatter, card, body, navigation)  # type: ignore[attr-defined]
    return proc