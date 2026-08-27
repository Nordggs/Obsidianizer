"""Topic builder — merge several chats (processed notes) into a single
knowledge topic:

    processed/ (selected chats) -> one LLM call -> enriched/topics/<Name>.md

Unlike the per-file AI pass (``postprocess.enrich``), this stage does not copy
the chats: the topic note is a *second-layer* summary that links back to the
source chats via ``[[wiki]]`` links. Ollama stays the only engine; any failure
degrades to a skipped topic, never a crash.

Incrementality is driven by ``topic_hash``: a hash of the sorted ``source_hash``
values of the selected chats. When a topic file already carries the same hash it
is left alone (the source chats did not change). A changed selection produces a
new hash and therefore a regenerated topic.

Guarantees:
- never writes outside ``enriched_root/topics``;
- the input chats are never modified;
- a missing/unreadable/foreign chat degrades to a skipped topic.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from .emit import atomic_write
from .enrich import date_from_meta
from .events import Event, EventType
from .index import build_index_from_dir, frontmatter_of
from .llm import LLMClient
from .postprocess import split_file

logger = logging.getLogger("obsidianizer.topics")

EventCallback = Callable[[Event], None] | None
CancelCheck = Callable[[], bool] | None

TOPICS_DIR = "topics"
TOPIC_LIMIT_CHARS = 16000
TOPIC_NAME_MAX = 40
MAP_CHUNK_CHARS = 6000
MAP_SNIPPET_CHARS = 80
_RE_INVALID_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


@dataclass
class TopicReport:
    """Result of a single group analysis."""

    created: str = ""  # topic path relative to enriched ("" = not written)
    name: str = ""  # sanitized topic name
    skipped: bool = False  # topic already up to date (same topic_hash)
    updated: bool = False  # an existing topic was regenerated in place
    failed: list[str] = field(default_factory=list)
    cancelled: bool = False
    critical_error: str = ""  # "" = no fatal run-level error


@dataclass
class GroupReport:
    """Result of an auto-grouping run over the whole processed collection."""

    created: list[str] = field(default_factory=list)  # topic paths written
    skipped: int = 0  # topics already up to date
    one_chat: int = 0  # clusters of a single chat — not topics
    failed: list[str] = field(default_factory=list)  # per-topic error texts
    total_chats: int = 0
    cancelled: bool = False
    critical_error: str = ""  # "" = no fatal run-level error


def collect_chats(
    target_root: Path,
    rel_files: list[str],
    emit: Callable[..., None],
    cancel_check: CancelCheck,
) -> tuple[list[dict], bool]:
    """Resolve the selected processed notes into (meta, body) chats.

    Emits ``TOPIC_FILE_STARTED/DONE/ERROR`` per file. Returns the readable
    chats and whether the run was cancelled. Only files carrying the
    ``source_hash`` ownership marker are accepted — foreign notes are skipped.
    """

    chats: list[dict] = []
    root_resolved = target_root.resolve()
    for index, rel in enumerate(rel_files, start=1):
        if cancel_check is not None:
            try:
                cancelled = bool(cancel_check())
            except Exception:  # noqa: BLE001 - broken checker must not kill the batch
                cancelled = False
            if cancelled:
                return chats, True

        emit(EventType.TOPIC_FILE_STARTED, path=rel, index=index)
        try:
            candidate = target_root.joinpath(*rel.split("/"))
            resolved = candidate.resolve()
            if root_resolved not in resolved.parents and resolved != root_resolved:
                raise ValueError("путь за пределами папки processed")
            if not candidate.is_file():
                raise ValueError("файл не найден")
            parsed = split_file(candidate.read_text(encoding="utf-8", errors="replace"))
            if parsed is None:
                raise ValueError("не файл Obsidianizer (нет frontmatter)")
            meta, body = parsed
            if not meta.get("source_hash"):
                raise ValueError("нет маркера владения source_hash")
            chats.append({"rel": rel, "meta": meta, "body": body})
            emit(EventType.TOPIC_FILE_DONE, path=rel, index=index)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
            emit(
                EventType.TOPIC_FILE_ERROR,
                path=rel,
                index=index,
                message=str(exc),
            )
            logger.error("Ошибка сбора чата %s: %s", rel, exc)
    return chats, False


def build_payload(chats: list[dict], limit_chars: int = TOPIC_LIMIT_CHARS) -> str:
    """Serialize the chats into a single prompt payload.

    Each chat becomes a ``## Файл`` block with its title/source/date plus a
    body snippet. The character budget is distributed evenly so every chat is
    represented (a huge selection degrades to shorter snippets, never to an
    oversized single request).
    """

    if not chats:
        return ""
    per_chat = max(400, limit_chars // len(chats))
    blocks: list[str] = []
    for chat in chats:
        meta, body = chat["meta"], chat["body"]
        title = str(meta.get("title") or chat["rel"]).strip()[:200]
        service = str(meta.get("service") or "").strip()
        stamp = date_from_meta(meta)
        header = f"## Файл\n**Название:** {title}\n**Источник:** {service}\n**Дата:** {stamp}"
        blocks.append(header + "\n\n" + body[:per_chat].strip())
    return "\n\n---\n\n".join(blocks)


def topic_hash_of(chats: list[dict]) -> str:
    """Content identity of the selection: hash of the sorted source hashes."""

    hashes = sorted(str(c["meta"]["source_hash"]) for c in chats)
    return hashlib.sha1("|".join(hashes).encode("utf-8")).hexdigest()[:12]


def sanitize_name(name: str) -> str:
    """Turn the model's NAME into a safe file stem."""

    cleaned = _RE_INVALID_NAME.sub(" ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:TOPIC_NAME_MAX].strip()
    return cleaned or "Тема"


def build_topic_doc(
    name: str,
    result: dict,
    chats: list[dict],
    topic_hash: str,
    *,
    topic_id: str | None = None,
    created: str | None = None,
    updated: str | None = None,
) -> str:
    """Compose the topic note: frontmatter + knowledge card + sources."""

    fm = {
        "type": "topic",
        "title": name,
        "topic": name,
        "created": created or datetime.now().strftime("%Y-%m-%d"),
        "topic_hash": topic_hash,
        "chats": [c["rel"] for c in chats],
    }
    if topic_id:
        fm["topic_id"] = topic_id
    if updated:
        fm["updated"] = updated
    frontmatter = (
        "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip() + "\n---\n"
    )

    lines = [f"# {name}", ""]
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines += ["## Суть", "", summary, ""]
    for title, key in (("Решения", "decisions"), ("Ключевые факты", "key_facts"), ("Артефакты", "artifacts")):
        items = _clean_list(result.get(key, []))
        if items:
            lines += [f"## {title}", ""]
            lines += [f"- {item}" for item in items]
            lines.append("")

    lines += ["## Источники", ""]
    by_service: dict[str, list[dict]] = {}
    for chat in chats:
        svc = str(chat["meta"].get("service") or "—").strip() or "—"
        by_service.setdefault(svc, []).append(chat)
    for svc in sorted(by_service):
        lines.append(f"### {svc}")
        lines.append("")
        for chat in by_service[svc]:
            lines.append(f"- {_chat_link(chat)}")
        lines.append("")

    return frontmatter + "\n" + "\n".join(lines).rstrip("\n") + "\n"


def find_existing(topic_dir: Path, topic_hash: str) -> Path | None:
    """Return the topic file already carrying ``topic_hash``, if any."""

    if not topic_dir.is_dir():
        return None
    for path in sorted(topic_dir.glob("*.md")):
        meta = frontmatter_of(path)
        if meta is not None and meta.get("topic_hash") == topic_hash:
            return path
    return None


def create_topic(
    target_root: Path,
    enriched_root: Path,
    rel_files: list[str],
    llm: LLMClient,
    *,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
) -> TopicReport:
    """Merge the selected chats into one topic note under ``enriched_root/topics``.

    Skips when a topic with the same ``topic_hash`` already exists (the source
    chats did not change). Rebuilds ``enriched/_index.md`` after writing so the
    new topic appears in navigation.
    """

    report = TopicReport()
    if not rel_files:
        report.critical_error = "Не выбраны файлы"
        _finish(report, on_event)
        return report
    try:
        topic_dir = enriched_root / TOPICS_DIR
        emit = _make_emitter(on_event, len(rel_files))
        emit(
            EventType.TOPIC_SCAN_STARTED,
            path=str(enriched_root),
            message=str(len(rel_files)),
        )

        chats, cancelled = collect_chats(target_root, rel_files, emit, cancel_check)
        if cancelled:
            report.cancelled = True
            _finish(report, on_event)
            return report
        if not chats:
            report.critical_error = "Нет читаемых чатов для объединения"
            _finish(report, on_event)
            return report

        topic_hash = topic_hash_of(chats)
        existing = find_existing(topic_dir, topic_hash)
        if existing is not None:
            report.skipped = True
            report.name = existing.stem
            _finish(report, on_event)
            return report

        result = llm.analyze_topic(build_payload(chats))
        if not result.get("name") and not result.get("summary"):
            report.failed.append("пустой ответ модели")
            _finish(report, on_event)
            return report

        name = sanitize_name(result.get("name", ""))
        out_path = topic_dir / f"{name}.md"
        atomic_write(
            out_path,
            build_topic_doc(
                name, result, chats, topic_hash, topic_id=uuid.uuid4().hex[:12]
            ),
        )
        report.created = out_path.relative_to(enriched_root).as_posix()
        report.name = name

        index_md = build_index_from_dir(enriched_root)
        if index_md:
            atomic_write(enriched_root / "_index.md", index_md)
    except Exception as exc:  # noqa: BLE001 - fatal stage failure must still emit TOPIC_FINISHED
        report.critical_error = str(exc)
        logger.error("Критическая ошибка объединения в тему: %s", exc)
    finally:
        _finish(report, on_event)
    return report


# ── topic lifecycle (per-topic management) ─────────────────────────────────


def collect_chat_cards(target_root: Path) -> list[dict]:
    """Scan processed into compact chat cards (owned notes only)."""

    root = target_root.resolve()
    if not root.is_dir():
        return []
    cards: list[dict] = []
    for path in sorted(root.rglob("*.md")):
        if path.name == "_index.md":
            continue
        try:
            parsed = split_file(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if parsed is None:
            continue
        meta, _ = parsed
        if not meta.get("source_hash"):
            continue
        msgs = meta.get("messages")
        cards.append(
            {
                "rel": path.relative_to(root).as_posix(),
                "title": str(meta.get("title") or path.stem),
                "service": str(meta.get("service") or ""),
                "date": str(meta.get("date") or "")[:10],
                "messages": msgs if isinstance(msgs, dict) else {},
                "summary": str(meta.get("summary") or "")[:120],
            }
        )
    return cards


def find_topic_file(enriched_root: Path, topic_id: str) -> Path | None:
    """Locate the topic note carrying ``topic_id``, if any."""

    topic_dir = enriched_root / TOPICS_DIR
    if not topic_dir.is_dir():
        return None
    for path in topic_dir.glob("*.md"):
        meta = frontmatter_of(path)
        if meta is not None and str(meta.get("topic_id") or "") == topic_id:
            return path
    return None


def _migrate_topic_id(path: Path) -> str:
    """Persist a fresh ``topic_id`` into a legacy topic note; "" on failure."""

    topic_id = uuid.uuid4().hex[:12]
    try:
        parsed = _split_note(path.read_text(encoding="utf-8", errors="replace"))
        if parsed is None:
            return ""
        meta, body = parsed
        meta["topic_id"] = topic_id
        fm = (
            "---\n"
            + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
            + "\n---\n"
        )
        atomic_write(path, fm + "\n" + body.lstrip("\n"))
    except Exception:  # noqa: BLE001 - a broken note must not block listing
        return ""
    return topic_id


def list_topics(enriched_root: Path) -> list[dict]:
    """List topic notes under ``enriched_root/topics``.

    Legacy topics (no ``topic_id``) get one assigned and persisted on first
    listing so the whole collection becomes manageable through the lifecycle
    API. Returns ``[{topic_id, name, file, chats, created}]``.
    """

    topic_dir = enriched_root / TOPICS_DIR
    if not topic_dir.is_dir():
        return []
    topics: list[dict] = []
    for path in sorted(topic_dir.glob("*.md")):
        meta = frontmatter_of(path)
        if meta is None or meta.get("type") != "topic":
            continue
        topic_id = str(meta.get("topic_id") or "")
        if not topic_id:
            topic_id = _migrate_topic_id(path)
            if not topic_id:
                continue
        topics.append(
            {
                "topic_id": topic_id,
                "name": str(meta.get("title") or path.stem),
                "file": path.name,
                "chats": [str(c) for c in (meta.get("chats") or [])],
                "created": str(meta.get("created") or "")[:10],
            }
        )
    return topics


def get_topic(enriched_root: Path, topic_id: str) -> dict | None:
    """Return the topic note content plus its frontmatter summary."""

    path = find_topic_file(enriched_root, topic_id)
    if path is None:
        return None
    meta = frontmatter_of(path)
    body = path.read_text(encoding="utf-8", errors="replace")
    return {
        "topic_id": topic_id,
        "name": str((meta or {}).get("title") or path.stem),
        "file": path.name,
        "chats": [str(c) for c in ((meta or {}).get("chats") or [])],
        "created": str((meta or {}).get("created") or "")[:10],
        "updated": str((meta or {}).get("updated") or "")[:10],
        "body": body,
    }


def update_topic(
    enriched_root: Path,
    target_root: Path,
    topic_id: str,
    rel_files: list[str],
    llm: LLMClient,
    *,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
) -> TopicReport:
    """Regenerate an existing topic with a new chat selection, in place.

    The topic keeps its ``topic_id`` and name; only the knowledge card is
    recomputed over the new selection. Skipped when the selection is
    unchanged (same ``topic_hash``). Rebuilds ``enriched/_index.md`` after a
    write.
    """

    report = TopicReport()
    existing = find_topic_file(enriched_root, topic_id)
    if existing is None:
        report.critical_error = f"Тема {topic_id} не найдена"
        _finish(report, on_event)
        return report
    if not rel_files:
        report.critical_error = "Не выбраны файлы"
        _finish(report, on_event)
        return report
    try:
        old_meta = frontmatter_of(existing) or {}
        name = str(old_meta.get("title") or existing.stem)
        emit = _make_emitter(on_event, len(rel_files))
        emit(
            EventType.TOPIC_SCAN_STARTED,
            path=str(enriched_root),
            message=str(len(rel_files)),
        )

        chats, cancelled = collect_chats(target_root, rel_files, emit, cancel_check)
        if cancelled:
            report.cancelled = True
            _finish(report, on_event)
            return report
        if not chats:
            report.critical_error = "Нет читаемых чатов для объединения"
            _finish(report, on_event)
            return report

        topic_hash = topic_hash_of(chats)
        if topic_hash == old_meta.get("topic_hash"):
            report.skipped = True
            report.name = name
            _finish(report, on_event)
            return report

        result = llm.analyze_topic(build_payload(chats))
        if not result.get("name") and not result.get("summary"):
            report.failed.append("пустой ответ модели")
            _finish(report, on_event)
            return report

        # The model's suggested name is ignored: updates keep the topic's own
        # name so the file path (and any wiki links) stay stable.
        atomic_write(
            existing,
            build_topic_doc(
                name,
                result,
                chats,
                topic_hash,
                topic_id=topic_id,
                created=old_meta.get("created"),
                updated=datetime.now().strftime("%Y-%m-%d"),
            ),
        )
        report.updated = True
        report.name = name
        report.created = existing.relative_to(enriched_root).as_posix()
        emit(EventType.TOPIC_UPDATED, path=name, message=name)
        _rebuild_index(enriched_root)
    except Exception as exc:  # noqa: BLE001 - fatal failure must still emit TOPIC_FINISHED
        report.critical_error = str(exc)
        logger.error("Критическая ошибка обновления темы: %s", exc)
    finally:
        _finish(report, on_event)
    return report


def rename_topic(
    enriched_root: Path, topic_id: str, new_name: str, *, on_event: EventCallback = None
) -> dict:
    """Rename a topic note (file + frontmatter) keeping ``topic_id``."""

    path = find_topic_file(enriched_root, topic_id)
    if path is None:
        return {"ok": False, "error": "Тема не найдена"}
    name = sanitize_name(new_name)
    if not name or name == "Тема" and not str(new_name or "").strip():
        return {"ok": False, "error": "Пустое имя темы"}
    topic_dir = enriched_root / TOPICS_DIR
    new_path = topic_dir / f"{name}.md"
    if new_path.resolve() != path.resolve() and new_path.exists():
        return {"ok": False, "error": "Тема с таким именем уже существует"}
    try:
        parsed = _split_note(path.read_text(encoding="utf-8", errors="replace"))
        if parsed is None:
            return {"ok": False, "error": "Не удалось прочитать тему"}
        meta, body = parsed
        meta["title"] = name
        meta["topic"] = name
        fm = (
            "---\n"
            + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
            + "\n---\n"
        )
        atomic_write(new_path, fm + "\n" + body.lstrip("\n"))
        if new_path.resolve() != path.resolve():
            path.unlink()
        _rebuild_index(enriched_root)
        emit = _make_emitter(on_event, 0)
        emit(EventType.TOPIC_RENAMED, path=new_path.name, message=name)
        return {"ok": True, "topic_id": topic_id, "name": name, "file": new_path.name}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def delete_topic(
    enriched_root: Path, topic_id: str, *, on_event: EventCallback = None
) -> dict:
    """Delete a topic note (ownership marker: ``type: topic`` + ``topic_id``)."""

    path = find_topic_file(enriched_root, topic_id)
    if path is None:
        return {"ok": False, "error": "Тема не найдена"}
    try:
        path.unlink()
        _rebuild_index(enriched_root)
        emit = _make_emitter(on_event, 0)
        emit(EventType.TOPIC_DELETED, path=path.name, message=path.name)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def chats_without_topic(target_root: Path, enriched_root: Path) -> list[dict]:
    """Chat cards from ``target_root`` not referenced by any topic note."""

    covered: set[str] = set()
    for topic in list_topics(enriched_root):
        covered.update(topic["chats"])
    return [c for c in collect_chat_cards(target_root) if c["rel"] not in covered]


def _rebuild_index(enriched_root: Path) -> None:
    index_md = build_index_from_dir(enriched_root)
    if index_md:
        atomic_write(enriched_root / "_index.md", index_md)


# ── auto-grouping (whole collection) ───────────────────────────────────────


def collect_catalog(target_root: Path) -> list[dict]:
    """Scan the whole processed folder into compact chat cards.

    Only files carrying the ``source_hash`` ownership marker are listed;
    ``_index.md`` and foreign notes are excluded. Each card carries the chat
    title/source/date plus a short body snippet as a clustering hint.
    """

    root = target_root.resolve()
    if not root.is_dir():
        return []
    catalog: list[dict] = []
    for path in sorted(root.rglob("*.md")):
        if path.name == "_index.md":
            continue
        try:
            parsed = split_file(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if parsed is None:
            continue
        meta, body = parsed
        if not meta.get("source_hash"):
            continue
        catalog.append(
            {
                "rel": path.relative_to(root).as_posix(),
                "title": str(meta.get("title") or path.stem).strip(),
                "service": str(meta.get("service") or "").strip(),
                "date": date_from_meta(meta),
                "snippet": _snippet(body),
            }
        )
    return catalog


def _snippet(body: str) -> str:
    """First non-markdown line of the chat body, truncated — a clustering hint."""

    for line in (body or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(("#", ">", "-", "*", "|")) or re.match(r"^\d+[.)]", text):
            continue
        return text[:MAP_SNIPPET_CHARS]
    return ""


def _chunk_catalog(catalog: list[dict], chunk_chars: int = MAP_CHUNK_CHARS) -> list[list[dict]]:
    """Split the catalog into payload-sized chunks (by card length)."""

    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for entry in catalog:
        card = _map_card(entry, 0)
        if current and size + len(card) > chunk_chars:
            chunks.append(current)
            current = []
            size = 0
        current.append(entry)
        size += len(card)
    if current:
        chunks.append(current)
    return chunks


def _map_card(entry: dict, idx: int) -> str:
    parts = [f"[{idx}]"]
    title = entry.get("title") or ""
    if title:
        parts.append(title)
    extras = [p for p in (entry.get("service") or "", entry.get("date") or "") if p]
    if extras:
        parts.append("(" + ", ".join(extras) + ")")
    line = " ".join(parts)
    snippet = entry.get("snippet") or ""
    if snippet:
        line += " — " + snippet
    return line


def build_map_payload(chunk: list[dict], start: int, known: list[str]) -> str:
    """Serialize one catalog chunk into a topic-map prompt payload.

    Cards carry 1-based *global* indices so clusters from different chunks can
    be merged safely. Already-discovered topic names are appended so the model
    reuses them across chunks (stable cluster names).
    """

    cards = [_map_card(e, start + i + 1) for i, e in enumerate(chunk)]
    lines = ["Список чатов:", *cards]
    if known:
        lines += ["", "Уже выделенные темы (используй те же названия, если тема совпадает):"]
        lines += [f"- {name}" for name in known]
    return "\n".join(lines)


def build_topic_map(
    target_root: Path,
    llm: LLMClient,
    *,
    catalog: list[dict] | None = None,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
) -> dict[str, list[str]] | None:
    """Cluster the whole processed collection into topics by LLM.

    Returns ``{topic_name: [rel, ...]}`` (possibly empty) or ``None`` on
    cancel. Large collections are split into payload-sized chunks; already
    discovered topic names are fed back into the next chunk so the same theme
    keeps the same name. A chat belongs to the first matching topic only.
    """

    cards = catalog if catalog is not None else collect_catalog(target_root)
    if not cards:
        return {}
    chunks = _chunk_catalog(cards)
    known: list[str] = []
    clusters: dict[str, list[str]] = {}
    assigned: set[str] = set()
    pos = 0
    for chunk in chunks:
        if cancel_check is not None:
            try:
                if bool(cancel_check()):
                    return None
            except Exception:  # noqa: BLE001 - broken checker must not kill the run
                pass
        for group in llm.analyze_topic_map(build_map_payload(chunk, pos, known)):
            name = str(group.get("name") or "").strip()
            if not name:
                continue
            bucket = clusters.setdefault(name, [])
            for raw_id in group.get("ids") or []:
                try:
                    idx = int(str(raw_id).strip())
                except ValueError:
                    continue
                at = idx - 1  # 1-based global index across the whole collection
                if not (0 <= at < len(cards)):
                    continue
                rel = cards[at]["rel"]
                if rel in assigned:
                    continue  # a chat belongs to the first matching topic
                assigned.add(rel)
                bucket.append(rel)
        pos += len(chunk)
        known = [name for name in clusters if clusters[name]]
    return {name: rels for name, rels in clusters.items() if rels}


def group_all(
    target_root: Path,
    enriched_root: Path,
    llm: LLMClient,
    *,
    on_event: EventCallback = None,
    cancel_check: CancelCheck = None,
) -> GroupReport:
    """Auto-group the whole processed collection into topic notes.

    One clustering pass over all chats (``build_topic_map``) yields the topic
    buckets; each bucket of two or more chats is then merged through the same
    ``create_topic`` path (incremental via ``topic_hash``). Single-chat buckets
    are counted and skipped. Never deletes existing topics.
    """

    report = GroupReport()
    try:
        catalog = collect_catalog(target_root)
        report.total_chats = len(catalog)
        _make_emitter(on_event, len(catalog))(
            EventType.TOPIC_MAP_STARTED,
            path=str(enriched_root),
            message=str(len(catalog)),
        )
        if not catalog:
            report.critical_error = "Нет обработанных чатов для авто-группировки"
            _finish_group(report, on_event)
            return report

        clusters = build_topic_map(
            target_root, llm, catalog=catalog, on_event=on_event, cancel_check=cancel_check
        )
        if clusters is None:
            report.cancelled = True
            _finish_group(report, on_event)
            return report

        items = sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
        topic_emit = _make_emitter(on_event, len(items))
        for i, (name, rels) in enumerate(items, start=1):
            if cancel_check is not None:
                try:
                    if bool(cancel_check()):
                        report.cancelled = True
                        break
                except Exception:  # noqa: BLE001 - broken checker must not kill the run
                    pass
            if report.cancelled:
                break
            topic_emit(EventType.TOPIC_FILE_STARTED, path=name, index=i, message=name)
            if len(rels) < 2:
                report.one_chat += 1
                topic_emit(
                    EventType.TOPIC_FILE_DONE,
                    path=name,
                    index=i,
                    message="один чат — тема не создана",
                )
                continue

            topic = create_topic(
                target_root, enriched_root, rels, llm,
                on_event=None,  # the group drives the progress itself
                cancel_check=cancel_check,
            )
            if topic.created:
                report.created.append(topic.created)
                topic_emit(EventType.TOPIC_FILE_DONE, path=topic.name, index=i, message=topic.created)
            elif topic.skipped:
                report.skipped += 1
                topic_emit(EventType.TOPIC_FILE_DONE, path=topic.name, index=i, message="актуальна")
            elif topic.critical_error:
                report.failed.append(topic.critical_error)
                topic_emit(EventType.TOPIC_FILE_ERROR, path=topic.name, index=i, message=topic.critical_error)
            else:
                message = "; ".join(topic.failed) or "тема не создана"
                report.failed.append(message)
                topic_emit(EventType.TOPIC_FILE_ERROR, path=topic.name, index=i, message=message)
    except Exception as exc:  # noqa: BLE001 - fatal stage failure must still emit TOPIC_FINISHED
        report.critical_error = str(exc)
        logger.error("Критическая ошибка авто-группировки: %s", exc)
    finally:
        _finish_group(report, on_event)
    return report


def _finish_group(report: GroupReport, on_event: EventCallback) -> None:
    emit = _make_emitter(on_event, 0)
    if report.cancelled:
        message = "Авто-группировка отменена"
    elif report.critical_error:
        message = f"Критическая ошибка: {report.critical_error}"
    else:
        message = (
            f"Авто-группировка: создано={len(report.created)}, "
            f"актуально={report.skipped}, пропущено={report.one_chat}, "
            f"ошибок={len(report.failed)}"
        )
    emit(EventType.TOPIC_FINISHED, message=message)


# ── internals ──────────────────────────────────────────────────────────────


def _chat_link(chat: dict) -> str:
    stem = chat["rel"].replace("\\", "/").rsplit(".md", 1)[0]
    title = str(chat["meta"].get("title") or chat["rel"]).strip()
    link = f"[[{stem}]]" if title == chat["rel"] or not title else f"[[{stem}|{title}]]"
    extra = _chat_extra(chat["meta"])
    return link + extra


def _chat_extra(meta: dict) -> str:
    parts: list[str] = []
    stamp = date_from_meta(meta)
    if stamp:
        parts.append(stamp)
    msgs = meta.get("messages") or {}
    if isinstance(msgs, dict) and msgs.get("total"):
        parts.append(f"{msgs['total']} сообщений")
    return f" — {', '.join(parts)}" if parts else ""


def _clean_list(items: list) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        bare = text.strip("- •\t")
        if not bare or bare.lower().strip(" .") == "нет":
            continue
        cleaned.append(text)
    return cleaned


def _split_note(text: str) -> tuple[dict, str] | None:
    """Split a produced note into (frontmatter dict, rest of text).

    Unlike ``postprocess.split_file`` this does not require the card/body
    separator: topic notes (``build_topic_doc``) have no card block.
    """

    if not text.startswith("---\n"):
        return None
    fm_end = text.find("\n---", 4)
    if fm_end < 0:
        return None
    try:
        meta = yaml.safe_load(text[4:fm_end])
    except Exception:  # noqa: BLE001 - tolerate malformed notes
        return None
    if not isinstance(meta, dict):
        return None
    return meta, text[fm_end + 4 :]


def _finish(report: TopicReport, on_event: EventCallback) -> None:
    emit = _make_emitter(on_event, 0)
    if report.cancelled:
        message = "Объединение в тему отменено"
    elif report.critical_error:
        message = f"Критическая ошибка: {report.critical_error}"
    elif report.skipped:
        message = f"Тема актуальна: {report.name} (чаты не менялись)"
    elif report.updated:
        message = f"Тема обновлена: {report.name}"
    elif report.created:
        message = f"Тема создана: {report.name}"
    else:
        message = "Тема не создана"
    emit(EventType.TOPIC_FINISHED, message=message)


def _make_emitter(on_event: EventCallback, total: int) -> Callable[..., None]:
    """Return a safe emitter bound to the batch total. A broken listener must
    never stop the stage."""

    def emit(type_: EventType, path: str = "", index: int = 0, message: str = "") -> None:
        if on_event is None:
            return
        try:
            on_event(
                Event(type=type_, path=path, index=index, total=total, message=message)
            )
        except Exception:  # noqa: BLE001 - listener errors must not kill the batch
            logger.debug("Ошибка обработчика событий", exc_info=True)

    return emit