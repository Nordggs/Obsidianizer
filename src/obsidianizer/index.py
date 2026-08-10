"""_index.md generator — a navigation table compiled from the produced notes."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def build_index(records: list[dict]) -> str:
    """records: list of {"title", "file_stem", "date", "service", "messages", "summary"}."""

    header = ["# Индекс обработанных файлов", ""]
    if not records:
        header.append("Пока нет записей.")
        return "\n".join(header) + "\n"

    lines = [
        "| Дата | Провайдер | Тема | Сообщений | Резюме |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(records, key=lambda x: (x.get("date") or "", x.get("title") or "")):
        stem = r.get("file_stem") or ""
        title = str(r.get("title") or stem or "—")
        link = f"[[{_wiki_escape(stem)}|{_cell(title)}]]" if stem else _cell(title)
        msgs = r.get("messages")
        msg_cell = "—"
        if isinstance(msgs, dict) and msgs.get("total"):
            msg_cell = f"{msgs['total']}"
        summary = _cell(str(r.get("summary") or "")[:120])
        lines.append(
            f"| {_cell(r.get('date'))} | {_cell(r.get('service'))} | {link} | {msg_cell} | {summary} |"
        )
    return "\n".join(header + lines) + "\n"


def frontmatter_of(path: Path) -> dict | None:
    """Parse the YAML frontmatter block of a produced note, if present."""

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    block = text[4:end]
    try:
        data = yaml.safe_load(block)
    except Exception:  # noqa: BLE001 - tolerate malformed notes
        return None
    return data if isinstance(data, dict) else None


def build_index_from_dir(target_root: Path) -> str:
    """Navigation index compiled from every note already produced in target."""

    records: list[dict] = []
    for path in sorted(target_root.rglob("*.md")):
        if path.name == "_index.md":
            continue
        meta = frontmatter_of(path)
        if meta is None:
            continue
        records.append(
            {
                "title": meta.get("title"),
                "file_stem": path.stem,
                "date": str(meta.get("date") or "")[:10],
                "service": meta.get("service"),
                "messages": meta.get("messages")
                if isinstance(meta.get("messages"), dict)
                else None,
                "summary": meta.get("summary"),
            }
        )
    return build_index(records)


def _wiki_escape(stem: str) -> str:
    return stem.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()