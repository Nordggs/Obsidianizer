# Folder Obsidianizer (`obsidianizer folders`)

Creates "live" Markdown project cards for any folder: every folder gets a
`<folder name>.md` card that looks like a small local GitHub repository
(header, navigation, subfolder table, file table, project card, gallery,
AI review, embedded working notes).

Two templates, **the same structure**:

- **`github`** (default) — adds `cssclasses: [github-dashboard]` so the
  bundled CSS snippet can style the card as a GitHub page (optional).
- **`classic`** — identical structure, but without `cssclasses` (plain
  Markdown, works without the snippet).

**User data model** — the card is fully regenerable; user data lives outside
it:

| Data | Where it lives | What the generator does |
|---|---|---|
| Project files (facts) | the folder itself | scanned read-only |
| User fields (проект, источник, контакт, комментарий, …) | `<folder>_заметки.md` frontmatter | **never overwritten****; edited notes mark the card stale so the next update renders fresh About data |
| Working notes body | `<folder>_заметки.md` | created once, **never replaced**, embedded via `![[…_заметки]]` |
| AI review | `<folder>_обзор.md` | embedded when present |

**Core contract — the folder is fully read-only**: the scanner only reads
names, sizes, dates and extensions. The only files ever created or modified
are the `.md` cards and the derived files (`*_заметки.md`, `*_обзор.md` are
never treated as user project files). Source files are never renamed, moved,
deleted or converted.

## Card structure (renderer version 8)

```markdown
---
obsidianizer: true           # marker of our card
obsidianizer_hash: a1b2c3…   # folder fingerprint (freshness; basis-independent)
obsidianizer_template: github
obsidianizer_version: 8      # renderer version (bump → auto migration)
cssclasses: [github-dashboard]   # github template only
---

# <Folder Name>

Автоматическая карточка каталога

Local project · N файлов · M папок · SIZE
Updated DD Mon YYYY

[[#Folders|Folders]] | [[#Files|Files]] | [[#About|About]] | [[#Gallery|Gallery]]
| [[#Images|Images]] | [[#AI Review|AI Review]] | [[#Notes|Notes]]

## Folders
| Name | Files | Size | Updated |
| ⬆ [[../Parent\|Up]] |  |  |  |        (sub-folder cards only)
| 📁 [[Sub/Sub\|Sub]] | 3 | 1.2 MB | сегодня |

## Files
| File | Type | Opens with | Modified | Size | Comment |
| 📊 [[report.xlsx]] | XLSX | Excel | 18.01.2026 | 1.5 MB |  |

## About                                     (only with user fields present)
> [!info] 📋 Карточка проекта
> - **Проект**: ООО Ромашка
> - **Контакт**: Татьяна

## Gallery                                   (direct images + known vault path)
```img-gallery
path: <vault-relative path to the folder>
type: vertical
columns: 4
```

## Images                                    (direct images of THIS folder only)
> [!example]- Images · N изображений · SIZE
> ![фото.png](./фото.png)

## AI Review                                 (only when <Folder>_обзор.md exists)
![[Folder_обзор]]

## Notes
![[Folder_заметки]]                          (embedded; user-owned file)

<footer class="repo-meta">Updated DD.MM.YYYY HH:mm · N файлов · M папок · SIZE</footer>
<!-- obsidianizer-manifest: {"base": …, "files": {…}, "folders": […], "notes": "…"} -->
```

Section semantics:

- **Folders** — physical subfolders with real aggregates (file count, subtree
  size, latest change). The first row is the `⬆ Up` link (sub-folder cards
  only; the alias pipe is escaped `\|` so the table keeps 4 cells).
- **Files** — one GitHub-style table of the folder's direct files, sorted by
  extension then name. `Opens with` is a per-extension label (Obsidian,
  AutoCAD, Excel, Word, Revit, SketchUp, CAD, `—`).
- **About** — a plain Obsidian callout projected from the notes frontmatter
  (readable in PDF export and non-Obsidian viewers). Hidden when no user
  fields are set.
- **Gallery** — an interactive `img-gallery` block, generated only for direct
  images when a vault path is known (`--vault-root`, or `--gallery-prefix`
  for projects outside the vault, or an auto-detected vault root in the GUI).
- **Images** — an archive callout of the folder's **direct images only**
  (not recursive). Nested folders have their own cards with their own
  Images section. Relative, URL-encoded links (`./подпапка/фото%20файл.png`)
  work in any viewer.
- **AI Review** — embed of `<folder>_обзор.md` (AI-анализ tab) when present.
- **Notes** — embed of the user-owned `<folder>_заметки.md`.

