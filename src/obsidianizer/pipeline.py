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
from .config import PROCESS_VERSION, Settings
from .emit import atomic_write, copy_media, write_note
from .events import Event, EventType
from .index import build_index, build_index_from_dir
from .llm import LLMClient
from .manifest import prune as manifest_prune
from .manifest import read_manifest, write_manifest
from .models import ProcessedFile, SourceFile
from .registry import ProcessorRegistry

logger = logging.getLogger("obsidianizer.pipeline")

_HASH_RE = re.compile(r"^source_hash:\s*(\w+)$", re.MULTILINE)
_VERSION_RE = re.compile(r"^process_version:\s*(\S+)$", re.MULTILINE)

EventCallback = Callable[[Event], None] | None


def _make_emitter(on_event: EventCallback, total: int) -> Callable[..., None]:
    """Return a safe emitter bound to the batch total.

    A broken listener must never stop processing, so exceptions raised by the
    callback are swallowed and logged.
    """

    def emit(type_: EventType, path: str = "", index: int = 0, message: str = "") -> None:
        if on_event is None:
            return
        try:
            on_event(Event(type=type_, path=path, index=index, total=total, message=message))
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
        f"обработано={report.processed}, пропущено={report.skipped}, "
        f"ошибок={len(report.failed)}"
    )
    if report.cancelled:
        return f"отменено с сохранением уже записанных файлов ({counts})"
    if report.critical_error:
        return f"критическая ошибка: {report.critical_error} ({counts})"
    return counts


def run(
    registry: ProcessorRegistry,
    settings: Settings,
    llm: LLMClient | None,
    *,
    dry_run: bool = False,
    prune: bool = False,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
) -> Report:
    """Run the pipeline once.

    ``on_event`` is an optional listener for progress events.
    ``cancel_check`` is polled between files (never inside a file, never during
    an LLM call): returning True stops after the current file is finished and
    preserves ownership of the files already written. A failing listener must
    never stop processing.
    """

    source_root = settings.source.resolve()
    target_root = settings.target.resolve()
    report = Report()
    current: set[str] = set()
    index_records: list[dict] = []
    emit: Callable[..., None] | None = None

    try:
        sources = registry.scan(source_root)
        total = len(sources)
        logger.info("Найдено %d файлов для обработки", total)
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
                if llm is not None and settings.llm_enabled:
                    emit(EventType.LLM_STARTED, path=out_rel, index=index)

                proc = _process_one(registry, llm, sf, current_hash, settings.llm_enabled)
                index_records.append(
                    {
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
                logger.error("Ошибка обработки %s: %s", sf.rel_path, exc)

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
            index_md = (
                build_index_from_dir(target_root)
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
                    report.pruned = manifest_prune(old, frozenset(current), target_root)
                write_manifest(target_root, current)
    except Exception as exc:  # noqa: BLE001 - fatal run-level failure must still emit FINISHED
        report.critical_error = str(exc)
        logger.error("Критическая ошибка запуска: %s", exc)
    finally:
        if emit is None:
            # scan itself failed before an emitter existed — emit one anyway
            emit = _make_emitter(on_event, 0)
        emit(
            EventType.FINISHED,
            index=report.processed,
            message=_finish_message(report),
        )

    return report


def _process_one(
    registry: ProcessorRegistry, llm: LLMClient | None, sf: SourceFile,
    current_hash: str, llm_enabled: bool,
) -> ProcessedFile:
    processor = registry.processor_for(sf.ext)
    if processor is None:
        raise ValueError(f"нет процессора для расширения {sf.ext}")

    meta = processor.parse(sf)
    meta["process_version"] = PROCESS_VERSION
    body = processor.body(sf)
    refs = processor.media_refs(sf)

    summary, tags = "", []
    if llm is not None and llm_enabled:
        summary, tags = llm.summarize(body)

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
    frontmatter = enrich_mod.build_frontmatter(meta, summary, tags, current_hash)
    card = enrich_mod.build_card(meta, summary, tags)
    navigation = enrich_mod.build_navigation(meta)
    proc.final_md = enrich_mod.compose(frontmatter, card, body, navigation)  # type: ignore[attr-defined]
    return proc