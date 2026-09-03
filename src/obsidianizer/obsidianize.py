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
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

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
RENDER_VERSION = 8  # bump to auto-migrate existing cards to a new structure

YAML_KEYS = ["дата_начала", "источник", "контакт", "проект", "адрес", "tags", "комментарий"]
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
    include_md: bool = False
    img_gallery: bool = True
    vault_root: str = ""  # preferred: vault-relative gallery paths
    gallery_prefix: str = ""  # fallback: "PROJECT/OBSIDIAN/Objects" style prefix
    force: bool = False  # overwrite a foreign note that lacks the marker
    adopt: bool = False  # rename a foreign note into <name>_заметки.md (1:1)
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


def parse_frontmatter(content: str) -> dict:
    """Parse the leading YAML block into typed values (order preserved).

    Handles flat keys, inline lists and block lists (``- item`` lines that
    belong to the previous key, as Obsidian writes multi-line ``tags``), so
    user frontmatter data is never silently lost on regeneration.
    """

    m = re.match(r"^---\n([\s\S]*?)\n---", content)
    if not m:
        return {}
    props: dict = {}
    cur_key: str | None = None
    for line in m.group(1).split("\n"):
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
        props[key] = _parse_scalar(line[idx + 1 :].strip())
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
        return [s.strip().strip('"\'') for s in inner.split(",") if s.strip()]
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
    """Split a markdown table row into cells, keeping wikilinks whole.

    Both escaped (``\|``) and raw alias pipes inside ``[[…|…]]`` are masked
    before splitting, so the cell boundary never cuts a link in two.
    """

    masked = re.sub(
        r"\[\[[^\]\n]*\]\]",
        lambda m: m.group(0).replace("|", "\x00"),
        line,
    )
    return [c.strip() for c in masked.strip().strip("|").replace("\\|", "\x00").split("|")]