The trailing HTML comment is the hidden change-detection manifest (never
rendered by Obsidian) — see [Technical notes](#technical-notes).

## User flow (from zero)

1. **Open the GUI** (`Obsidianizer.bat`) → tab **📁 Obsidianize**.
2. Pick the folder, leave «Вложенные папки» on, choose the template, press
   **✨ Obsidianize**. Every folder of the tree gets a card and a notes file.
3. **🔍 Просканировать** shows a status table: card state per folder (ok /
   stale / missing / conflict), a one-line summary, and ⚠ change rows
   (добавлен/удалён/изменён файл, структура папок, данные проекта) for stale
   cards. If a foreign note occupies the card name, the row suggests turning
   on **«Принять существующую заметку как заметки»** (`--adopt`).
4. **Edit user data** in `<folder>_заметки.md` (проект, источник, контакт,
   комментарий…). The card is not rewritten — the next scan/update picks the
   changes up.
5. **Update later**: press the Templater hotkey on a card (one card), or run
   Obsidianize from the GUI (whole tree). Unchanged cards are skipped.

### Common situations

**The folder has no card yet — just files.**
Run Obsidianize: the card and the notes file are created automatically. The
scan table shows such a folder as "missing". Until then, working with the
files is not restricted in any way.

**I created a note in Obsidian (say, Ctrl+click on a folder), and
Obsidianizer sees it as foreign.**
A note created by Obsidian itself has no card marker, so the program never
touches it without permission — status "conflict". In the GUI, turn on
"Принять существующую заметку как заметки": the note is renamed into
`<folder>_заметки.md` (content preserved 1:1) and a fresh card appears in its
place. The Templater hotkey and the Shell command do this automatically —
adoption is always enabled there.

**Will the program overwrite my notes?**
No. `<folder>_заметки.md` is created once and never rewritten afterwards.
Even foreign-note adoption only fires while the notes file does not exist.

**I changed the project fields in the notes.**
The card is honestly marked stale, and the next update rebuilds the About
section with the new data. The file sections stay accurate: no phantom
"added/removed" lines for unchanged files.

**Can I skip updating for now?**
Yes: a card is a dashboard, not a mandatory ritual. Unrefreshed cards are
flagged in the scan table and wait their turn.

**I never ran Obsidianizer at all.**
Then the program has written nothing: all your files are in their original
state. There are no background processes — everything happens only on an
explicit launch.

## Refreshing a card from Obsidian

### Templater hotkey (primary)

1. Copy `obsidian/templater/Obsidianizer Update.md` into your Templater
   templates folder.
2. Replace the `cli` path inside with the path to your
   `obsidianizer-cli.bat` (repo root).
3. Bind a hotkey (e.g. `Alt+3`) → Settings → Hotkeys → "Obsidianizer Update".

The template runs the CLI against the open note's folder only:

```
obsidianizer-cli.bat folders --path "<folder>" --no-recursive --adopt
    --vault-root "<vault>" --rel "<vault-relative folder>"
```

`--no-recursive` writes **only this folder's card** (siblings untouched) while
still computing real subfolder aggregates; `--rel` keeps the `⬆ Up` link
correct; `--adopt` converts a foreign note into the notes file once. On
success a Notice reports self-diagnostics: `Gallery ✓/✗ · Images ✓/✗`.

### Option 2 — "Shell commands" plugin

Create a command (Settings → Shell commands → New command):

- Name: `Obsidianizer: обновить карточку`
- Command:
  `"C:\path\to\obsidianizer-cli.bat" folders --path "{{folder_path}}" --no-recursive --adopt --vault-root "{{vault_path}}"`

Run it from the command palette or bind a hotkey. Both variants are
documented in [obsidian/obsidianizer-refresh.md](../obsidian/obsidianizer-refresh.md).

## CLI

```bash
obsidianizer folders --path "D:\Projects\MyProject"                 # full tree
obsidianizer folders --path "D:\Projects\MyProject" --dry-run       # report only
obsidianizer folders --path "D:\Projects\MyProject" --force         # rebuild all cards
obsidianizer folders --path "D:\Projects\MyProject" --no-recursive  # root card only
obsidianizer folders --path "D:\Projects\MyProject" --no-gallery    # no Gallery block
obsidianizer folders --path "D:\Projects\MyProject" \
    --vault-root "D:\Obsidian\Vault"                              # vault paths for Gallery
obsidianizer folders --path "D:\Projects\MyProject" \
    --gallery-prefix "PROJECT/OBSIDIAN/Objects"             # projects outside the vault
obsidianizer folders --path "D:\Projects\MyProject" --template classic
obsidianizer folders --path "D:\Projects\MyProject" --adopt         # adopt foreign notes
```

## GitHub look (CSS snippet, optional)

The `github` template emits `cssclasses: [github-dashboard]`. To make cards
look like a GitHub page, install the bundled snippet once:

1. Copy `obsidian/github-dashboard.css` into
   `<vault>/.obsidian/snippets/github-dashboard.css`.
2. Obsidian → Settings → Appearance → **CSS snippets** → toggle
   `github-dashboard` on.

Without the snippet the cards stay plain Markdown — nothing breaks.

## Behaviour notes

- A card is regenerated when file names/sizes/dates change, when the
  `<folder>_обзор.md` review appears/disappears, when user notes fields
  change, when the template differs from `--template`, or when
  `obsidianizer_version` is older than the renderer's (migration).
- `<folder>_заметки.md` is created once (as a template, or with the old
  in-card manual block migrated into it) and is **never overwritten** after
  that. A foreign note occupying the card name is skipped (conflict) unless
  `--force` / `--adopt` is used.
- `--no-recursive` writes only the root card but computes aggregates over the
  **full subtree** — the Folders table shows real values, identical to a
  recursive GUI run (GUI and hotkey produce the same card).
- `*_заметки.md`, `*_обзор.md` and cards are derived artifacts: never scanned
  into project stats/tables. Hidden folders and `.obsidian`, `.git`,
  `node_modules`, `__pycache__` are skipped.
- Re-running without changes writes nothing (fingerprint comparison).

## Technical notes

Change detection must work no matter which root a scan was made from (GUI
scans a project root, the Templater hotkey scans the card folder itself), so
everything is keyed in the **card-folder basis**:

- `folder_fingerprint` hashes card-relative paths (`F:<rel>·<size>·<mtime>`,
  subfolders, review presence) — identical for the same folder scanned from
  any root.
- The manifest stores card-relative file keys plus a `base` field (the card
  folder's rel from the update root, for diagnostics).
- `card_diff` aligns legacy manifests (no `base`) with a deterministic
  prefix rule: if any stored key carries the scan-root prefix, the prefix is
  stripped from every key — no heuristics.
- `obs_scan` logs every stale-card diff (root, folder rel, added/removed/
  changed counters) to `obsidianizer.log` for post-mortem checks.
