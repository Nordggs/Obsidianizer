"""Folder Obsidianizer — read-only directory scanning into Obsidian cards.

The source tree is NEVER modified: the scanner only reads names, sizes, dates
and extensions (os.scandir / os.stat), and the only writes are ``.md`` catalog
cards, written atomically. A card is created for every folder (card name equals
the folder name); sub-folder cards link to their parent via ``[[../Parent|↑
Родитель]]``. Links are relative to the card, so the whole scanned tree stays
portable between vaults and machines.

Cards are recognized by the ``obsidianizer: true`` frontmatter marker. An
existing note with the same name that lacks the marker is treated as foreign
and is never overwritten unless ``force=True``. User data is preserved:
ALL user frontmatter keys (standard, unknown, multi-line lists) are carried
over untouched, per-file comments survive regeneration, and the working
notes live in the separate ``*_заметки.md`` file (created once, never
replaced, embedded into the card). ``*_заметки.md`` / ``*_обзор.md`` are
derived obsidianizer artifacts — never scanned as project files, though the
review file's presence participates in the card fingerprint. ``obsidianizer_hash``
in the frontmatter tracks the folder fingerprint, so an unchanged card is
not rewritten on the next run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import yaml

logger = logging.getLogger("obsidianizer.obsidianize")


def _get_now() -> datetime:
    """Return current datetime. Can be patched in tests for reproducibility."""
    return datetime.now()
from pathlib import Path

MANUAL_HEADER = "## ✍️ Ручные заметки и дополнения"
NOTES_SUFFIX = "_заметки.md"
REVIEW_SUFFIX = "_обзор.md"
DERIVED_SUFFIXES = (NOTES_SUFFIX, REVIEW_SUFFIX)
CARD_MARKER_KEY = "obsidianizer"
HASH_KEY = "obsidianizer_hash"
TEMPLATE_KEY = "obsidianizer_template"
VERSION_KEY = "obsidianizer_version"
RENDER_VERSION = 10  # bump to auto-migrate v9 cards (pre-Comments /
# Description (Auto) structure) to the current 7-column Files table

YAML_KEYS = ["дата_начала", "источник", "контакт", "проект", "адрес", "tags", "комментарий", "file_comments", "folder_comments"]
FIELD_LABELS = {
    "проект": "Проект",
    "адрес": "Адрес",
    "контакт": "Контакт",
    "дата_начала": "Дата начала",
    "источник": "Источник",
    "комментарий": "Комментарий",
}
# Legacy user-field names (pre-v0.6.2) — remapped on read so existing vaults
# keep working without a manual migration. Writing always uses the new keys.
OLD_KEY_MAP = {"клиент": "проект", "дизайнер": "контакт"}


def _map_old_keys(props: dict) -> dict:
    """Remap legacy frontmatter keys onto the current names.

    Original (current-name) keys always win over remapped legacy values, so a
    notes file that carries both spellings keeps its up-to-date data.
    """

    if not props:
        return props
    out = {OLD_KEY_MAP.get(k, k): v for k, v in props.items()}
    for k, v in props.items():
        if k not in OLD_KEY_MAP:
            out[k] = v
    return out

CATEGORY_ORDER = ("drafting", "tables", "docs", "images", "other")

GITHUB_CATEGORY_TITLES = {
    "drafting": "📐 Чертежи",
    "tables": "📊 Таблицы",
    "docs": "📄 Документы",
    "images": "🖼️ Изображения",
    "other": "📦 Прочие файлы",
}
FILE_ICONS = {"drafting": "📐", "tables": "📊", "docs": "📄", "images": "🖼️", "other": "📦"}
OPENERS = {
    # Файлы, которые открывает сам Obsidian
    "md": "Obsidian",
    "canvas": "Obsidian",
    "pdf": "Obsidian",
    "png": "Obsidian",
    "jpg": "Obsidian",
    "jpeg": "Obsidian",
    "gif": "Obsidian",
    "webp": "Obsidian",
    "svg": "Obsidian",
    "bmp": "Obsidian",
    # Прочее — внешние программы
    "dwg": "AutoCAD",
    "dxf": "AutoCAD",
    "xls": "Excel",
    "xlsx": "Excel",
    "csv": "Excel",
    "doc": "Word",
    "docx": "Word",
    "rvt": "Revit",
    "rfa": "Revit",
    "skp": "SketchUp",
    "step": "CAD",
    "stp": "CAD",
    "stl": "CAD",
    "iges": "CAD",
    "igs": "CAD",
}
CATEGORY_SHORT = {
    "drafting": "Чертежи",
    "tables": "Таблицы",
    "docs": "Документы",
    "images": "Изображения",
    "other": "Прочие",
}
CATEGORY_PLURALS = {
    "drafting": ("чертёж", "чертежа", "чертежей"),
    "tables": ("таблица", "таблицы", "таблиц"),
    "docs": ("документ", "документа", "документов"),
    "images": ("изображение", "изображения", "изображений"),
    "other": ("файл", "файла", "файлов"),
}
EMPTY_PLACEHOLDERS = {
    "drafting": "*Чертежей нет*",
    "tables": "*Таблиц нет*",
    "docs": "*Документов нет*",
    "images": "*Изображений нет*",
    "other": "*Прочих файлов нет*",
}

_UPDATED_RE = re.compile(
    r"\n?_Обновлено: \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?: · [^\n]*)?_\n?"
)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = int(n)
    if 11 <= n % 100 <= 14:
        return many
    r = n % 10
    if r == 1:
        return one
    if 2 <= r <= 4:
        return few
    return many


def format_size(n: int) -> str:
    """1024-based human size: B / KB / MB / GB / TB (one decimal)."""

    size = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024


def format_rel_date(mtime_ns: int, now: datetime | None = None) -> str:
    """GitHub-style relative date: today / yesterday / N days ago / DD.MM.YYYY."""

    now = now or _get_now()
    dt = datetime.fromtimestamp(mtime_ns / 1_000_000_000)
    days = (now.date() - dt.date()).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 30:
        return f"{days} {_plural(days, 'день', 'дня', 'дней')} назад"
    return dt.strftime("%d.%m.%Y")


@dataclass(frozen=True)
class FileEntry:
    """One file inside a scanned folder (metadata only, nothing is read)."""

    name: str
    rel: str  # posix path relative to the scan root
    ext: str  # lowercase extension without the dot ("" when absent)
    size: int
    mtime_ns: int


@dataclass
class FolderScan:
    """Read-only snapshot of a single folder."""

    path: Path  # absolute path
    rel: str  # posix path relative to the scan root ("" for the root itself)
    files: list[FileEntry] = field(default_factory=list)
    subfolders: list[str] = field(default_factory=list)
    images: list[FileEntry] = field(default_factory=list)


@dataclass
class ObsidianizeConfig:
    """Behaviour knobs. Categories are extensible dicts of extensions."""

    skip_hidden: bool = True
    exclude: list[str] = field(
        default_factory=lambda: [".obsidian", ".git", "node_modules", "__pycache__"]
    )
    include_md: bool = True
    img_gallery: bool = True
    vault_root: str = ""  # preferred: vault-relative gallery paths
    gallery_prefix: str = ""  # fallback: "PROJECT/OBSIDIAN/Objects" style prefix
    force: bool = False  # overwrite a foreign note that lacks the marker
    adopt: bool = False  # rename a foreign note into <name>_заметки.md (1:1)
    rebuild: bool = False  # regenerate OWN cards even when up to date
    # (structure migration — TICKET-003); foreign notes still conflict,
    # never overwritten (unlike force)
    rel_root: str = ""  # vault-relative path of the scanned root (for "⬆ Вверх")
    template: str = "github"  # github (Project Dashboard) | classic
    categories: dict = field(
        default_factory=lambda: {
            "drafting": ["dwg", "dxf"],
            "tables": ["xls", "xlsx", "csv"],
            "docs": ["pdf", "docx", "doc", "txt", "rtf", "odt"],
            "images": ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"],
        }
    )


@dataclass
class UpdateSummary:
    """Result of one update_cards run."""

    scanned: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    conflicts: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Scanning (read-only)
# --------------------------------------------------------------------------


def scan_tree(root: Path, cfg: ObsidianizeConfig | None = None) -> dict[str, FolderScan]:
    """Recursively scan ``root``. Returns {rel_posix: FolderScan}.

    Classification rule: ``*_заметки.md`` and ``*_обзор.md`` are derived
    obsidianizer artifacts — never user project files, excluded here at the
    classification level (no matter what ``include_md`` says), so they can
    never leak into stats or tables.
    """

    cfg = cfg or ObsidianizeConfig()
    if not root.is_dir():
        raise NotADirectoryError(f"Не папка: {root}")
    out: dict[str, FolderScan] = {}
    _walk_folder(root, root, "", cfg, out)
    return out


def _walk_folder(
    root: Path,
    abs_dir: Path,
    rel: str,
    cfg: ObsidianizeConfig,
    out: dict[str, FolderScan],
) -> None:
    fs = FolderScan(path=abs_dir, rel=rel)
    own_card = abs_dir.name + ".md"
    try:
        entries = sorted(os.scandir(abs_dir), key=lambda e: e.name.casefold())
    except OSError:
        return
    for entry in entries:
        name = entry.name
        if cfg.skip_hidden and name.startswith("."):
            continue
        if name in cfg.exclude:
            continue
        if name == own_card:
            continue  # the card itself is not a catalogued file
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
            if is_dir:
                fs.subfolders.append(name)
            elif is_file:
                fe = _to_entry(name, rel, entry)
                if fe is None:
                    continue
                if fe.ext == "md" and (
                    not cfg.include_md or fe.name.endswith(DERIVED_SUFFIXES)
                ):
                    # derived artifacts (*_заметки.md, *_обзор.md) are never
                    # user project files — excluded at the classification level
                    continue
                fs.files.append(fe)
                if fe.ext in cfg.categories.get("images", []):
                    fs.images.append(fe)
        except OSError:
            continue
    fs.subfolders.sort(key=str.casefold)
    fs.files.sort(key=lambda f: f.name.casefold())
    out[rel] = fs
    for sub in fs.subfolders:
        _walk_folder(root, abs_dir / sub, _join_rel(rel, sub), cfg, out)


def _to_entry(name: str, rel_dir: str, entry: os.DirEntry) -> FileEntry | None:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return FileEntry(
        name=name,
        rel=_join_rel(rel_dir, name),
        ext=ext,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
    )


def _join_rel(rel_dir: str, name: str) -> str:
    return f"{rel_dir}/{name}" if rel_dir else name


def discover_dir_rels(
    root: Path,
    excl=None,
    user_dirs: list[str] | set[str] | tuple | None = None,
) -> list[dict]:
    """Lightweight directory discovery for tab-1 selection (TICKET-013).

    ``os.scandir`` over directory NAMES only — never lists/stats files
    inside excluded or hidden directories (zero content reads, so
    ``node_modules/.git`` cost nothing and the GUI never gets a giant DOM).
    An excluded/hidden directory is emitted as a LEAF (its subtree is
    covered by the rule, ticket §12) and never descended into.

    ``excl`` is a ready ``ExclusionFilter`` built on the union
    ``Settings ∪ DEFAULT_EXCLUDE_DIRS`` (built once by the caller —
    no ``Settings`` reads here). ``user_dirs`` feeds the
    ``system > user > hidden-only`` kind classifier.
    Returns ``[{rel, kind}]`` with ``kind`` in
    ``system|user|hidden-only``. ``FolderScan`` is never built here.
    """

    from .exclusions import DEFAULT_EXCLUDE_DIRS

    user_set = set(user_dirs or [])

    def _kind(name: str) -> str:
        if name in DEFAULT_EXCLUDE_DIRS:
            return "system"
        if name in user_set:
            return "user"
        return "hidden-only"

    out: list[dict] = []

    def _excluded(rel: str) -> bool:
        if excl is not None:
            try:
                if excl.is_excluded_dir(rel):
                    return True
            except Exception:  # noqa: BLE001 — discovery never crashes the scan
                pass
        return False

    def _walk(abs_dir: Path, rel: str) -> None:
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: e.name.casefold())
        except OSError:
            return
        for entry in entries:
            name = entry.name
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            child = _join_rel(rel, name)
            hidden = name.startswith(".")
            if _excluded(child) or hidden:
                out.append({"rel": child, "kind": _kind(name)})
                continue  # leaf: never descend into excluded/hidden
            _walk(abs_dir / name, child)

    _walk(root, "")
    return out


def classify_dir_isolated(
    scan_root: Path, rel: str, cfg: ObsidianizeConfig | None = None
) -> FolderScan | None:
    """Build a SINGLE FolderScan node outside the strict tree (TICKET-013).

    Strictly one node — never recurses into the subtree (an override of
    ``node_modules/.git`` must not explode). Same basis as ``_card_rel_key``
    consumes (``path=scan_root/rel``, ``rel=rel`` — never ``scan_tree``
    on the subdir, which would shift the basis to ``rel=""``). Same
    classification body as ``_walk_folder`` with a single permissive
    config (``skip_hidden=False, exclude=[]``, same categories).

    Used in exactly two places: the worker for selected group-(a) rels
    (outside the strict tree) and CLI ``--select`` resolution. Returns
    ``None`` when the path is unsafe or not a directory.
    """

    cfg = cfg or ObsidianizeConfig()
    norm = (rel or "").replace("\\", "/").strip("/")
    if not norm or norm in ("", ".") or norm.startswith(("/", "..")):
        return None
    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    abs_dir = scan_root / Path(*parts)
    try:
        if not abs_dir.is_dir():
            return None
    except OSError:
        return None
    fs = FolderScan(path=abs_dir, rel=norm)
    own_card = abs_dir.name + ".md"
    try:
        entries = sorted(os.scandir(abs_dir), key=lambda e: e.name.casefold())
    except OSError:
        return fs
    for entry in entries:
        name = entry.name
        if name == own_card:
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
            if is_dir:
                fs.subfolders.append(name)
            elif is_file:
                fe = _to_entry(name, norm, entry)
                if fe is None:
                    continue
                if fe.ext == "md" and (
                    not cfg.include_md or fe.name.endswith(DERIVED_SUFFIXES)
                ):
                    continue
                fs.files.append(fe)
                if fe.ext in cfg.categories.get("images", []):
                    fs.images.append(fe)
        except OSError:
            continue
    fs.subfolders.sort(key=str.casefold)
    fs.files.sort(key=lambda f: f.name.casefold())
    return fs


# --------------------------------------------------------------------------
# Card recognition and user-data extraction
# --------------------------------------------------------------------------


def card_name(folder_name: str) -> str:
    return folder_name + ".md"


def card_path_for(folder: FolderScan) -> Path:
    return folder.path / (folder.path.name + ".md")


def review_file_path(folder: FolderScan) -> Path:
    """The ``<folder name>_обзор.md`` AI-review file (the semantic layer on
    top of the dashboard). Its existence is part of the fingerprint, so the
    card's AI-review section stays accurate across review create/delete."""

    return folder.path / (folder.path.name + REVIEW_SUFFIX)