def extract_comments(content: str) -> dict[str, str]:
    """Collect per-file comments from the card tables.

    The key is the display name (the part after ``|`` in a wikilink), which is
    stable across card regeneration AND across vault moves, and matches the
    format produced by the original Templater script.

    Handles both table shapes: old cards had the comment right after the
    wikilink; the current Files table places it in the LAST column (after
    «Открывается / Изменено / Размер»). A comment that merely duplicates the
    «Открывается» value is treated as a migration artifact and ignored.
    """

    comments: dict[str, str] = {}
    new_format = False
    for line in content.splitlines():
        if line.lstrip().startswith("|") and "Открывается" in line and "Файл" in line:
            new_format = True
        m = re.search(r"\[\[([^\]|]*?)(?:\|([^\]|]*))?\]\]", line)
        if not m:
            continue
        name = (m.group(2) or m.group(1)).strip()
        cells = _split_table_cells(line)
        if not cells:
            continue
        if new_format:
            opener = cells[1] if len(cells) >= 2 else ""
            comment = cells[-1].strip()
            if comment and comment != opener:
                comments[name] = comment
        else:
            idx = next((i for i, c in enumerate(cells) if "[[" in c), None)
            if idx is not None and idx + 1 < len(cells):
                comment = cells[idx + 1].strip()
                if comment:
                    comments[name] = comment
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
    """YAML block for the notes file: standard keys first, then user extras."""

    lines = ["---"]
    for key in YAML_KEYS:
        lines.append(_fmt_yaml_val(key, props.get(key, _default_value(key))).rstrip("\n"))
    for key, val in props.items():
        if key in YAML_KEYS or key in SERVICE_KEYS:
            continue
        lines.append(_fmt_yaml_val(key, val).rstrip("\n"))
    lines.append("---")
    return "\n".join(lines)


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
) -> str:
    """Compose the full card text for one folder (Project Dashboard v5).

    The card carries ONLY service frontmatter; ALL user fields live in the
    separate ``*_заметки.md`` file (embedded). ``prev`` is the current card
    content; ``notes_prev`` is the current notes-file content whose
    frontmatter feeds the About section and the repo subtitle.
    ``stats`` is the optional ``folder_stats`` entry for this folder.
    """

    return _render_dashboard(folder, prev, cfg, parent_rel, stats, notes_prev)


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

    digest = folder_fingerprint(folder)
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
    parts.append("\n| Name | Files | Size | Updated |")
    parts.append("| --- | --- | --- | --- |")
    if parent_rel is not None:
        if parent_rel:
            parent_name = parent_rel.rsplit("/", 1)[-1]
            link = f"../{parent_name}"
        else:
            # Parent is the scanned root
            parent_name = folder.path.parent.name
            link = ".."
        parts.append(f"| ⬆ [[{link}\\|Up]] |  |  |  |")
    subs = st.get("subfolders") or {}
    if folder.subfolders:
        for sub in folder.subfolders:
            ss = subs.get(sub)
            if ss is None or not ss["count"]:
                parts.append(f"| 📁 [[./{sub}/{sub}\\|{sub}]] | 0 | 0 B | |")
                continue
            changed = format_rel_date(ss["max_mtime_ns"]) if ss["max_mtime_ns"] else ""
            size_str = format_size(ss["size"])
            parts.append(
                f"| 📁 [[./{sub}/{sub}\\|{sub}]] | {ss['count']} | {size_str} | {changed} |"
            )
    else:
        parts.append("| *No folders* | | | |")

    # ── Files: single table of all direct files (GitHub file list) ──
    parts.append("\n## Files")
    if folder.files:
        parts.append(_files_table(folder.files, comments, cfg))
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
        _manifest_payload(folder, notes_prev), ensure_ascii=False, sort_keys=True
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
    rows: list[FileEntry], comments: dict[str, str], cfg: ObsidianizeConfig
) -> str:
    """Single GitHub-style file table: icon · name · type · opens-with · date · size
    · comment. ``opens-with`` is a per-extension label (OPENERS)."""

    # Sort by extension (without dot) then by name
    rows = sorted(rows, key=lambda f: (f.ext.lower().lstrip('.'), f.name.casefold()))

    lines = [
        "\n| File | Type | Opens with | Modified | Size | Comment |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in rows:
        icon = FILE_ICONS[_category_of(f.ext, cfg)]
        file_type = f.ext.lstrip('.').upper() if f.ext else "—"
        opener = OPENERS.get(f.ext, "—")
        lines.append(
            f"| {icon} [[{f.name}]] | {file_type} | {opener} | {format_rel_date(f.mtime_ns)}"
            f" | {format_size(f.size)} | {comments.get(f.name, '')} |"
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


def folder_fingerprint(folder: FolderScan) -> str:
    """sha1 over sorted file entries (rel, size, mtime), subfolders and the
    presence of the AI-review file (so the card's review link stays fresh).

    File paths are keyed in the card-folder basis, so the fingerprint is
    identical no matter which root the tree was scanned from."""

    items = [
        f"F:{_card_rel_key(folder, f.rel)}\x00{f.size}\x00{f.mtime_ns}"
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


def _manifest_payload(folder: FolderScan, notes_prev: str | None) -> dict:
    # ``base`` pins the basis the file keys were written against (the rel of
    # the card folder from the update root), so card_diff can always align
    # legacy/future manifests scanned from a different root.
    return {
        "base": folder.rel,
        "files": {_card_rel_key(folder, f.rel): f.size for f in folder.files},
        "folders": list(folder.subfolders),
        "notes": _notes_user_hash(notes_prev),
    }


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
) -> dict | None:
    """Human-oriented diff between the stored manifest and current state.

    Returns None when the old card has no parsable manifest (e.g. a foreign
    note or a pre-manifest version) — the caller then just reports "stale".
    Both sides are compared in the card-folder basis: scan keys via
    :func:`_card_rel_key`, manifest keys via ``base`` (new manifests) or the
    deterministic legacy-prefix rule.
    """

    old = _parse_manifest(prev_card or "")
    if old is None:
        return None
    cur_files = {_card_rel_key(folder, f.rel): f.size for f in folder.files}
    old_raw = old.get("files") or {}
    if "base" in old:
        # New manifest: keys are already card-relative (written via
        # _card_rel_key); ``base`` only records the update root.
        old_files = dict(old_raw)
    else:
        old_files = _align_legacy_manifest_keys(folder, old_raw)
    added = sorted(set(cur_files) - set(old_files))
    removed = sorted(set(old_files) - set(cur_files))
    changed = sorted(
        r for r in set(cur_files) & set(old_files) if cur_files[r] != old_files[r]
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
) -> str:
    """One of: ok / stale / missing / conflict.

    ``stale`` covers a changed file fingerprint, a mismatched card template,
    an older renderer version, AND changed user data in the notes file
    (e.g. ``контакт`` edited → the project card must be rebuilt).
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
    if props.get(HASH_KEY) != folder_fingerprint(folder):
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


def update_cards(
    root: Path,
    cfg: ObsidianizeConfig | None = None,
    on_progress=None,
    dry_run: bool = False,
    recursive: bool = True,
) -> UpdateSummary:
    """Create/refresh cards for the whole tree.

    Read-only contract: the only files ever written are ``<folder name>.md``
    cards and the derived ``<folder name>_заметки.md`` notes files (created
    only when missing, never overwritten, and written BEFORE the card so a
    crash cannot lose the migrated manual block). Foreign notes with the same
    name are skipped (recorded in ``conflicts``) unless ``cfg.force`` is set.
    Unchanged cards are not rewritten (unless ``cfg.force`` — then they are
    rebuilt from the current renderer anyway). With ``recursive=False`` only
    the root card is processed.

    ``on_progress(rel, action)`` is called once per folder with the action
    taken: ``created`` / ``updated`` / ``skipped`` / ``conflict``.
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
    summary = UpdateSummary(scanned=len(tree))
    for rel, folder in tree.items():
        card = card_path_for(folder)
        parent_rel = _parent_rel(rel, cfg)
        notes_p = notes_file_path(folder)

        def _read_notes() -> str | None:
            if not notes_p.exists():
                return None
            try:
                return notes_p.read_text(encoding="utf-8")
            except OSError:
                return None

        prev: str | None = None
        if card.exists():
            try:
                prev = card.read_text(encoding="utf-8")
            except OSError:
                prev = None
        # Notes are read BEFORE the status check: changed user data must
        # mark the card stale even when no project file has changed.
        notes_prev = _read_notes()

        if prev is not None and card_is_ours(prev):
            if (
                card_status(card, folder, template=cfg.template, notes_prev=notes_prev)
                == "ok"
                and not cfg.force
            ):
                summary.skipped += 1
                if on_progress is not None:
                    on_progress(rel, "skipped")
                continue
        elif prev is not None and not cfg.force:
            adopted = False
            if cfg.adopt and not dry_run and not notes_p.exists():
                adopted = _adopt_foreign_note(card, notes_p)
            if not adopted:
                summary.conflicts.append(str(card))
                if on_progress is not None:
                    on_progress(rel, "conflict")
                continue
            # Adopted: the foreign note became our notes file; build fresh.
            prev = None

        # Order-of-operations safety: the notes file (the user-owned layer,
        # including migrated frontmatter/manual block) is written BEFORE the
        # card, so a crash in between can never lose user data.
        if not dry_run:
            _ensure_notes_file(folder, prev)
            notes_prev = _read_notes()  # may have been created/migrated

        text = build_card(folder, prev, cfg, parent_rel, stats.get(rel), notes_prev)
        if dry_run:
            summary.updated += 1
            if on_progress is not None:
                on_progress(rel, "created" if prev is None else "updated")
            continue
        changed = write_atomic(card, text)
        # force counts as an update even when the content is identical:
        # the card was explicitly rebuilt from the current renderer.
        if changed or cfg.force:
            if prev is None:
                summary.created += 1
            else:
                summary.updated += 1
        else:
            summary.skipped += 1
        if on_progress is not None:
            action = "created" if prev is None else (
                "updated" if (changed or cfg.force) else "skipped"
            )
            on_progress(rel, action)
    return summary


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