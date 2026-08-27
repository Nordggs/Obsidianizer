# Folder Obsidianizer (`obsidianizer folders`)

Creates "live" Markdown catalog cards for any folder: every folder gets a
`<folder name>.md` card that looks like a small local GitHub repository
(header, nav, repository tree, README, AI review, embedded working notes).

Two templates with **the same structure**:

- **`github`** (default) — *Project Dashboard v2*: adds
  `cssclasses: [github-dashboard]` so the bundled CSS snippet can style the
  card as a GitHub page (optional).
- **`classic`** — identical structure, but without `cssclasses` (plain
  Markdown, works without the snippet).

**User data model** — the card is fully regenerable; user data lives outside
it:

| Data | Where it lives | What the generator does |
|---|---|---|
| Project files (facts) | the folder itself | scanned read-only |
| User frontmatter keys (клиент, телефон, карта, …) | card frontmatter | **never overwritten**, defaults only for missing standard fields |
| Working notes | separate `<folder>_заметки.md` | created once, **never replaced**, embedded via `![[…_заметки]]` |
| AI review | separate `<folder>_обзор.md` | embedded when present |

**Core contract — the folder is fully read-only**: the scanner only reads
names, sizes, dates and extensions. The only files ever created or modified
are the `.md` cards and the derived notes files (`*_заметки.md`,
`*_обзор.md` are never treated as user project files). Source files are
never renamed, moved, deleted or converted.

## Card contents (v2 — both templates)

```markdown
---
дата_начала: 2026-03-16
...                          # ALL user keys preserved (incl. unknown, multi-line lists)
obsidianizer: true           # marker of our card
obsidianizer_hash: a1b2c3…   # folder fingerprint (freshness)
obsidianizer_template: github
obsidianizer_version: 2      # renderer version (bump → auto migration)
cssclasses: [github-dashboard]   # github template only
---

# 🏗️ <Folder Name>

> <comment from frontmatter>              (or "Автоматическая карточка каталога")
> ◉ Local project · N файлов · 52.2 MB · M папок     (compact meta line)
[[../Parent|↑ Родитель]]                 (sub-folders only)

[[#📂 Code|Code]] · [[#🤖 AI Review|AI Review]] · [[#✍️ Рабочие заметки|Notes]]   (nav)

<div class="github-langbar">…</div>       (proportional category bar, flex-grow)

## 📂 Code                                (repository tree, 4 columns)
| Файл | Комментарий | Изменено | Размер |
| 📁 [[Sub/Sub|Sub]] · N файлов |  | сегодня | 1.2 MB |
| 📐 Чертежи · 2 файла |  | вчера | 3.4 MB |     (categories as virtual rows)

## 📖 README

### 📋 О проекте                          (non-empty fields only; user keys too)
| Поле | Значение |
| Клиент | ООО Ромашка |

### 📐 Чертежи                            (category headers have no extensions)
_2 файла · 3.4 MB · изменено вчера_
| Файл | Комментарий | Изменено | Размер |

### 🖼️ Галерея проекта                    (only with --vault-root)
    img-gallery block

## 🤖 AI Review                           (only when <Folder>_обзор.md exists)
![[Folder_обзор]]

## ✍️ Рабочие заметки
![[Folder_заметки]]                       (embedded; the file itself is user-owned)

_Обновлено: DD.MM.YYYY HH:mm_
```

- Sizes and latest-change info are **aggregates only** (folders/categories);
  the meta line shows the subtree totals.
- The repository tree covers folders + categories; category tables in README
  show only direct files.

## GitHub look (CSS snippet, optional)

The `github` template emits `cssclasses: [github-dashboard]` in the
frontmatter. To make the card actually *look* like a GitHub page, install the
bundled snippet once:

1. Copy `obsidian/github-dashboard.css` into your vault's snippet folder:
   `<vault>/.obsidian/snippets/github-dashboard.css`.
2. Obsidian → Settings → Appearance → **CSS snippets** → toggle
   `github-dashboard` on.

The snippet styles: repo header, compact meta line, nav pills, the category
"language bar" (GitHub colors), the `📂 Code` tree in a file-list frame,
GitHub-style tables (monospace names, muted date/size columns, hover), muted
meta lines and framed embedded notes/review blocks. Without the snippet the
cards stay plain Markdown — nothing breaks.

- Links are **relative** (`[[file.xlsx]]`), so the folder can be moved
  between directories and vaults without breaking links.
- Sub-folder cards carry `[[../Parent|↑ Родитель]]`.
- Table comments survive updates; user frontmatter keys (unknown ones
  included) are carried over untouched.
- A card is recognized by `obsidianizer: true`. A foreign note named like the
  folder is **never overwritten** (use `--force`).
- Re-running without changes writes nothing (compared via
  `obsidianizer_hash`).

## CLI

```bash
obsidianizer folders --path "D:\Projects\DemoProject"            # create/refresh cards
obsidianizer folders --path "D:\Projects\DemoProject" --dry-run  # report only
obsidianizer folders --path "D:\Projects\DemoProject" --force    # overwrite a foreign note
obsidianizer folders --path "D:\Projects\DemoProject" --no-recursive
obsidianizer folders --path "D:\Projects\DemoProject" --no-gallery
obsidianizer folders --path "D:\Projects\DemoProject" --vault-root "D:\Obsidian\Vault"
obsidianizer folders --path "D:\Projects\DemoProject" --template classic  # same structure, no cssclasses
```

## Refreshing a card from Obsidian

### Option 1 — "Shell commands" plugin (recommended)

1. Install the **Shell commands** plugin.
2. Create a command (Settings → Shell commands → New command):
   - Name: `Obsidianizer: обновить карточку`
   - Command: `obsidianizer folders --path "{{folder_path}}"`
3. Run it from the command palette (or bind a hotkey) — the current folder
   card and all sub-folder cards are refreshed.

### Option 2 — Templater wrapper (button/script inside a note)

```js
<%*
// Refresh the Folder Obsidianizer card for the current folder.
// Requires the "Shell commands" plugin with command id = obsidianizer-update
// (command: obsidianizer folders --path "{{folder_path}}").
const tFile = tp.file.find_tfile(tp.file.path(true));
if (!tFile) return;
const vaultRoot = app.vault.adapter.getBasePath ? app.vault.adapter.getBasePath() : "";
const folderAbs = vaultRoot + "/" + tFile.parent.path;
app.openUrl("obsidian://shellcommands?execute=obsidianizer-update");
new Notice("Обновление карточки: " + folderAbs);
%>
```

> In the Shell commands plugin the command id is set manually in the "Id"
> field (e.g. `obsidianizer-update`) — put the same id into the script.

## Behaviour notes

- A card is regenerated when file names/sizes/dates in the folder change,
  when the `<folder>_обзор.md` review appears/disappears, when the card
  template differs from `--template`, or when `obsidianizer_version` is older
  than the renderer's (migration; frontmatter keys and table comments are
  preserved).
- Working notes: `<folder>_заметки.md` is created once — as a template, or
  with the old in-card manual block migrated into it (the block is consulted
  as a migration source only while the notes file does not exist). Existing
  notes files are **never overwritten**.
- `*_заметки.md` and `*_обзор.md` are derived obsidianizer artifacts: never
  scanned into project stats/tables, whatever `include_md` says.
- Hidden folders and `.obsidian`, `.git`, `node_modules`, `__pycache__` are
  skipped; `.md` files are not catalogued (cards are a separate layer).
- The `img-gallery` block is generated only with `--vault-root` (its path must
  be vault-relative); images are always listed in a table.