def notes_file_path(folder: FolderScan) -> Path:
    """The ``<folder name>_заметки.md`` free-form working-notes file. It is a
    derived artifact of the obsidianizer: never scanned into the project
    stats and never replaced once it exists; the card embeds it via
    ``![[<name>_заметки]]``."""

    return folder.path / (folder.path.name + NOTES_SUFFIX)


def card_is_ours(content: str) -> bool:
    """True when the note carries the ``obsidianizer: true`` marker."""

    return parse_frontmatter(content).get(CARD_MARKER_KEY) is True


def _normalize_fm_types(props: dict) -> dict:
    """Normalize types after yaml.safe_load() to match custom parser behavior.

    PyYAML YAML 1.1 may return datetime.date for date strings — convert to str.
    Bool values (true/false) are kept as-is since they match the old parser.
    """
    date_key = "дата_начала"
    if date_key in props and hasattr(props[date_key], "isoformat"):
        props[date_key] = props[date_key].isoformat()
    return props


def parse_frontmatter(content: str) -> dict:
    """Parse the leading YAML block into typed values (order preserved).

    Uses PyYAML safe_load for full YAML compatibility, with type normalization
    to preserve backward compatibility with the custom parser behavior.
    Falls back to a tolerant line-by-line parser when PyYAML rejects the block,
    so user data is never silently lost on edge-case YAML.
    """

    m = re.match(r"^---\n([\s\S]*?)\n---", content)
    if not m:
        return {}
    block = m.group(1)
    try:
        props = yaml.safe_load(block)
        if not isinstance(props, dict):
            return {}
        # PyYAML may yield non-string keys (int/bool/None) for unquoted keys
        # like ``50: ...``. Downstream code assumes ``str`` keys
        # (``key.startswith(...)``), so normalize once at the single entry
        # point instead of guarding every consumer.
        props = {
            (k if isinstance(k, str) else ("" if k is None else str(k))): v
            for k, v in props.items()
        }
        return _normalize_fm_types(props)
    except yaml.YAMLError:
        return _parse_frontmatter_legacy(block)


def parse_frontmatter_strict(content: str) -> tuple[dict, str]:
    """Strict frontmatter inspection for the TICKET-015 safe-refusal path.

    Returns ``(props, status)`` with ``status`` in
    ``{"absent", "valid", "corrupt"}``:

    - ``absent`` — no ``---`` block as the first significant line (BOM and
      leading blank lines skipped, CRLF tolerated): ``props`` is ``{}``.
      A missing block is the normal case (e.g. a fresh notes file), NOT
      a refusal reason by itself.
    - ``valid`` — block found, closed, and ``yaml.safe_load`` succeeded
      without the legacy fallback (empty block or ``None`` result counts
      as ``valid`` with ``props={}``).
    - ``corrupt`` — block opened but not closed, ``safe_load`` failed
      (INCLUDING cases the tolerant legacy parser could partially read —
      fallback success must never count as trust), or the result is not
      a dict.  Callers must refuse to overwrite the file before any write.

    The legacy-tolerant :func:`parse_frontmatter` is intentionally NOT used
    here: it masks damage as ``{}``/partial data, which would license a
    rewrite of the whole user frontmatter (A3).  Bodies starting with
    ``---`` (horizontal rule / code fence) with no FM intent may report
    ``corrupt`` — acceptable for cards/notes where FM, when present, is
    always first.

    Known limitation (F4): ``yaml.safe_load`` (PyYAML 1.1) is stricter
    than Obsidian's frontmatter parser, so a note that Obsidian opens
    fine may report ``corrupt`` here and land in ``summary.failed`` until
    its FM is made strict-YAML-valid.  This fail-safe direction (a
    never-updated card instead of a silently rewritten FM) is deliberate
    for a P0 data-integrity ticket.
    """

    text = content.lstrip("\ufeff")
    # Universal line split keeps CRLF/BOM handling explicit below.
    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return {}, "absent"
    first = lines[idx].strip()
    if first != "---" and not first.startswith("--- "):
        return {}, "absent"
    # FM block opened at idx — find the closing delimiter.
    end = None
    for j in range(idx + 1, len(lines)):
        if lines[j].strip() in ("---", "..."):
            end = j
            break
    if end is None:
        return {}, "corrupt"
    block = "\n".join(lines[idx + 1 : end])
    if not block.strip():
        return {}, "valid"
    try:
        props = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}, "corrupt"
    if props is None:
        return {}, "valid"
    if not isinstance(props, dict):
        return {}, "corrupt"
    props = {
        (k if isinstance(k, str) else ("" if k is None else str(k))): v
        for k, v in props.items()
    }
    return _normalize_fm_types(props), "valid"


