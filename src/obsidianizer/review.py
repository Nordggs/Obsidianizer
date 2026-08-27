"""AI folder review (GUI tab 3).

Read-only contract (same as the Folder Obsidianizer): the source tree is
never modified; the only file written per folder is ``<folder name>_обзор.md``
right next to the project card.

Flow: ``scan_tree`` (from ``obsidianize``) → ``collect_payload`` (metadata +
card + optional text excerpts) → ``build_request`` (user message) →
``llm.chat`` → ``build_review_markdown`` → ``save_review`` (atomic write).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .obsidianize import (
    FolderScan,
    ObsidianizeConfig,
    card_path_for,
    review_file_path,
)

REVIEW_TEXT_EXTS = {".txt", ".md"}
REVIEW_MAX_TEXT_FILES = 8
REVIEW_MAX_TEXT_CHARS = 6000
REVIEW_SUFFIX = "_обзор.md"

_CATEGORY_LABELS = {
    "drafting": "Чертежи",
    "tables": "Таблицы",
    "docs": "Документы",
    "images": "Изображения",
    "other": "Прочее",
}


def review_file_for(folder: FolderScan) -> Path:
    """The ``<folder name>_обзор.md`` path (same folder as the project card)."""

    return review_file_path(folder)


def _category(ext: str, cfg: ObsidianizeConfig) -> str:
    for cat, exts in cfg.categories.items():
        if ext in exts:
            return cat
    return "other"


def collect_payload(
    folder: FolderScan,
    include_text: bool = True,
    cfg: ObsidianizeConfig | None = None,
) -> dict:
    """Read-only payload for one folder: file metadata, card, text excerpts."""

    cfg = cfg or ObsidianizeConfig()
    files: list[dict] = []
    for fe in folder.files:
        if fe.ext == "md":
            continue  # cards are handled separately, not catalogued
        files.append(
            {
                "name": fe.name,
                "size": fe.size,
                "ext": fe.ext or "",
                "category": _CATEGORY_LABELS.get(_category(fe.ext, cfg), "Прочее"),
            }
        )
    # Text excerpts come from a dedicated pass: scan_tree drops .md by default,
    # and the card itself must never land in the text excerpts.
    texts: list[dict] = []
    if include_text:
        for p in sorted(folder.path.iterdir(), key=lambda p: p.name.casefold()):
            if not p.is_file() or p.suffix.lower() not in REVIEW_TEXT_EXTS:
                continue
            if p.suffix.lower() == ".md" and p.stem == folder.path.name:
                continue  # the project card — its content goes to "card"
            if len(texts) >= REVIEW_MAX_TEXT_FILES:
                break
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[
                    :REVIEW_MAX_TEXT_CHARS
                ]
            except OSError:
                continue
            if text.strip():
                texts.append({"name": p.name, "text": text})
    card_text = ""
    card = card_path_for(folder)
    if card.exists():
        try:
            card_text = card.read_text(encoding="utf-8", errors="replace")[
                :REVIEW_MAX_TEXT_CHARS
            ]
        except OSError:
            card_text = ""
    return {
        "rel": folder.rel,
        "name": folder.path.name,
        "files": files,
        "subfolders": list(folder.subfolders),
        "images": len(folder.images),
        "card": card_text,
        "texts": texts,
    }


def build_request(payloads: list[dict], include_text: bool = True) -> str:
    """One user message describing every selected folder."""

    blocks = []
    for p in payloads:
        lines = [f"## Папка: {p['name']}" + (f" (rel: {p['rel']})" if p["rel"] else "")]
        if p["card"]:
            lines.append("\n### Карточка проекта\n" + p["card"])
        if p["files"]:
            lines.append(
                "\n### Состав файлов (имя, размер, категория)\n"
                + "\n".join(
                    f"- {f['name']} ({f['size']} б, {f['category']})" for f in p["files"]
                )
            )
        else:
            lines.append("\n### Состав файлов\n- (файлов нет)")
        if p["subfolders"]:
            lines.append("\n### Подпапки\n" + "\n".join(f"- {s}" for s in p["subfolders"]))
        if p["images"]:
            lines.append(f"\nИзображений: {p['images']}")
        if include_text and p["texts"]:
            lines.append("\n### Содержимое текстовых файлов")
            for t in p["texts"]:
                lines.append(f"\n#### {t['name']}\n{t['text']}")
        elif not include_text:
            lines.append("\n(содержимое текстовых файлов не запрашивалось)")
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


def build_review_markdown(reply: str, model: str = "", now=None) -> str:
    """Final ``_обзор.md``: frontmatter + heading + the model's answer."""

    now = now or datetime.now()
    lines = [
        "---",
        "type: обзор",
        f"generated: {now.strftime('%Y-%m-%d %H:%M')}",
        f"model: {model}",
        "---",
        "",
    ]
    if reply.strip().startswith("#"):
        lines.append(reply.strip())
    else:
        lines.append("Обзор папки\n\n" + reply.strip())
    return "\n".join(lines).rstrip() + "\n"


def save_review(folder: FolderScan, text: str) -> Path:
    """Atomic write of the review next to the project card. Returns the path."""

    target = review_file_for(folder)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target
