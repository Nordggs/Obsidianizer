"""Structural overview builder (TICKET-014): SourceIndex -> Markdown card.

Pure, deterministic, LLM-free. Analysis depth (§13 of the ticket): directory
names, nesting, file names/extensions and obvious patterns only — no file
contents are read. Unknown directories are never an error: they fall back to
content-based heuristics.

The DTO keeps normalized relative paths; the renderer presents files as
Obsidian ``[[wiki]]`` links (same syntax family as the v5 ``obsidianize.py``
cards: ``[[rel/path|name]]``) and folders as plain code spans (M6 — a folder
has no note to resolve to, so fake-navigable links are not emitted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: Derived card artifacts: tracked as service files, excluded from the
#: purpose heuristics (TICKET-014 AUDIT 3, remark 2). Unlike ``scan_tree``,
#: the SourceIndex does not filter them, so the builder does it explicitly.
DERIVED_SUFFIXES = ("_заметки.md", "_обзор.md")

#: Known directory patterns (§11): exact (case-insensitive) base names with
#: a ready-made purpose line. Extensible — add entries, never branch logic.
KNOWN_DIR_PATTERNS: dict[str, str] = {
    ".git": "Служебный каталог Git-репозитория.",
    ".obsidian": "Конфигурация и служебные данные хранилища Obsidian.",
    ".opencode": "Конфигурация и рабочие данные OpenCode.",
    "__pycache__": "Кэш скомпилированных Python-модулей.",
    ".pytest_cache": "Кэш и служебные данные pytest.",
    ".venv": "Виртуальное окружение Python.",
    "venv": "Виртуальное окружение Python.",
    ".mypy_cache": "Кэш проверки типов mypy.",
    ".ruff_cache": "Кэш линтера Ruff.",
    "node_modules": "Зависимости Node.js.",
    ".idea": "Служебные данные IDE PyCharm/IntelliJ.",
    ".vscode": "Настройки редактора VS Code.",
    "context": "Контекстные материалы проекта / данные для работы с контекстом.",
}

#: file_type (from ``source_index.detect_type``) -> human group name.
TYPE_GROUP: dict[str, str] = {
    "chat_export": "экспортированные чаты",
    "text": "текстовые файлы",
    "document": "документы",
    "image": "изображения",
    "cad": "CAD-чертежи",
    "spreadsheet": "таблицы",
    "archive": "архивы",
    "binary": "бинарные файлы",
    "unknown": "файлы",
}

#: extension -> (Тип для таблицы, Описание для таблицы) (§14).
FILE_DESC: dict[str, tuple[str, str]] = {
    ".py": ("Python", "Исходный код"),
    ".md": ("Markdown", "Документация"),
    ".txt": ("Текст", "Текстовый файл"),
    ".toml": ("TOML", "Конфигурация"),
    ".yml": ("YAML", "Конфигурация"),
    ".yaml": ("YAML", "Конфигурация"),
    ".json": ("JSON", "Данные/конфигурация"),
    ".cfg": ("CFG", "Конфигурация"),
    ".ini": ("INI", "Конфигурация"),
    ".pdf": ("PDF", "Документация"),
    ".docx": ("Word", "Документация"),
    ".doc": ("Word", "Документация"),
    ".xlsx": ("Excel", "Таблица"),
    ".xls": ("Excel", "Таблица"),
    ".csv": ("CSV", "Таблица данных"),
    ".png": ("PNG", "Изображение"),
    ".jpg": ("JPEG", "Изображение"),
    ".jpeg": ("JPEG", "Изображение"),
    ".gif": ("GIF", "Изображение"),
    ".svg": ("SVG", "Векторное изображение"),
    ".dwg": ("DWG", "CAD-чертёж"),
    ".dxf": ("DXF", "CAD-чертёж"),
    ".zip": ("ZIP", "Архив"),
    ".exe": ("EXE", "Исполняемый файл"),
    ".bat": ("BAT", "Сценарий Windows"),
    ".ps1": ("PowerShell", "Сценарий"),
    ".html": ("HTML", "Веб-страница"),
    ".css": ("CSS", "Стили"),
    ".js": ("JavaScript", "Исходный код"),
}

#: Fallback per file_type when the extension is not in FILE_DESC.
TYPE_FALLBACK: dict[str, tuple[str, str]] = {
    "chat_export": ("Чат", "Экспортированный чат"),
    "text": ("Текст", "Текстовый файл"),
    "document": ("Документ", "Документация"),
    "image": ("Изображение", "Изображение"),
    "cad": ("CAD", "CAD-чертёж"),
    "spreadsheet": ("Таблица", "Таблица данных"),
    "archive": ("Архив", "Архив"),
    "binary": ("Бинарный", "Бинарный файл"),
    "folder": ("Папка", "Каталог"),
    "unknown": ("Файл", "Файл"),
}


def is_derived(name: str) -> bool:
    """True for card-derived artifacts (notes/review files)."""

    low = (name or "").lower()
    return low.endswith(DERIVED_SUFFIXES)


def file_type_desc(file_name: str, file_type: str) -> tuple[str, str]:
    """(Тип, Описание) for the files table — by name/extension/pattern (§14)."""

    ext = Path(file_name).suffix.lower()
    if file_name.startswith("test_") and ext == ".py":
        return ("Python-тест", "Автоматизированный тест")
    if ext in FILE_DESC:
        return FILE_DESC[ext]
    return TYPE_FALLBACK.get(file_type or "unknown", ("Файл", "Файл"))


def dir_purpose(dir_name: str, child_types: list[str]) -> str:
    """Purpose line: known pattern first, content heuristic as fallback (§12)."""

    known = KNOWN_DIR_PATTERNS.get((dir_name or "").lower())
    if known:
        return known
    if not child_types:
        return "Пустой каталог."
    from collections import Counter

    top = Counter(child_types).most_common(3)
    lead, lead_n = top[0]
    total = len(child_types)
    group = TYPE_GROUP.get(lead, "файлы")
    if lead_n == total and len(top) == 1:
        line = f"Каталог содержит {group}."
    else:
        line = f"Каталог содержит преимущественно {group} ({lead_n} из {total})."
    # Cheap test-layout hint on top of the generic line.
    names_hint = "test_" in dir_name.lower() or dir_name.lower() in ("test", "tests")
    if names_hint and lead in ("text", "chat_export", "unknown"):
        return "Каталог содержит автоматизированные тесты Python-проекта."
    return line


@dataclass
class OverviewFile:
    name: str
    rel: str
    type: str
    desc: str
    derived: bool = False


@dataclass
class OverviewDir:
    rel: str
    name: str
    purpose: str
    files: list[OverviewFile] = field(default_factory=list)


@dataclass
class Overview:
    root_name: str
    root: str
    dirs: list[OverviewDir] = field(default_factory=list)
    total_files: int = 0
    total_folders: int = 0
    character: str = ""


def structural_closure(rels) -> set[str]:
    """Selected rels plus every parent chain up to the root (``""``).

    Shared by the builder and ``UIApp.overview_build`` so the card and the
    response counters agree on the folder skeleton (M5 follow-up).
    """

    selected = set(rels or [])
    structural = set(selected)
    for rel in selected:
        cur = rel
        while "/" in cur:
            cur, _, _ = cur.rpartition("/")
            structural.add(cur)
        if rel != "":
            structural.add("")
    return structural


def build_overview(index, rels: list[str]) -> Overview:
    """Assemble the Overview DTO from a SourceIndex + filtered rels.

    Only names/extensions/structure are used — file bodies are never read.
    ``rels`` comes from ``UIApp.overview_build`` (exact rels + subtrees).

    Ragged selections (M5): the choice may hold a file whose parent folder
    is not selected (``selection=["src/main.py"]``). The builder splits:

    - ``selected`` — the user's exact choice; the ONLY source of files in
      the tables (neighbours are never pulled in);
    - ``structural`` — ``selected`` plus every parent chain up to the root
      (``""``, see ``structural_closure``); the folder skeleton of the card
      (``dirs``).

    Counters: ``total_files`` counts selected files, ``total_folders``
    counts structural folders shown in the card.
    """

    from .source_index import Kind

    selected = set(rels or [])
    structural = structural_closure(selected)

    root_name = Path(index.root).name or index.root
    by_rel = {item.rel_posix: item for item in index.items.values()}

    # Direct file children per folder — from SELECTED files only, so a
    # structural parent never drags in unselected neighbours.
    children_files: dict[str, list] = {}
    for rel in selected:
        item = by_rel.get(rel)
        if item is None or item.kind != Kind.FILE:
            continue
        parent, _, _ = rel.rpartition("/")
        children_files.setdefault(parent, []).append(item)

    dirs: list[OverviewDir] = []
    total_files = sum(
        1
        for rel in selected
        if (item := by_rel.get(rel)) is not None and item.kind == Kind.FILE
    )
    total_folders = sum(
        1
        for rel in structural
        if (item := by_rel.get(rel)) is not None and item.kind == Kind.FOLDER
    )
    for rel in sorted(structural, key=lambda r: (r != "", r.casefold())):
        item = by_rel.get(rel)
        if item is None:
            continue
        if item.kind == Kind.FOLDER:
            name = rel.split("/")[-1] if rel else root_name
            kids = sorted(
                children_files.get(rel, []), key=lambda i: i.rel_posix.casefold()
            )
            heur_types = [
                k.file_type for k in kids if not is_derived(k.rel_posix.split("/")[-1])
            ]
            files = []
            for k in kids:
                fname = k.rel_posix.split("/")[-1]
                derived = is_derived(fname)
                ftype, fdesc = (
                    ("Служебный", "Производный файл карточки")
                    if derived
                    else file_type_desc(fname, k.file_type)
                )
                files.append(
                    OverviewFile(
                        name=fname, rel=k.rel_posix, type=ftype,
                        desc=fdesc, derived=derived,
                    )
                )
            dirs.append(
                OverviewDir(rel=rel, name=name, purpose=dir_purpose(name, heur_types), files=files)
            )

    character = _character_line(by_rel, selected)
    return Overview(
        root_name=root_name, root=index.root, dirs=dirs,
        total_files=total_files, total_folders=total_folders, character=character,
    )


def _character_line(by_rel: dict, selected: set[str]) -> str:
    from collections import Counter

    from .source_index import Kind

    cnt: Counter[str] = Counter()
    for rel in selected:
        item = by_rel.get(rel)
        if item is None or item.kind != Kind.FILE:
            continue
        fname = rel.split("/")[-1]
        if is_derived(fname):
            continue
        cnt[item.file_type] += 1
    if not cnt:
        return "Файлов для описания нет."
    parts = [f"{TYPE_GROUP.get(ft, 'файлы')}: {n}" for ft, n in cnt.most_common(3)]
    return "Общий характер содержимого — " + ", ".join(parts) + "."


def _structure_lines(dirs: list[OverviewDir]) -> list[str]:
    """Indented folder list for the card (one line per selected folder)."""

    lines = []
    for d in dirs:
        if not d.rel:
            lines.append("./")
            continue
        depth = d.rel.count("/") + 1
        lines.append("  " * depth + d.rel.split("/")[-1] + "/")
    return lines


def _wiki(rel: str, label: str) -> str:
    safe = (rel or "").replace("[", "").replace("]", "").replace("|", "/")
    text = (label or "").replace("[", "").replace("]", "").replace("|", "/")
    return f"[[{safe}|{text}]]" if safe != text else f"[[{safe}]]"


def render_overview_markdown(ov: Overview, now: str | None = None) -> str:
    """Render the DTO as a Markdown card.

    Files link via ``[[wiki]]`` (``[[rel/path|name]]``, same syntax family
    as the v5 cards). Folders are plain code spans with their relative path
    (M6): a ``[[folder]]`` link would not open the folder in Obsidian
    (there is no such note), so no fake-navigable links are emitted —
    see the §15 limitation note in the ticket.

    ``now`` is an explicit substitution point for the ``generated`` stamp
    (principle №8: deterministic output); defaults to the current time.
    """

    stamp = now if now is not None else datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [
        "---",
        "type: overview",
        f"title: {ov.root_name} — общий обзор",
        f"generated: {stamp}",
        f"folders: {ov.total_folders}",
        f"files: {ov.total_files}",
        "---",
        "",
        f"# {ov.root_name} — общий обзор",
        "",
        "Структурный обзор выбранного дерева (без LLM): структура каталогов, "
        "базовые описания и таблицы файлов.",
        "",
        "## Структура",
        "",
        "```text",
        *[line for line in _structure_lines(ov.dirs)],
        "```",
        "",
        "## Содержимое",
        "",
        ov.character,
        "",
    ]
    for d in ov.dirs:
        if d.rel:
            out.append(f"### `{d.rel}/`")
        else:
            out.append(f"### {d.name}/ (корень)")
        out.append("")
        out.append(f"Назначение: {d.purpose}")
        out.append("")
        if d.files:
            out.append("Файлы:")
            out.append("")
            out.append("|Файл|Тип|Описание|")
            out.append("|---|---|---|")
            for f in d.files:
                out.append(f"|{_wiki(f.rel, f.name)}|{f.type}|{f.desc}|")
            out.append("")
        else:
            out.append("Файлов нет.")
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def overview_output_dir(enriched_root: Path) -> Path:
    """Output dir for the structural overview (outside source, next to topics)."""

    return Path(enriched_root) / "overview"


def overview_filename(root_name: str) -> str:
    """Card file name — deliberately distinct from ``<папка>_обзор.md``."""

    from .topics import sanitize_name

    stem = sanitize_name(f"{root_name} структура")
    return f"{stem}.md"


def write_overview(output_dir: Path, root_name: str, markdown: str) -> Path:
    """Atomically write the card (``.tmp`` → ``os.replace``)."""

    from .emit import atomic_write

    target = Path(output_dir) / overview_filename(root_name)
    atomic_write(target, markdown)
    return target
