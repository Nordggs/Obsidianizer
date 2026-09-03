# Obsidianizer

| [Русский](README.ru.md) | **English** |

Obsidianizer is a local tool that prepares project materials for working in
[Obsidian](https://obsidian.md) — and keeps working with them afterwards.

Point it at an ordinary project folder (drawings, PDFs, documents, images,
spreadsheets — no Markdown required) and it builds a structured, navigable
system of cards that opens comfortably in your vault. It also processes
exported AI chats into clean, enriched Markdown notes, and connects a local
LLM (Ollama) for searching, reviewing and summarizing your materials.

## Three tools in one app

| # | Function | What it does |
|---|---|---|
| ① | **Obsidianize** — the primary tool | Turn ordinary project folders and files into a comfortable Obsidian-ready structure: cards, navigation, galleries |
| ② | **Chat Processing** | Process exported chats and other Markdown materials |
| ③ | **AI Analysis** | Search, analyze and summarize materials in any folder you point it at |

## Install

Requires Python 3.10+.

```bash
pip install .            # or: pip install -e .[dev] for development
```

Or run without installing:

```bash
python -m obsidianizer --help
```

Portable Windows build: download `Obsidianizer-x.y.z-win64.zip` from
[Releases](https://github.com/Nordggs/Obsidianizer/releases), unpack and run
`Obsidianizer.exe`.

**Shortcut icon**: `Obsidianizer.ico` lives in the repo root — create a
shortcut to `Obsidianizer.bat`, then right-click the shortcut → Properties →
Change Icon → point it at `Obsidianizer.ico`.

## Graphical interface (primary)

The desktop GUI is the main way to work with Obsidianizer — launch it straight
from the repo root:

```bash
# Windows: double-click Obsidianizer.bat (uses .venv if present), or:
python Obsidianizer.py
```

Diagnostics without opening a window:

```bash
python Obsidianizer.py --check
```

The window has three tabs, matching the three tools:

- **📁 Obsidianize** — project cards ([① below](#-obsidianize--project-cards));
- **💬 Chat Processing** — export processing ([② below](#-chat-processing--exported-chats));
- **🤖 AI Analysis** — local LLM analysis ([③ below](#-ai-analysis--work-with-your-materials)).

The window shows a progress bar, the current file, a color-coded log and a
final summary. **Стоп** stops the batch after the current file finishes — an
in-flight Ollama request is never interrupted. The CLI remains available for
automation.

The floating Help window (the **?** button at the right of the tab bar)
explains the active tab in plain language, and its **🔗 Integration** tab
shows the live Obsidian integration status.

## ① Obsidianize — project cards

**The primary tool.** Give it an ordinary project folder:

```
Проект/
├── DWG/
├── PDF/
├── DOC/
├── изображения/
└── прочие файлы
```

…and it turns it into an Obsidian-ready structure: a GitHub-style **project
card** (`<folder>.md`) for every folder of the tree, with navigation,
`Folders` (subfolder table with real aggregates), `Files` (one table: type,
opens-with, modified, size, comment), `About` (project fields), `Gallery`,
`Images`, `AI Review`, `Notes`.

Every card comes with a paired **`<folder>_заметки.md`** notes file — the
only user-editable layer (project, address, contact, comments…). Obsidianizer
never rewrites the notes; editing them marks the card stale so the next
update rebuilds the rendered part with fresh About data.

The Obsidianize tab: **🔍 Просканировать** scans read-only and reports the
state of every card (ok / stale / missing / conflict) with ⚠ change details;
**✨ Obsidianize** creates and updates cards; **🔗 Obsidian Integration**
connects Obsidian (see [below](#obsidian-integration-templater-hotkey)).

### Common situations

**The folder has no card yet — just files.**
No problem: run Obsidianize — the card and the notes file will appear
automatically. Until then nothing is blocked, the files work as usual.

**I accidentally created a note in Obsidian (say, Ctrl+click on a folder),
then ran Obsidianizer.**
Nothing is lost. The program sees "this note is not mine" and never touches
it without your permission — the scan table will hint what to do. Turn on
"Принять существующую заметку как заметки" (or simply refresh the card with
the hotkey — it is already enabled there): your note becomes the notes file,
and a fresh card appears in its place.

**Will the program ever overwrite my notes?**
No. The `<folder>_заметки.md` file is your personal territory: the program
creates it once and never rewrites it afterwards, no matter what happens.

**I changed the project fields in the notes (project, contact…).**
Edit the notes — the next update will honestly mark the card as
"needs update" and pick up the new data. No phantom "file added/removed"
lines for unchanged files.

**Do I have to update right away?**
No. A card is just a convenient dashboard; update whenever you like, even a
month later. Changed cards will be flagged in the scan table.

**I work in Obsidian and never ran Obsidianizer.**
Your files stay untouched: the program writes nothing until you run it
yourself.

### CLI

```bash
# full tree
obsidianizer folders --path "D:\Projects\MyProject" --force

# single card only (the Templater hotkey does exactly this)
obsidianizer folders --path "<folder>" --no-recursive --adopt \
    --vault-root "<vault>" --rel "<folder-relative-to-vault>"
```

```
folders options
--path DIR            folder to scan (required)
--no-recursive        write only this folder's card; subfolder cards untouched
--adopt               a foreign note named <folder>.md is renamed (1:1) into
                      <folder>_заметки.md, then a fresh card is created
--vault-root DIR      Obsidian vault root — enables the Gallery block
--gallery-prefix TXT  fallback vault path prefix for the gallery when the
                      project lives outside the vault
--rel TXT             vault-relative path of the scanned folder (keeps the
                      "⬆ Up" link correct on single-card updates)
--template NAME       github (Project Dashboard) | classic
--force               rebuild even when nothing changed; overwrite a foreign
                      note only together with --adopt semantics
--dry-run             scan and report, write nothing
```

Full card structure, templates and the user flow live in
[docs/obsidianize.md](docs/obsidianize.md) (RU: `docs/obsidianize.ru.md`).

## ② Chat Processing — exported chats

Chat Processing is designed for **exported chats and other Markdown
materials**: it structures them, extracts metadata, copies referenced media
and optionally enriches every note with a summary and tags via a local LLM
(Ollama).

One of the main scenarios is processing files exported with **AI Chat
Exporter** — a separate app and a separate project:

**[AI Chat Exporter](https://github.com/Nordggs/Project_AI_Base)**
exports chats from ChatGPT, Claude, DeepSeek, Gemini, Qwen and other services
to Markdown →

```
AI Chat Exporter
       ↓  Markdown export
Chat Processing (Obsidianizer)
       ↓  structured material + optional AI enrichment
Obsidian vault
```

Obsidianizer does not require AI Chat Exporter — any Markdown folder works.

### How it works

```
raw notes ──> Obsidianizer ──> processed/ ──> enriched/ ──> Obsidian vault
                 │                │             │
                 │                │             └── AI stage returns here
                 └── no LLM here  └── import result
```

Two physically separate output folders:

- `processed/` — import result: metadata + media, **never** touched by LLM.
- `enriched/` — AI stage result: import + LLM summary/tags/topics, plus
  topic notes (`enriched/topics/*.md`) that merge several chats into a single
  knowledge card. **This is the folder to open in Obsidian.**

Incremental by design: an enriched note is skipped when its `ai_hash` matches
the processed `source_hash`, so re-running the AI stage only calls Ollama for
new or changed files ("145 skipped, 10 processed"), and a plain re-import can
never destroy AI results.

### Safety contract

- **Source is never modified.**
- **Deletions happen only via the ownership manifest.** `--prune` removes only
  files Obsidianizer itself created; foreign files are never touched.
- **Adjacent-stage folders must not overlap** (identical paths or nesting are
  hard errors) — enforced for `(source, target)`, `(target, enriched)` and
  `(source, enriched)`.
- **LLM is optional.** If Ollama is unavailable the files are still processed
  with metadata-only enrichment.
- **AI-stage orphan pruning is marker-gated.** `--prune-enriched` deletes only
  enriched files carrying an `ai_hash` marker whose pair is gone from processed,
  plus media no other note references.

### CLI

```bash
# 1. Local config (optional overrides)
cp config.example.yml config.yml

# 2. Dry run — preview what would happen
obsidianizer --source ./raw --target ./processed --dry-run

# 3. Real run (imports; then AI stage if the LLM is enabled)
obsidianizer --source ./raw --target ./processed --enriched ./enriched

# 4. Later — only new/changed files:
obsidianizer ai --target ./processed --enriched ./enriched

# 5. Remove files no longer produced (only Obsidianizer-owned ones):
obsidianizer --source ./raw --target ./processed --prune

# 6. Remove orphaned AI results (deleted chats leave no ghosts):
obsidianizer ai --target ./processed --enriched ./enriched --prune-enriched
```

```
options
--source DIR      source folder (overrides config)
--target DIR      processed folder (overrides config)
--enriched DIR    AI-enriched folder (overrides config)
--model NAME      Ollama model to use for summary/tags
--no-llm          skip LLM enrichment entirely
--dry-run         compute and report, but write nothing
--prune           delete Obsidianizer-owned files no longer produced
--prune-enriched  on the AI stage, delete orphaned enriched files + their media
```

## ③ AI Analysis — work with your materials

AI Analysis is a local-LLM tool for **intelligent work with materials in any
folder you point it at** — the chat-processing result, your project folders,
a research directory or the whole vault:

- **search** through the materials;
- **review** a folder's contents — the model reads the folder and writes
  `<folder>_обзор.md`, which is embedded into the card's AI Review section;
- **summarize** notes and documents;
- **analyze** several notes together.

Run it from the **🤖 AI Analysis** tab: pick the folder, select subfolders,
optionally include plain-text file contents (more detail, slower), and
generate. Sources are never modified — only the review files are written.

## Obsidian integration (Templater hotkey)

One hotkey refreshes the card of the folder you are standing in. Setup is a
single button — no manual path editing:

1. In the GUI, set the «Папка» field to your Obsidian vault.
2. Press **🔗 Obsidian Integration** — the update template is copied into
   your Templater folder **with the real Obsidianizer path filled in
   automatically**.
3. Bind the hotkey (e.g. `Alt+3`) in Obsidian: Settings → Hotkeys →
   "Obsidianizer Update".

On success you get a Notice with self-diagnostics: `Gallery ✓/✗ · Images ✓/✗`.
The template runs the CLI against the note's folder only (`--no-recursive`)
with `--adopt --vault-root --rel`, so a single press updates exactly one card.

The floating Help window (the «?» button → **🔗 Integration** tab) shows the
live integration status — vault ✓/✗, Templater ✓/✗, template ✓/✗, CLI ✓ —
with an **Install / Repair** button. The frozen EXE doubles as the CLI, so
installed builds need no bat files or PATH entries. Manual setup is still
documented in
[obsidian/obsidianizer-refresh.md](obsidian/obsidianizer-refresh.md).

### About the hotkey

Obsidianizer does **not** assign Obsidian hotkeys automatically and never
edits your vault's `hotkeys.json`. After installing the integration:

1. Open Obsidian → Settings → Hotkeys.
2. Find the Templater command bound to the "Obsidianizer Update" template.
3. Check which key is assigned (e.g. `Alt+3`) or assign one yourself.
4. Open a project card and run the command.

The exact key depends on your vault's configuration — `Alt+3` is just the
example used throughout this documentation.

## Config

`config.yml` is read if present; otherwise defaults from `config.example.yml`
apply. CLI arguments always win.

## Project layout

```
Obsidianizer.py / .bat   launch entry points (double-click to start the GUI)
src/obsidianizer/   package (importable, installable)
tests/              pytest suite (safety-critical paths first)
docs/               architecture + processor contract + development
assets/             placeholder for future GUI assets (.exe, icons)
```

## License

MIT. See [LICENSE](LICENSE).