def _parse_frontmatter_legacy(block: str) -> dict:
    """Tolerant line-by-line YAML parser — handles values PyYAML rejects.

    Parses ``key: value`` lines, block-list items (``- item``), and quoted
    scalars.  Never raises — returns what it managed to extract.
    """
    props: dict = {}
    cur_key: str | None = None
    for line in block.split("\n"):
        idx = line.find(":")
        if idx == -1:
            item = line.strip()
            if item.startswith("-") and cur_key is not None:
                val = item[1:].strip()
                if val:
                    cur = props.get(cur_key)
                    if not isinstance(cur, list):
                        cur = [] if cur is None else [cur]
                    cur.append(_parse_scalar(val))
                    props[cur_key] = cur
            continue
        key = line[:idx].strip()
        if not key:
            continue
        cur_key = key
        props[key] = _parse_scalar(line[idx + 1:].strip())
    return props


def _parse_scalar(raw: str):
    if not raw:
        return None
    if len(raw) >= 2 and (
        (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'"))
    ):
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return [s.strip().strip("\"'") for s in inner.split(",") if s.strip()]
    if raw in ("null", "~"):
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def _split_table_cells(line: str) -> list[str]:
    r"""Split a markdown table row into cells, keeping wikilinks whole.

    Both escaped (``\|``) and raw alias pipes inside ``[[…|…]]`` are masked
    before splitting, so the cell boundary never cuts a link in two.
    """

    masked = re.sub(
        r"\[\[[^\]\n]*\]\]",
        lambda m: m.group(0).replace("|", "\x00"),
        line,
    )
    return [c.strip() for c in masked.strip().strip("|").replace("\\|", "\x00").split("|")]


def _escape_pipe(text: str) -> str:
    """Escape | for markdown table cells."""
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _truncate_comment(text: str, max_len: int = 80) -> str:
    """Truncate long comments with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def extract_comments(content: str) -> dict[str, str]:
    """Collect per-file auto-comments from the Comment column of the Files table.

    Handles: new English header, old Russian header, and headerless script rows.
    Key = display name from wikilink. Comment = auto-comment column only.
    """

    comments: dict[str, str] = {}
    in_files = False
    format_version = None  # "new" = English 7-col, "old_compat" = Russian last-col
    for line in content.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            in_files = False
            format_version = None
            continue
        # Detect new English Files table header
        if "File" in stripped and "Opens with" in stripped:
            in_files = True
            format_version = "new"
            continue
        # Detect old Russian table headers
        if "Файл" in stripped and ("Комментарий" in stripped or "Открывается" in stripped):
            in_files = True
            format_version = "old_compat"
            continue
        if in_files and "---" in stripped:
            continue
        m = re.search(r"\[\[([^\]|]*?)(?:\|([^\]|]*))?\]\]", line)
        if not m:
            continue
        name = (m.group(2) or m.group(1)).strip()
        cells = _split_table_cells(line)
        if not cells:
            continue
        if format_version == "new":
            if len(cells) >= 6:
                comment = cells[5].strip()
                if comment:
                    comments[name] = comment
        elif format_version == "old_compat":
            # Old format: comment is in the cell right after the wikilink
            idx = next((i for i, c in enumerate(cells) if "[[" in c), None)
            if idx is not None and idx + 1 < len(cells):
                comment = cells[idx + 1].strip()
                if comment:
                    comments[name] = comment
        else:
            # Headerless row (script/legacy) — comment is last cell
            comment = cells[-1].strip()
            if comment:
                comments[name] = comment
    return comments


def extract_user_comments(
    content: str,
    folder_files: list | None = None,
) -> dict[str, str]:
    """Extract user-editable Comments column (last column) from the Files table.

    Keys are canonical card-basis paths (see :func:`_card_rel_key`), so a
    full-tree scan and a Templater single-card scan (``rel=""`` basis) share
    the same key for the same file.  ``folder_files`` (``folder.files``)
    resolves a bare ``[[name]]`` link target — and legacy ``[[rel|name]]``
    targets — to that canonical key; without it the raw link target is kept
    (backward compatible with existing callers/tests).
    Unescapes ``\\|`` → ``|`` so stored values are the original user text.
    Table parsing never raises: undecodable rows are skipped, never a
    refusal reason (B2 — ambiguity excluded by unique names per folder).
    """

    # Canonical card-basis key for a direct file is its file name
    # (``_card_rel_key(folder, f.rel) == f.name`` by construction), stable
    # across scan bases (full tree vs Templater ``rel=""``).
    name_to_canon: dict[str, str] = {}
    rel_to_canon: dict[str, str] = {}
    if folder_files:
        for f in folder_files:
            rel = getattr(f, "rel", None)
            name = getattr(f, "name", None)
            if name is None:
                continue
            name_to_canon[str(name)] = str(name)
            if rel is not None:
                rel_to_canon[str(rel)] = str(name)
    comments: dict[str, str] = {}
    in_files = False
    for line in content.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            in_files = False
            continue
        if "File" in stripped and "Opens with" in stripped:
            in_files = True
            continue
        if in_files and "---" in stripped:
            continue
        if in_files:
            m = re.search(r"\[\[([^\]|]*?)(?:\\?\|([^\]|]*))?\]\]", line)
            if not m:
                continue
            link_target = m.group(1).strip()
            display = (m.group(2) or m.group(1)).strip()
            if link_target.startswith(".."):
                continue
            cells = _split_table_cells(line)
            if len(cells) >= 7:
                comment = cells[-1].strip().replace("\x00", "|").replace("\\\\", "\\").replace("\\|", "|")
                key = link_target
                if folder_files:
                    if link_target in rel_to_canon:
                        key = rel_to_canon[link_target]
                    elif link_target in name_to_canon:
                        key = name_to_canon[link_target]
                    elif display in name_to_canon:
                        key = name_to_canon[display]
                comments[key] = comment
    return comments


def extract_folder_comments(
    content: str, name_to_rel: dict[str, str] | None = None
) -> dict[str, str]:
    """Extract user-editable Comments column from the Folders table.

    Key = canonical folder relative path (via ``name_to_rel`` mapping).
    """

    comments: dict[str, str] = {}
    in_folders = False
    for line in content.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            in_folders = False
            continue
        if "Name" in stripped and "Updated" in stripped and "Files" in stripped:
            in_folders = True
            continue
        if in_folders and "---" in stripped:
            continue
        if in_folders:
            m = re.search(r"\[\[([^\]|]*?)(?:\\?\|([^\]|]*))?\]\]", line)
            if not m:
                continue
            link_target = m.group(1).strip()
            if link_target.startswith(".."):
                continue
            display = (m.group(2) or m.group(1)).strip()
            canonical = name_to_rel.get(display, display) if name_to_rel else display
            cells = _split_table_cells(line)
            if len(cells) >= 5:
                comment = cells[-1].strip().replace("\x00", "|").replace("\\\\", "\\").replace("\\|", "|")
                comments[canonical] = comment
    return comments


def extract_manual_block(content: str) -> str | None:
    """Return the manual-notes block (header included), with every trailing
    ``_Обновлено: ..._`` footer stripped so it can never duplicate."""

    idx = content.find(MANUAL_HEADER)
    if idx == -1:
        return None
    block = content[idx:]
    while _UPDATED_RE.search(block):
        block = _UPDATED_RE.sub("", block)
    return block.rstrip()


SERVICE_KEYS = {
    CARD_MARKER_KEY,
    HASH_KEY,
    TEMPLATE_KEY,
    VERSION_KEY,
    "cssclasses",
    "position",
    "file",
}


def _user_props(props: dict) -> dict:
    """User-owned frontmatter fields (everything except service keys)."""

    props = _map_old_keys(props)
    out: dict = {}
    for key in YAML_KEYS:
        if key in props:
            out[key] = props[key]
    for key, val in props.items():
        if key in YAML_KEYS or key in SERVICE_KEYS or key.startswith("obsidianizer"):
            continue
        out[key] = val
    return out


def _has_user_data(props: dict) -> bool:
    """True when props hold real user data (not empty generator defaults)."""

    for key, val in props.items():
        if val is None or val == "":
            continue
        if key == "tags" and val == []:
            continue
        if key == "дата_начала" and str(val) == datetime.now().strftime("%Y-%m-%d"):
            continue
        return True
    return False


def _notes_frontmatter(props: dict) -> str:
    """YAML block for the notes file using PyYAML: standard keys first, then user extras."""

    ordered = {}
    for key in YAML_KEYS:
        ordered[key] = props.get(key, _default_value(key))
    for key, val in props.items():
        if key in YAML_KEYS or key in SERVICE_KEYS:
            continue
        ordered[key] = val
    yaml_block = yaml.safe_dump(
        ordered, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).rstrip("\n")
    return "---\n" + yaml_block + "\n---"


def _strip_frontmatter(content: str) -> str:
    m = re.match(r"^---\n[\s\S]*?\n---\n?", content)
    return content[m.end():] if m else content


def _adopt_foreign_note(card_path: Path, notes_path: Path) -> bool:
    """Rename a foreign ``<name>.md`` into the ``<name>_заметки.md`` slot.

    Content is preserved 1:1 (a plain atomic rename) — the old frontmatter
    then feeds the Project Card automatically. Never overwrites an existing
    notes file: returns False when one already exists or the rename fails.
    """

    if notes_path.exists() or not card_path.is_file():
        return False
    try:
        os.replace(card_path, notes_path)
        return True
    except OSError:
        return False


def _sync_comments_to_notes(
    notes_path: Path,
    prev: str | None,
    folder: FolderScan,
) -> bool:
    """Extract Comments from card table, merge with notes FM, write back.

    Called ALWAYS (even when card is skipped) to preserve user edits.
    Table values win over frontmatter (user edits the table).

    Canonical file keys are card-basis (``f.name`` — identical for a full
    scan and a Templater ``rel=""`` scan; equals ``_card_rel_key`` for
    direct files).  This intentionally deviates from PLAN v6 letter
    (``f.rel``), recorded as PLAN v7: a scan-basis canonical diverges
    between bases and re-breaks Templater interop.  Legacy scan-basis keys
    (``f.rel``) found in old frontmatter migrate to canonical; legacy keys
    with no matching file (B3) are preserved untouched.

    Clearing rule (C2 + v7 intention anchor): an empty cell clears FM only
    when the card manifest proves the card DISPLAYED exactly the FM value
    (``fc``/``fdc`` snapshot hash == live FM value hash) — i.e. the user
    saw the value and then emptied the cell.  Otherwise the empty cell is
    a render miss (including the fully fresh post-bug state, where the
    manifest notes-hash already matches) — FM is preserved and the rebuild
    restores the display.  Missing snapshot (old manifests) always means
    "not proven" → preserve.  Error direction is data-safe by
    construction.

    Returns True on success (including no-op); False means the notes FM is
    ``corrupt`` or unreadable — the caller must refuse the whole folder
    update (``failed``) WITHOUT writing card or notes.
    """
    if not notes_path.exists():
        return True
    try:
        notes_content = notes_path.read_text(encoding="utf-8")
    except OSError:
        return False
    _, notes_status = parse_frontmatter_strict(notes_content)
    if notes_status == "corrupt":
        return False
    notes_props = parse_frontmatter(notes_content)
    raw_fc = notes_props.get("file_comments") or {}
    raw_fdc = notes_props.get("folder_comments") or {}
    if not isinstance(raw_fc, dict):
        raw_fc = {}
    if not isinstance(raw_fdc, dict):
        raw_fdc = {}

    table_fc = extract_user_comments(prev, folder.files) if prev else {}
    name_to_rel = {s: _join_rel(folder.rel, s) for s in folder.subfolders}
    table_fdc = extract_folder_comments(prev, name_to_rel) if prev else {}

    # Intention anchor (v7): the manifest ``fc``/``fdc`` snapshot records
    # WHAT THE CARD DISPLAYED (per-key hashes).  An empty cell clears FM
    # only when the snapshot proves the card showed exactly the live FM
    # value (deliberate clear).  Any other empty (render miss — including
    # the fully fresh post-bug state — or an old manifest without snapshot)
    # preserves FM and lets the rebuild restore the display.
    try:
        _manifest = _parse_manifest(prev) if prev else None
    except Exception:
        _manifest = None
    _snap_fc = (_manifest or {}).get("fc") if isinstance(_manifest, dict) else None
    _snap_fdc = (_manifest or {}).get("fdc") if isinstance(_manifest, dict) else None
    if not isinstance(_snap_fc, dict):
        _snap_fc = {}
    if not isinstance(_snap_fdc, dict):
        _snap_fdc = {}

    def _displayed(key: str, live, snap: dict) -> bool:
        try:
            return bool(live) and snap.get(key) == _comment_hash(live)
        except Exception:
            return False

    # Empty table value = user cleared (only for files already in FM AND
    # only when the snapshot proves the card displayed that FM value).
    # For new files with no FM history, skip empty entries (no noise).
    # If table value is a truncated version of the FM value (ends with …),
    # keep the FM original — the table is a display, not the source of truth.
    merged_fc = {**raw_fc}
    known_files = {f.name for f in folder.files}
    rel_alias = {f.rel: f.name for f in folder.files if f.rel != f.name}
    for f in folder.files:
        canon = f.name
        legacy_keys = [k for k in (f.rel,) if k != canon and k in raw_fc]
        table_v = table_fc.get(canon)
        if table_v is None:
            # File absent from the table (should not happen) — never invent.
            continue
        if canon in raw_fc:
            # (a) canonical history exists.
            existing = raw_fc.get(canon)
            if table_v:
                if existing and table_v != existing and table_v == _truncate_comment(existing):
                    pass  # truncated display → preserve FM
                else:
                    merged_fc[canon] = table_v
                    for lk in legacy_keys:
                        merged_fc.pop(lk, None)  # D1: drop legacy twin
            else:
                if _displayed(canon, existing, _snap_fc):
                    merged_fc[canon] = table_v  # deliberate clear proven
                    for lk in legacy_keys:
                        merged_fc.pop(lk, None)
                # else render miss (even when fresh): preserve, rebuild restores
        elif legacy_keys:
            # (б)/(б′) canonical history missing, legacy present.
            legacy_val = raw_fc.get(legacy_keys[0])
            if table_v:
                # (б′) non-empty cell wins over legacy (D2).
                if legacy_val and table_v != legacy_val and table_v == _truncate_comment(legacy_val):
                    merged_fc[canon] = legacy_val
                else:
                    merged_fc[canon] = table_v
                for lk in legacy_keys:
                    merged_fc.pop(lk, None)
            else:
                if _displayed(canon, legacy_val, _snap_fc):
                    # Card showed the legacy value (via fallback) and the
                    # user cleared it → record the clearing, drop legacy.
                    merged_fc[canon] = ""
                    for lk in legacy_keys:
                        merged_fc.pop(lk, None)
                else:
                    # (б) render miss = recovery, NOT clearing (C2/F1).
                    if legacy_val:
                        merged_fc[canon] = legacy_val
                    for lk in legacy_keys:
                        merged_fc.pop(lk, None)
        else:
            if table_v:
                existing = raw_fc.get(canon)
                if existing and table_v != existing and table_v == _truncate_comment(existing):
                    continue
                merged_fc[canon] = table_v
    # Legacy keys with no matching file (B3) stay untouched via merged copy.
    # Stale scan-basis aliases of DELETED files are also preserved, never
    # silently dropped: only aliases of CURRENT files migrate above.
    for k, v in table_fc.items():
        if k in known_files or k in rel_alias:
            continue
        if v or k in raw_fc:
            existing = raw_fc.get(k)
            if existing and v != existing and v == _truncate_comment(existing):
                continue
            merged_fc[k] = v
    merged_fdc = {**raw_fdc}
    for k, v in table_fdc.items():
        if v or k in raw_fdc:
            if not v and k in raw_fdc and not _displayed(k, raw_fdc.get(k), _snap_fdc):
                continue  # unproven empty folder cell: preserve, never clear
            existing = raw_fdc.get(k)
            if existing and v != existing and v == _truncate_comment(existing):
                continue
            merged_fdc[k] = v

    if merged_fc != raw_fc or merged_fdc != raw_fdc:
        notes_props["file_comments"] = merged_fc
        notes_props["folder_comments"] = merged_fdc
        body = _strip_frontmatter(notes_content).lstrip("\n")
        try:
            write_atomic(notes_path, _notes_frontmatter(notes_props) + "\n\n" + body)
        except OSError:
            return False
    return True


def _ensure_notes_file(folder: FolderScan, prev: str | None) -> bool:
    """Create/fill ``<name>_заметки.md`` — the single user-owned layer.

    - File missing → created with the user frontmatter template. User fields
      from the old card migrate into it, and the old in-card manual block
      (v1/v2 era) becomes the body.
    - File exists WITHOUT user data, but the old card carries user fields →
      only the frontmatter block is added/replaced; the body is preserved.
    - In every other case the file is NEVER touched.
    Returns True when the file was written.
    """

    notes = notes_file_path(folder)
    card_props = parse_frontmatter(prev) if prev else {}
    user_props = _user_props(card_props)

    if not notes.exists():
        body_src = extract_manual_block(prev) if prev else None
        if body_src is not None:
            body = body_src.rstrip() + "\n"
        else:
            body = (
                "# Рабочие заметки\n\n"
                "*Всё, что вы напишете ниже и в полях выше, "
                "сохранится при обновлении карточки.*\n"
            )
        write_atomic(notes, _notes_frontmatter(user_props) + "\n\n" + body)
        return True

    # Migration path: fill frontmatter from the old card while it holds no data.
    if not _has_user_data(user_props):
        return False
    try:
        content = notes.read_text(encoding="utf-8")
    except OSError:
        return False
    if _has_user_data(parse_frontmatter(content)):
        return False  # the user already keeps data here — never overwrite
    body = _strip_frontmatter(content).lstrip("\n")
    write_atomic(notes, _notes_frontmatter(user_props) + "\n\n" + body)
    return True


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def build_card(
    folder: FolderScan,
    prev: str | None,
    cfg: ObsidianizeConfig,
    parent_rel: str | None = None,
    stats: dict | None = None,
    notes_prev: str | None = None,
    content_hashes: dict[str, str] | None = None,
    file_comments: dict[str, str] | None = None,
    folder_comments: dict[str, str] | None = None,
) -> str:
    """Compose the full card text for one folder (Project Dashboard v5).

    The card carries ONLY service frontmatter; ALL user fields live in the
    separate ``*_заметки.md`` file (embedded). ``prev`` is the current card
    content; ``notes_prev`` is the current notes-file content whose
    frontmatter feeds the About section and the repo subtitle.
    ``stats`` is the optional ``folder_stats`` entry for this folder.
    ``content_hashes`` are precomputed content hashes (see
    :func:`_compute_content_hashes`); computed on demand when omitted.
    ``file_comments`` / ``folder_comments`` override values from notes_prev
    when provided (used by tests and direct callers).
    """

    return _render_dashboard(
        folder, prev, cfg, parent_rel, stats, notes_prev, content_hashes,
        file_comments=file_comments, folder_comments=folder_comments,
    )


def _local_stats(folder: FolderScan, cfg: ObsidianizeConfig) -> dict:
    """Fallback aggregates for a single folder (no tree available)."""

    return folder_stats({folder.rel: folder}, cfg)[folder.rel]


def _render_dashboard(
    folder: FolderScan,
    prev: str | None,
    cfg: ObsidianizeConfig,
    parent_rel: str | None = None,
    stats: dict | None = None,
    notes_prev: str | None = None,
    content_hashes: dict[str, str] | None = None,
    file_comments: dict[str, str] | None = None,
    folder_comments: dict[str, str] | None = None,
) -> str:
    """Project Dashboard v5 — GitHub project page structure.

    Repo header (plain markdown, no HTML) → Code (physical folders only) →
    README (About + Files with category tables) → Images gallery →
    AI Review → Notes → Footer. All stats use DIRECT counts for the current
    folder. The card carries ONLY service frontmatter; every user field
    lives in the ``*_заметки.md`` file (``notes_prev`` feeds About/subtitle).
    """

    existing = parse_frontmatter(prev) if prev else {}
    comments = extract_comments(prev) if prev else {}

    # User data source of truth = the notes file; fall back to the old card
    # frontmatter only when notes content was not provided.
    user_source = _map_old_keys(parse_frontmatter(notes_prev)) if notes_prev else {}
    if not user_source:
        user_source = _map_old_keys(
            {k: v for k, v in existing.items() if not k.startswith("obsidianizer")}
        )

    raw_fc = file_comments if file_comments is not None else (user_source.get("file_comments") or {})
    raw_fdc = folder_comments if folder_comments is not None else (user_source.get("folder_comments") or {})
    if not isinstance(raw_fc, dict):
        raw_fc = {}
    if not isinstance(raw_fdc, dict):
        raw_fdc = {}

    digest = folder_fingerprint(folder, content_hashes)
    st = stats or _local_stats(folder, cfg)

    # ── DIRECT counts for the current folder (single source of truth) ──
    direct_counts = {cat: 0 for cat in CATEGORY_ORDER}
    direct_sizes = {cat: 0 for cat in CATEGORY_ORDER}
    direct_mtimes = {cat: None for cat in CATEGORY_ORDER}
    for f in folder.files:
        cat = _category_of(f.ext, cfg)
        direct_counts[cat] += 1
        direct_sizes[cat] += f.size
        direct_mtimes[cat] = max(direct_mtimes[cat] or 0, f.mtime_ns)
    direct_total = len(folder.files)
    direct_size = sum(f.size for f in folder.files)
    direct_subfolders = len(folder.subfolders)

    prev_cc = existing.get("cssclasses")
    cssclasses = prev_cc if isinstance(prev_cc, list) else ["github-dashboard"]
    parts = [format_frontmatter(digest, template=cfg.template, cssclasses=cssclasses)]

    # ── Repository header (plain markdown — no HTML blocks here, they break
    #    wikilink parsing and the following tables in Obsidian) ──
    parts.append(f"\n# {folder.path.name}")
    parts.append(f"\nАвтоматическая карточка каталога")
    meta = (
        f"\nLocal project · {direct_total} "
        f"{_plural(direct_total, 'файл', 'файла', 'файлов')} "
        f"· {direct_subfolders} {_plural(direct_subfolders, 'папка', 'папки', 'папок')} "
        f"· {format_size(direct_size)}  "
        f"\nUpdated {_get_now().strftime('%d %b %Y').replace('Aug', 'авг').replace('Sep', 'сен').replace('Oct', 'окт').replace('Nov', 'ноя').replace('Dec', 'дек')}"
    )
    parts.append(meta)

    has_direct_imgs = bool(folder.images)
    gallery_path = _gallery_rel_path(folder, cfg) if has_direct_imgs else None
    has_gallery = has_direct_imgs and cfg.img_gallery and gallery_path is not None

    tree_items = st.get("images_tree") or []
    prefix = (folder.rel + "/") if folder.rel else ""
    rel_items: list[tuple[str, int]] = []
    for rel_p, size in tree_items:
        r = rel_p[len(prefix):] if prefix and rel_p.startswith(prefix) else rel_p
        rel_items.append((r, size))

    nav_tabs = ["[[#Folders|Folders]]", "[[#Files|Files]]", "[[#About|About]]"]
    if has_gallery:
        nav_tabs.append("[[#Gallery|Gallery]]")
    if rel_items:
        nav_tabs.append("[[#Images|Images]]")
    if review_file_path(folder).is_file():
        nav_tabs.append("[[#AI Review|AI Review]]")
    nav_tabs.append("[[#Notes|Notes]]")
    parts.append("\n" + " | ".join(nav_tabs))

    # ── Folders: physical folders only (no categories here) ──
    parts.append("\n## Folders")
    parts.append("\n| Name | Files | Size | Updated | Comments |")
    parts.append("| --- | --- | --- | --- | --- |")
    if parent_rel is not None:
        if parent_rel:
            parent_name = parent_rel.rsplit("/", 1)[-1]
        else:
            # Parent is the scanned root; its card lives at
            # <root>/<root_name>.md (cards are named after their folder).
            # A bare ".." would point at the parent FOLDER, which Obsidian
            # cannot open as a note ("file already exists" on click).
            parent_name = folder.path.parent.name
        link = f"../{parent_name}"
        parts.append(f"| ⬆ [[{link}\\|Up]] |  |  |  |  |")
    subs = st.get("subfolders") or {}
    if folder.subfolders:
        for sub in folder.subfolders:
            folder_rel = _join_rel(folder.rel, sub)
            fc = _truncate_comment(_escape_pipe(raw_fdc.get(folder_rel, "")))
            ss = subs.get(sub)
            if ss is None or not ss["count"]:
                parts.append(f"| 📁 [[./{sub}/{sub}\\|{sub}]] | 0 | 0 B | | {fc} |")
                continue
            changed = format_rel_date(ss["max_mtime_ns"]) if ss["max_mtime_ns"] else ""
            size_str = format_size(ss["size"])
            parts.append(
                f"| 📁 [[./{sub}/{sub}\\|{sub}]] | {ss['count']} | {size_str} | {changed} | {fc} |"
            )
    else:
        parts.append("| *No folders* | | | | |")

    # ── Files: single table of all direct files (GitHub file list) ──
    parts.append("\n## Files")
    if folder.files:
        parts.append(_files_table(folder.files, comments, raw_fc, cfg))
    else:
        parts.append("*Файлов нет*")

    # ── About: Project Card (from notes frontmatter) ──
    about: list[tuple[str, str]] = []
    for key in ("проект", "адрес", "источник", "дата_начала", "контакт", "комментарий"):
        if key == "дата_начала":
            raw = user_source.get(key)
            if not raw or str(raw) == _get_now().strftime("%Y-%m-%d"):
                continue
            val = str(raw)
        else:
            val = _display_value(user_source.get(key))
        if val:
            about.append((FIELD_LABELS[key], val))
    for key, val in user_source.items():
        if key in YAML_KEYS or key in SERVICE_KEYS:
            continue
        val = _display_value(val)
        if val:
            about.append((key.capitalize(), val))
    if about:
        parts.append("\n## About")
        # Static project card (old Templater behaviour): a plain Obsidian
        # callout projected from the notes frontmatter — stays readable in
        # PDF export and non-Obsidian viewers, unlike the YAML block itself.
        parts.append("> [!info] 📋 Карточка проекта")
        parts.extend(f"> - **{label}**: {val}" for label, val in about)

# ── Gallery: interactive Obsidian gallery (direct images only) ──
    if has_gallery:
        parts.append("\n## Gallery")
        parts.append(
            "```img-gallery\n"
            f"path: {gallery_path}\n"
            "type: vertical\n"
            "columns: 4\n"
            "gutter: 12\n"
            "radius: 4\n"
            "sortby: name\n"
            "sort: asc\n"
            "```"
        )

    # ── Images: archive of the current folder only, collapsed in Obsidian ──
    # Plain relative links (URL-encoded, no angle brackets) work in any
    # viewer; the collapsible callout gives Obsidian native previews.
    if folder.images:
        parts.append("\n## Images")
        total_size = sum(f.size for f in folder.images)
        parts.append(
            f"> [!example]- Images · {len(folder.images)} "
            f"{_plural(len(folder.images), 'изображение', 'изображения', 'изображений')}"
            f" · {format_size(sum(f.size for f in folder.images))}"
        )
        for f in folder.images:
            parts.append(f">\n> ![{f.name}](./{_img_href(f.name)})")

    # ── AI Review (embed) ──
    if review_file_path(folder).is_file():
        parts.append("\n## AI Review")
        parts.append(f"\n![[{folder.path.name}_обзор]]")

    # ── Working Notes (embed) ──
    parts.append("\n## Notes")
    parts.append(f"\n![[{folder.path.name}_заметки]]")

    # ── Footer: repo metadata ──
    parts.append(
        f'<footer class="repo-meta">'
        f"Updated {_get_now().strftime('%d.%m.%Y %H:%M')}"
        f" · {direct_total} {_plural(direct_total, 'файл', 'файла', 'файлов')}"
        f" · {direct_subfolders} {_plural(direct_subfolders, 'папка', 'папки', 'папок')}"
        f" · {format_size(direct_size)}"
        f"</footer>"
    )
    # Hidden change-detection manifest (never rendered by Obsidian)
    manifest = json.dumps(
        _manifest_payload(folder, notes_prev, content_hashes, raw_fc, raw_fdc),
        ensure_ascii=False,
        sort_keys=True,
    )
    parts.append(f"<!-- obsidianizer-manifest: {manifest} -->")
    return "\n".join(parts) + "\n"


def format_frontmatter(
    digest: str, template: str = "github", cssclasses: list | None = None
) -> str:
    """Serialize the card YAML: SERVICE KEYS ONLY.

    All user-owned fields live in the ``*_заметки.md`` file — the card is
    fully regenerable and carries nothing user-editable. ``cssclasses`` is
    kept here (not in the notes) because Obsidian applies it to this file.
    """

    lines = ["---"]
    lines.append(f"{CARD_MARKER_KEY}: true")
    lines.append(f"{HASH_KEY}: {digest}")
    lines.append(f"{TEMPLATE_KEY}: {template}")
    lines.append(f"{VERSION_KEY}: {RENDER_VERSION}")
    if template == "github":
        cc = cssclasses or ["github-dashboard"]
        lines.append("cssclasses: [" + ", ".join(str(c) for c in cc) + "]")
    lines.append("---")
    return "\n".join(lines)


def _fmt_yaml_val(key: str, val) -> str:
    if val is None:
        return f"{key}: \n"
    if isinstance(val, str):
        if "[[" in val:
            return f"{key}: '{val}'\n"
        if val == "":
            return f'{key}: ""\n'
        return f"{key}: {val}\n"
    if isinstance(val, bool):
        return f"{key}: {str(val).lower()}\n"
    if isinstance(val, (int, float)):
        return f"{key}: {val}\n"
    if isinstance(val, list):
        items = [f"'{it}'" if isinstance(it, str) and "[[" in it else str(it) for it in val]
        return f"{key}: [{', '.join(items)}]\n"
    return f"{key}: {val}\n"


def _default_value(key: str):
    if key == "дата_начала":
        return _get_now().strftime("%Y-%m-%d")
    if key == "tags":
        return []
    if key in ("file_comments", "folder_comments"):
        return {}
    return None


def _display_value(val) -> str | None:
    """formatDisplayValue: wikilinks kept verbatim, arrays joined."""

    if val is None or val == "":
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        items = [str(it) for it in val]
        return ", ".join(items)
    return str(val)


def _files_table(
    rows: list[FileEntry],
    auto_comments: dict[str, str],
    file_comments: dict[str, str],
    cfg: ObsidianizeConfig,
) -> str:
    """Single GitHub-style file table: icon · name · type · opens-with · date · size
    · auto-comment · user-comments. ``opens-with`` is a per-extension label (OPENERS)."""

    # Sort by extension (without dot) then by name
    rows = sorted(rows, key=lambda f: (f.ext.lower().lstrip('.'), f.name.casefold()))

    lines = [
        "\n| File | Type | Opens with | Modified | Size | Description (Auto) | Comments |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in rows:
        icon = FILE_ICONS[_category_of(f.ext, cfg)]
        file_type = f.ext.lstrip('.').upper() if f.ext else "—"
        opener = OPENERS.get(f.ext, "—")
        auto = auto_comments.get(f.name, "")
        # Canonical card-basis key is the file name (stable across scan
        # bases); legacy scan-basis ``f.rel`` accepted read-only fallback.
        user_raw = file_comments.get(f.name, "")
        if not user_raw and f.rel != f.name:
            user_raw = file_comments.get(f.rel, "")
        user = _truncate_comment(_escape_pipe(user_raw))
        lines.append(
            f"| {icon} [[{f.name}]] | {file_type} | {opener}"
            f" | {format_rel_date(f.mtime_ns)}"
            f" | {format_size(f.size)} | {auto} | {user} |"
        )
    return "\n".join(lines)


def _img_href(rel: str) -> str:
    """Percent-encode only the characters that break Markdown image paths
    (space, parentheses, percent) — Cyrillic stays human-readable and works
    in Obsidian, MarkText, VS Code and GitHub alike."""

    return rel.replace("%", "%25").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _vault_relative(folder_abs: Path, vault_root: str) -> str | None:
    try:
        vault = Path(vault_root).resolve()
    except OSError:
        return None
    folder = folder_abs.resolve()
    if vault == folder or vault in folder.parents:
        return folder.relative_to(vault).as_posix()
    return None


def _gallery_rel_path(folder: FolderScan, cfg: ObsidianizeConfig) -> str | None:
    """Vault-relative path for the img-gallery block.

    Preferred source is ``cfg.vault_root`` (works when the scanned tree lives
    inside the vault). Fallback is ``cfg.gallery_prefix`` — a plain string
    prefix like ``PROJECT/OBSIDIAN/Objects`` (the old Templater-style
    path), so the gallery works even when the working tree is outside the
    vault. Returns None when neither is usable → no gallery section.
    """

    if cfg.vault_root:
        rel = _vault_relative(folder.path, cfg.vault_root)
        if rel is not None:
            return rel
    prefix = (cfg.gallery_prefix or "").strip().strip("/")
    if prefix:
        base = folder.rel or folder.path.name
        return f"{prefix}/{base}"
    return None


# --------------------------------------------------------------------------
# Aggregates (read-only)
# --------------------------------------------------------------------------


def _category_of(ext: str, cfg: ObsidianizeConfig) -> str:
    if ext == "md" and cfg.include_md:
        return "docs"
    for cat in ("drafting", "tables", "docs", "images"):
        if ext in cfg.categories.get(cat, []):
            return cat
    return "other"


def folder_stats(
    tree: dict[str, FolderScan], cfg: ObsidianizeConfig | None = None
) -> dict[str, dict]:
    """Per-folder aggregates for the Project Dashboard renderer.

    For every folder: direct and recursive-subtree totals (files, folders,
    size, latest change), per-category counts/sizes/latest change (both
    direct and subtree-wide), and a subfolder tree-view map (GitHub style).
    Read-only, one pass.
    """

    cfg = cfg or ObsidianizeConfig()
    stats: dict[str, dict] = {}
    for rel, folder in tree.items():
        cats = {
            c: {"count": 0, "size": 0, "max_mtime_ns": None} for c in CATEGORY_ORDER
        }
        total = 0
        mtime = None
        for f in folder.files:
            c = cats[_category_of(f.ext, cfg)]
            c["count"] += 1
            c["size"] += f.size
            c["max_mtime_ns"] = max(c["max_mtime_ns"] or 0, f.mtime_ns)
            total += f.size
            mtime = max(mtime or 0, f.mtime_ns)
        stats[rel] = {
            "total_count": len(folder.files),
            "total_subfolders": len(folder.subfolders),
            "total_size": total,
            "max_mtime_ns": mtime,
            "categories": cats,
            "categories_tree": {c: dict(v) for c, v in cats.items()},
            "images_tree": [(f.rel, f.size) for f in folder.images],
            "subfolders": {},
        }
    # Post-order: deepest first, so subtree aggregates bubble up.
    for rel in sorted(stats, key=lambda r: (r.count("/"), r), reverse=True):
        parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent_rel not in stats:
            continue  # partial tree (e.g. a single subfolder) — nothing to bubble
        s = stats[rel]
        for sub in s["subfolders"].values():
            s["total_count"] += sub["count"]
            s["total_subfolders"] += sub["subfolders"]
            s["total_size"] += sub["size"]
            s["images_tree"] += sub.get("images_tree", [])
            if sub["max_mtime_ns"]:
                s["max_mtime_ns"] = max(s["max_mtime_ns"] or 0, sub["max_mtime_ns"])
            for c in CATEGORY_ORDER:
                cc = s["categories_tree"][c]
                sc = sub["categories"][c]
                cc["count"] += sc["count"]
                cc["size"] += sc["size"]
                cc["max_mtime_ns"] = max(
                    cc["max_mtime_ns"] or 0, sc["max_mtime_ns"] or 0
                )
        if not rel:
            continue
        parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
        name = rel.rsplit("/", 1)[-1]
        stats[parent_rel]["subfolders"][name] = {
            "count": s["total_count"],
            "subfolders": s["total_subfolders"],
            "size": s["total_size"],
            "max_mtime_ns": s["max_mtime_ns"],
            "categories": s["categories_tree"],
            "images_tree": s["images_tree"],
        }
    return stats


# --------------------------------------------------------------------------
# Freshness and updates
# --------------------------------------------------------------------------


def _card_rel_key(folder: FolderScan, rel: str) -> str:
    """Normalize a file rel-path to the card-folder basis.

    Scan rel-paths are relative to the scan root, which varies between runs
    (GUI scans a project root, the Templater hotkey scans the card folder
    itself). Keying fingerprints and manifests by the card-folder-relative
    path makes both basis-independent. Falls back to the raw rel when the
    prefix does not match (defensive; should not happen for real subtrees).
    """

    prefix = (folder.rel + "/") if folder.rel else (folder.path.name + "/")
    return rel[len(prefix):] if rel.startswith(prefix) else rel


def _compute_content_hashes(folder: FolderScan) -> dict[str, str]:
    """sha1 of the FULL content of every direct file of the folder.

    Content is the only reliable change indicator: cloud sync (Dropbox etc.)
    and background tools touch mtimes without changing a single byte, which
    made mtime-based fingerprints permanently stale. Keyed by the scan-root
    rel (the same basis :func:`folder_fingerprint` consumes). An unreadable
    file yields "" — not a valid hash, so it always counts as changed and
    the card refreshes as soon as the file becomes readable again.
    """

    hashes: dict[str, str] = {}
    for f in folder.files:
        try:
            h = hashlib.sha1()
            with open(folder.path / f.name, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            hashes[f.rel] = h.hexdigest()[:12]
        except OSError:
            hashes[f.rel] = ""
    return hashes


def folder_fingerprint(
    folder: FolderScan, content_hashes: dict[str, str] | None = None
) -> str:
    """sha1 over sorted file entries (rel, size, content hash), subfolders and
    the presence of the AI-review file (so the card's review link stays fresh).

    Content-based: mtime is NEVER part of the fingerprint (cloud sync makes
    it unstable); it stays in :class:`FileEntry` for display only.
    File paths are keyed in the card-folder basis, so the fingerprint is
    identical no matter which root the tree was scanned from."""

    if content_hashes is None:
        content_hashes = _compute_content_hashes(folder)
    items = [
        f"F:{_card_rel_key(folder, f.rel)}\x00{f.size}\x00{content_hashes.get(f.rel, '')}"
        for f in folder.files
    ]
    items += [f"D:{s}" for s in folder.subfolders]
    items.append("R:1" if review_file_path(folder).is_file() else "R:0")
    items.sort()
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Change detection (manifest stored inside the card as a hidden comment)
# --------------------------------------------------------------------------


_MANIFEST_RE = re.compile(
    r"<!-- obsidianizer-manifest: (\{.*?\}) -->", re.DOTALL
)


def _notes_user_hash(notes_prev: str | None) -> str:
    """sha1 of the user-owned frontmatter of the notes file ("" when empty).

    Included in the manifest so that editing e.g. ``контакт`` marks the
    card stale even when no project file changed at all.
    """

    if not notes_prev:
        return ""
    props = _user_props(parse_frontmatter(notes_prev))
    if not props:
        return ""
    payload = json.dumps(props, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _comment_hash(value) -> str:
    """Short stable hash of a rendered comment value (manifest snapshot).

    ``None`` counts as ``""``.  Hashes (not texts) are stored so the
    manifest stays small; equality with the live FM value proves the card
    displayed exactly what FM holds now.
    """

    return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:12]


def _cell_expected(live_raw: str) -> str:
    """Value the Files/Folders table cell shows for a raw FM comment.

    Mirrors the exact render→extract pipeline (escape → truncate → the
    unescape chain of :func:`extract_user_comments`), so a rebuilt card
    always compares equal — the coherence check below has a fixed point
    and can never force a rebuild loop.
    """

    return (
        _truncate_comment(_escape_pipe(live_raw or ""))
        .replace("\x00", "|")
        .replace("\\\\", "\\")
        .replace("\\|", "|")
    )


def _display_coherent(
    prev: str,
    folder: FolderScan,
    live_fc: dict,
    live_fdc: dict,
) -> bool:
    """True when the card's Comments cells already show the live FM values.

    Compares per canonical key, so a render miss (card shows ``""`` while
    FM holds text — even with a fully fresh manifest) is detected and the
    caller rebuilds instead of skipping.  Healing a divergent display is
    what restores affected vaults; coherent cards still skip untouched.
    """

    try:
        table_fc = extract_user_comments(prev, folder.files)
    except Exception:
        return False
    try:
        name_to_rel = {s: _join_rel(folder.rel, s) for s in folder.subfolders}
        table_fdc = extract_folder_comments(prev, name_to_rel)
    except Exception:
        return False
    for f in folder.files:
        raw = live_fc.get(f.name, "")
        if not raw and f.rel != f.name:
            raw = live_fc.get(f.rel, "")
        if table_fc.get(f.name) != _cell_expected(raw):
            return False
    for s in folder.subfolders:
        key = _join_rel(folder.rel, s)
        if table_fdc.get(key) != _cell_expected(live_fdc.get(key, "")):
            return False
    return True


def _manifest_payload(
    folder: FolderScan,
    notes_prev: str | None,
    content_hashes: dict[str, str] | None = None,
    file_comments: dict | None = None,
    folder_comments: dict | None = None,
) -> dict:
    # ``base`` pins the basis the file keys were written against (the rel of
    # the card folder from the update root), so card_diff can always align
    # legacy/future manifests scanned from a different root. Every file
    # entry carries the content hash: card_diff decides "changed" by hash,
    # never by mtime (see folder_fingerprint).
    # ``fc``/``fdc`` snapshot WHAT THE CARD DISPLAYED (per-key hashes of the
    # rendered comment values, canonical card-basis keys).  TICKET-015 v7:
    # an empty table cell clears FM only when the snapshot proves the card
    # showed exactly the FM value (deliberate clear); otherwise the empty
    # cell is a render miss and FM is preserved.  Missing snapshot (old
    # manifests) always means "not proven" → preserve (data-safe).
    if content_hashes is None:
        content_hashes = _compute_content_hashes(folder)
    payload = {
        "base": folder.rel,
        "files": {
            _card_rel_key(folder, f.rel): {
                "size": f.size,
                "hash": content_hashes.get(f.rel, ""),
            }
            for f in folder.files
        },
        "folders": list(folder.subfolders),
        "notes": _notes_user_hash(notes_prev),
    }
    if file_comments is not None:
        payload["fc"] = {
            f.name: _comment_hash(
                file_comments.get(f.name, "")
                or (file_comments.get(f.rel, "") if f.rel != f.name else "")
            )
            for f in folder.files
        }
    if folder_comments is not None:
        payload["fdc"] = {
            _join_rel(folder.rel, s): _comment_hash(folder_comments.get(_join_rel(folder.rel, s), ""))
            for s in folder.subfolders
        }
    return payload


def _parse_manifest(content: str) -> dict | None:
    m = _MANIFEST_RE.search(content or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _align_legacy_manifest_keys(
    folder: FolderScan, old_files: dict
) -> dict:
    """Deterministically align legacy manifest keys (no ``base`` field) to
    the card-folder basis.

    Rule (no heuristics): if at least one key carries the scan-root prefix
    (``folder.rel + "/"``, or the card folder's own name for a root card),
    the manifest was written from a higher root — strip that prefix from
    every key. Otherwise the keys are already card-relative.
    """

    prefix = (folder.rel + "/") if folder.rel else (folder.path.name + "/")
    if prefix and any(k.startswith(prefix) for k in old_files):
        return {k[len(prefix):]: v for k, v in old_files.items()}
    return dict(old_files)


def card_diff(
    prev_card: str | None,
    folder: FolderScan,
    notes_prev: str | None = None,
    content_hashes: dict[str, str] | None = None,
) -> dict | None:
    """Human-oriented diff between the stored manifest and current state.

    Returns None when the old card has no parsable manifest (e.g. a foreign
    note or a pre-manifest version) — the caller then just reports "stale".
    Both sides are compared in the card-folder basis: scan keys via
    :func:`_card_rel_key`, manifest keys via ``base`` (new manifests) or the
    deterministic legacy-prefix rule. ``changed`` is decided by CONTENT HASH:
    same-size edits (AAAA→BBBB) are detected, mtime jitter is ignored.
    Legacy entries (``path → size`` int, no hash) fall back to the old size
    comparison — a legacy manifest always ends up stale via the fingerprint
    check anyway and is regenerated in the new format on the next update.
    """

    old = _parse_manifest(prev_card or "")
    if old is None:
        return None
    if content_hashes is None:
        content_hashes = _compute_content_hashes(folder)
    cur_files = {
        _card_rel_key(folder, f.rel): {
            "size": f.size,
            "hash": content_hashes.get(f.rel, ""),
        }
        for f in folder.files
    }
    old_raw = old.get("files") or {}
    if "base" in old:
        # New manifest: keys are already card-relative (written via
        # _card_rel_key); ``base`` only records the update root.
        old_files = dict(old_raw)
    else:
        old_files = _align_legacy_manifest_keys(folder, old_raw)
    # Normalize legacy entries (path → size int) to the current dict shape.
    old_files = {
        k: (v if isinstance(v, dict) else {"size": v, "hash": ""})
        for k, v in old_files.items()
    }
    added = sorted(set(cur_files) - set(old_files))
    removed = sorted(set(old_files) - set(cur_files))
    changed = sorted(
        r
        for r in set(cur_files) & set(old_files)
        if (
            old_files[r]["hash"] and cur_files[r]["hash"] != old_files[r]["hash"]
        )
        or (
            not old_files[r]["hash"]
            and cur_files[r]["size"] != old_files[r]["size"]
        )
    )
    folders_changed = set(old.get("folders") or []) != set(folder.subfolders)
    notes_changed = (old.get("notes") or "") != _notes_user_hash(notes_prev)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "folders_changed": folders_changed,
        "notes_changed": notes_changed,
    }


def format_changes(diff: dict) -> list[str]:
    """Turn a :func:`card_diff` result into short human-readable lines."""

    lines: list[str] = []
    for rel in diff.get("added", []):
        lines.append(f"добавлен: {rel}")
    for rel in diff.get("removed", []):
        lines.append(f"удалён: {rel}")
    for rel in diff.get("changed", []):
        lines.append(f"изменён: {rel}")
    if diff.get("folders_changed"):
        lines.append("изменилась структура папок")
    if diff.get("notes_changed"):
        lines.append("изменены данные проекта (заметки)")
    return lines


def card_status(
    card_path: Path,
    folder: FolderScan,
    template: str = "github",
    notes_prev: str | None = None,
    content_hashes: dict[str, str] | None = None,
) -> str:
    """One of: ok / stale / missing / conflict.

    ``stale`` covers a changed file fingerprint (content-based: see
    :func:`folder_fingerprint`), a mismatched card template, an older
    renderer version, AND changed user data in the notes file (e.g.
    ``контакт`` edited → the project card must be rebuilt).
    """

    if not card_path.is_file():
        return "missing"
    try:
        content = card_path.read_text(encoding="utf-8")
    except OSError:
        return "conflict"
    props = parse_frontmatter(content)
    if props.get(CARD_MARKER_KEY) is not True:
        return "conflict"
    manifest = _parse_manifest(content)
    if (
        manifest is not None
        and notes_prev is not None
        and (manifest.get("notes") or "") != _notes_user_hash(notes_prev)
    ):
        return "stale"
    if props.get(HASH_KEY) != folder_fingerprint(folder, content_hashes):
        return "stale"
    if props.get(TEMPLATE_KEY, "github") != template:
        return "stale"
    if props.get(VERSION_KEY) != RENDER_VERSION:
        return "stale"
    return "ok"


def _parent_rel(rel: str, cfg: ObsidianizeConfig) -> str | None:
    """Vault-relative path of the parent folder for ``rel``.

    With ``cfg.rel_root`` set (local update: the scanned root is a folder
    inside the vault) the parent is computed from the vault path, so the
    "⬆ Up" link is consistent with GUI full-tree runs.
    """

    base = (cfg.rel_root or "").strip("/")
    if base:
        full = base + ("/" + rel if rel else "")
        if "/" in full:
            return full.rsplit("/", 1)[0]
        return None
    if rel:
        # Parent of a subfolder is the root (empty string)
        if "/" in rel:
            return rel.rsplit("/", 1)[0]
        return ""  # immediate child of root -> parent is root
    return None
    if rel and "/" in rel:
        return rel.rsplit("/", 1)[0]
    return None


def _process_folder(
    folder: FolderScan,
    cfg: ObsidianizeConfig,
    parent_rel: str | None,
    stats: dict | None,
    dry_run: bool,
) -> tuple[str, str | None]:
    """Process a single folder's card; returns ``(action, conflict_path)``.

    ``action`` is one of ``created`` / ``updated`` / ``skipped`` /
    ``conflict`` / ``failed`` (``failed`` = safe refusal: existing user
    content unreadable/unreliable, nothing was overwritten).
    Raises on any unexpected error — :func:`update_cards` isolates, logs and
    continues with the remaining folders.
    """

    card = card_path_for(folder)
    notes_p = notes_file_path(folder)

    def _read_notes() -> str | None:
        if not notes_p.exists():
            return None
        try:
            return notes_p.read_text(encoding="utf-8")
        except OSError:
            return None

    notes_unreadable = notes_p.exists()
    prev: str | None = None
    card_unreadable = False
    if card.exists():
        try:
            prev = card.read_text(encoding="utf-8")
        except OSError:
            prev = None
            card_unreadable = True
    if card_unreadable:
        logger.error("Obsidianize: карточка %s не читается — пропуск без записи", card)
        return "failed", None
    # Notes are read BEFORE the status check: changed user data must
    # mark the card stale even when no project file has changed.
    notes_prev = _read_notes()
    # Content hashes once per folder: reused by the status check and the
    # card build so every file is read at most one time per run.
    content_hashes = _compute_content_hashes(folder)

    # Safe refusal (TICKET-015) BEFORE any branch that could write or
    # misclassify: corrupt FM is failed with diagnostics, never a silent
    # conflict counter; absent FM keeps the legacy conflict path.
    if notes_unreadable and notes_prev is None:
        logger.error("Obsidianize: заметки %s не читаются — пропуск без записи", notes_p)
        return "failed", None
    if prev is not None:
        _, _cs0 = parse_frontmatter_strict(prev)
        if _cs0 == "corrupt":
            logger.error("Obsidianize: повреждён frontmatter карточки %s — пропуск без записи", card)
            return "failed", None

    _card_skipped = False
    if prev is not None and card_is_ours(prev):
        if (
            card_status(
                card,
                folder,
                template=cfg.template,
                notes_prev=notes_prev,
                content_hashes=content_hashes,
            )
            == "ok"
            and not cfg.force
            and not cfg.rebuild
        ):
            _card_skipped = True
    elif prev is not None and not cfg.force:
        adopted = False
        if cfg.adopt and not dry_run and not notes_p.exists():
            adopted = _adopt_foreign_note(card, notes_p)
        if not adopted:
            return "conflict", str(card)
        # Adopted: the foreign note became our notes file; build fresh.
        prev = None

    # Safe refusal (TICKET-015): when existing user content cannot be
    # read reliably, refuse BEFORE any write — source stays untouched,
    # the rel lands in summary.failed with a logged reason.
    if notes_prev is not None:
        _, _ns = parse_frontmatter_strict(notes_prev)
        if _ns == "corrupt":
            logger.error("Obsidianize: повреждён frontmatter заметок %s — пропуск без записи", notes_p)
            return "failed", None

    # Order-of-operations safety: the notes file (the user-owned layer,
    # including migrated frontmatter/manual block) is written BEFORE the
    # card, so a crash in between can never lose user data.
    if not dry_run:
        _ensure_notes_file(folder, prev)
        notes_prev = _read_notes()  # may have been created/migrated

        # Sync: extract Comments from card table → notes frontmatter.
        # Runs ALWAYS (even when card will be skipped) to preserve user edits.
        if not _sync_comments_to_notes(notes_p, prev, folder):
            logger.error("Obsidianize: sync комментариев %s не удался — пропуск без записи", folder.rel or ".")
            return "failed", None
        notes_prev = _read_notes()  # re-read with synced comments

    if _card_skipped and prev is not None:
        # Coherence gate (v7): status "ok" does not cover the Comments
        # display.  When the cells show anything but the live FM values
        # (render miss — even with a fresh manifest), rebuild instead of
        # skipping so the display heals.  Coherent cards still skip.
        try:
            _live = parse_frontmatter(notes_prev) if notes_prev else {}
            _lfc = _live.get("file_comments") or {}
            _lfdc = _live.get("folder_comments") or {}
            if not isinstance(_lfc, dict):
                _lfc = {}
            if not isinstance(_lfdc, dict):
                _lfdc = {}
            if _display_coherent(prev, folder, _lfc, _lfdc):
                return "skipped", None
            logger.info(
                "Obsidianize: Comments на карточке %s расходятся с заметками — перерисовка",
                folder.rel or ".",
            )
        except Exception:
            logger.exception("Obsidianize: проверка Comments %s не удалась", folder.rel or ".")

    text = build_card(
        folder, prev, cfg, parent_rel, stats, notes_prev, content_hashes
    )
    if dry_run:
        return ("created" if prev is None else "updated"), None
    changed = write_atomic(card, text)
    # force/rebuild count as an update even when the content is identical:
    # the card was explicitly rebuilt from the current renderer.
    if changed or cfg.force or cfg.rebuild:
        return ("created" if prev is None else "updated"), None
    return "skipped", None


def update_cards(
    root: Path,
    cfg: ObsidianizeConfig | None = None,
    on_progress=None,
    dry_run: bool = False,
    recursive: bool = True,
    selection: set[str] | list[str] | None = None,
    selection_provided: bool = False,
) -> UpdateSummary:
    """Create/refresh cards for the whole tree.

    Read-only contract: the only files ever written are ``<folder name>.md``
    cards and the derived ``<folder name>_заметки.md`` notes files (created
    only when missing, never overwritten, and written BEFORE the card so a
    crash cannot lose the migrated manual block). Foreign notes with the same
    name are skipped (recorded in ``conflicts``) unless ``cfg.force`` is set.
    Unchanged cards are not rewritten (unless ``cfg.force`` or ``cfg.rebuild``
    — then own cards are rebuilt from the current renderer anyway; rebuild,
    unlike force, never touches foreign notes). With ``recursive=False`` only
    the root card is processed.

    TICKET-013 selection: ``selection`` is the checked set from tab 1
    (card-relative rels, ``""`` = root). It filters only CARD GENERATION —
    never the parent ``Folders``-table content or the fingerprint/version.
    ``selection_provided=False`` ignores ``selection`` entirely (legacy
    behaviour); ``provided=True + None/empty`` is a safe no-op. A selected
    folder auto-creates its ancestor chain to the root as structural
    navigation containers (``⬆ Up`` never dangles); ancestors never trigger
    processing of their own content. An explicitly selected excluded dir is
    processed once via :func:`classify_dir_isolated` (one-shot override —
    the rule in config.yml is untouched).

    ``on_progress(rel, action)`` is called once per folder with the action
    taken: ``created`` / ``updated`` / ``skipped`` / ``conflict`` / ``failed``.
    A folder whose processing raises is isolated (logged with traceback,
    recorded in ``summary.failed``) and never aborts the remaining folders.
    """

    cfg = cfg or ObsidianizeConfig()
    tree = scan_tree(root, cfg)
    # Aggregates must be computed on the FULL subtree: with --no-recursive the
    # tree is trimmed to the root entry only, and folder_stats would silently
    # lose every subfolder aggregate (Folders table renders 0 / 0 B / empty).
    stats = folder_stats(tree, cfg)
    if not recursive:
        # Trim AFTER stats: only the iteration is scoped, the aggregates stay.
        tree = {rel: folder for rel, folder in tree.items() if rel == ""}
    if selection_provided:
        effective = {_norm_sel_rel(r) for r in (selection or [])}
        effective.discard(None)  # type: ignore[arg-type]
        effective = set(effective)
        if not effective:
            return UpdateSummary(scanned=0)
        if not recursive:
            # Fixed order (§5.4): trim first, then selection — with
            # recursive=False only the root card is ever in scope.
            effective = {r for r in effective if r == ""}
            if not effective:
                return UpdateSummary(scanned=0)
        # Ancestor chain: selected dir + parents to root as containers.
        expanded: set[str] = set(effective)
        for rel in list(effective):
            if rel == "":
                continue
            parts = rel.split("/")
            for i in range(1, len(parts)):
                expanded.add("/".join(parts[:i]))
            expanded.add("")
        work: dict[str, tuple] = {}
        for rel in sorted(expanded):
            if rel in tree:
                work[rel] = (tree[rel], stats.get(rel))
            else:
                folder = classify_dir_isolated(root, rel, cfg)
                if folder is None:
                    continue
                work[rel] = (folder, folder_stats({rel: folder}, cfg).get(rel))
        summary = UpdateSummary(scanned=len(work))
        for rel, (folder, st) in work.items():
            try:
                action, conflict_path = _process_folder(
                    folder, cfg, _parent_rel(rel, cfg), st, dry_run
                )
            except Exception:  # noqa: BLE001 — isolate a bad folder, keep going
                summary.failed.append(rel)
                logger.exception("Obsidianize: папка %s не обработана", rel)
                if on_progress is not None:
                    on_progress(rel, "failed")
                continue
            _account_summary(summary, action, conflict_path or rel, dry_run, rel)
            if on_progress is not None:
                on_progress(rel, action)
        return summary
    summary = UpdateSummary(scanned=len(tree))
    for rel, folder in tree.items():
        try:
            action, conflict_path = _process_folder(
                folder, cfg, _parent_rel(rel, cfg), stats.get(rel), dry_run
            )
        except Exception:  # noqa: BLE001 — isolate a bad folder, keep going
            summary.failed.append(rel)
            logger.exception("Obsidianize: папка %s не обработана", rel)
            if on_progress is not None:
                on_progress(rel, "failed")
            continue

        _account_summary(summary, action, conflict_path or rel, dry_run, rel)
        if on_progress is not None:
            on_progress(rel, action)
    return summary


def _norm_sel_rel(rel: str | None) -> str | None:
    """Normalize a selection rel; ``None`` on traversal/absolute paths."""

    if rel is None:
        return None
    s = str(rel).replace("\\", "/").strip("/")
    if s in ("", "."):
        return ""
    if not s or s.startswith("/") or s.startswith(".."):
        return None
    parts = s.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return s


def _account_summary(
    summary: UpdateSummary,
    action: str,
    conflict_path: str | None,
    dry_run: bool,
    rel: str = "",
) -> None:
    if action == "failed":
        summary.failed.append(conflict_path or rel or ".")
    elif action == "conflict":
        summary.conflicts.append(conflict_path or "")
    elif action == "skipped":
        summary.skipped += 1
    elif dry_run:
        # dry-run accounting preserved: everything that would be written
        # counts as an update (created stays 0).
        summary.updated += 1
    elif action == "created":
        summary.created += 1
    else:
        summary.updated += 1


def write_atomic(path: Path, content: str) -> bool:
    """Write atomically (.tmp -> os.replace); returns False when the content
    is unchanged (nothing is written then)."""

    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True