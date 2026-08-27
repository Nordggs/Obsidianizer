# Obsidianizer

| [Русский](README.ru.md) | **English** |

Local preprocessor that structures raw materials and prepares them for
[Obsidian](https://obsidian.md).

Point it at a folder of raw files (Markdown chat exports today, TXT/SVG/scripts
later). Stage 1 extracts metadata, copies referenced media and writes a clean,
self-contained import folder. An optional stage 2 then enriches each note with
a summary and tags via a local LLM (Ollama) into a second, ready-to-drop vault.

## Why

Working folders accumulate raw exports that are fine as-is but hard to browse
inside Obsidian. Obsidianizer wraps each raw file with a YAML frontmatter block
and a "business card" (summary, tags, dates, statistics) while keeping the
original content untouched — and leaves no trace in the source folder.

```
raw notes ──> Obsidianizer ──> processed/ ──> enriched/ ──> Obsidian vault
                 │                │             │
                 │                │             └── AI stage returns here
                 └── no LLM here  └── import result
 neighboring: local LLM (Ollama) runs only in the AI stage
```

Two physically separate output folders:

- `processed/` — import result: metadata + media, **never** touched by LLM.
- `enriched/` — AI stage result: import + LLM summary/tags/topic/type, plus
  topic notes (`enriched/topics/*.md`) that merge several chats into a single
  knowledge card. **This is the folder to open in Obsidian.** Local media is
  copied here, so the vault stays self-contained.

Incremental by design: an enriched note is skipped when its `ai_hash` matches
the processed `source_hash`, so re-running the AI stage only calls Ollama for
new or changed files ("145 skipped, 10 processed"), and a plain re-import can
never destroy AI results.

## Safety contract

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

See `docs/architecture.md` and `AGENTS.md` for the full contract.

## Install

Requires Python 3.10+.

```bash
pip install .            # or: pip install -e .[dev] for development
```

Or run without installing:

```bash
python -m obsidianizer --help
```

## Quick start

```bash
# 1. Local config (optional overrides)
cp config.example.yml config.yml

# 2. Dry run — preview what would happen
obsidianizer --source ./raw --target ./processed --dry-run

# 3. Real run (imports; then AI stage if the LLM is enabled)
obsidianizer --source ./raw --target ./processed --enriched ./enriched

# 4. Later, only new/changed files changed the vault:
obsidianizer ai --target ./processed --enriched ./enriched

# 5. Remove files that are no longer produced (only Obsidianizer-owned ones):
obsidianizer --source ./raw --target ./processed --prune

# 6. Remove orphaned AI results (deleted chats leave no ghosts):
obsidianizer ai --target ./processed --enriched ./enriched --prune-enriched
```

### CLI options

```
--source DIR      source folder (overrides config)
--target DIR      processed folder (overrides config)
--enriched DIR    AI-enriched folder (overrides config)
--model NAME      Ollama model to use for summary/tags
--no-llm          skip LLM enrichment entirely
--dry-run         compute and report, but write nothing
--prune           delete Obsidianizer-owned files no longer produced
--prune-enriched  on the AI stage, delete orphaned enriched files + their media
```

The `folders` subcommand builds project cards (see
[Folder Obsidianizer](#folder-obsidianizer)):

```bash
# full tree
obsidianizer folders --path "D:\Projects\DemoProject" --force

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

## Graphical interface (primary)

The desktop GUI is the main way to work with Obsidianizer. No package
installation is needed — launch it straight from the repo root:

```bash
# Windows: double-click Obsidianizer.bat (uses .venv if present), or:
python Obsidianizer.py
```

Diagnostics without opening a window:

```bash
python Obsidianizer.py --check
```

The window has three tabs:

- **📁 Obsidianize** — folder project cards (see below);
- **💬 Чат-обработка** — the raw → processed → enriched pipeline described
  below: pick source/processed/enriched folders, toggle Ollama, press
  **Обработать** (pure import, the LLM is never called). AI work is always an
  explicit action: the per-chat **AI-постобработка** button,
  **Объединить в тему…** to merge the selected chats into one
  `enriched/topics/<Name>.md` note (the model names it, and an unchanged chat
  set skips the call), or **Авто-группировка** to cluster the whole processed
  collection into topics automatically;
- **🤖 AI-анализ** — local LLM review of a folder's contents, written next to
  the card as `<folder>_обзор.md` and embedded into the card's AI Review
  section.

The window shows a progress bar, the current file, a color-coded log and a
final summary. **Стоп** stops the batch after the current file finishes — an
in-flight Ollama request is never interrupted. The CLI remains available for
automation.

## Folder Obsidianizer

The Obsidianize tab (or `obsidianizer folders`) generates a GitHub-style
**project card** for every folder of a working tree: `<folder>.md` next to
the folder's files. Sections: navigation, `Folders` (subfolder table with
real aggregates), `Files` (one table: type, opens-with, modified, size,
comment), `About` (project fields), `Gallery`, `Images`, `AI Review`,
`Notes`.

Every card comes with a paired **`<folder>_заметки.md`** notes file — the
only user-editable layer (client, address, designer, comments…). Obsidianizer
never rewrites the notes; editing them marks the card stale so the next
update rebuilds the rendered part with fresh About data.

Full card structure, templates and the user flow live in
[docs/obsidianize.md](docs/obsidianize.md) (RU: `docs/obsidianize.ru.md`).

## Obsidian integration (Templater hotkey)

One hotkey refreshes the card of the folder you are standing in:

1. Copy `obsidian/templater/Obsidianizer Update.md` into your Templater
   templates folder (Settings → Templater → Template folder location).
2. Inside the template, replace the `cli` path with the path to your
   `obsidianizer-cli.bat` (repo root; adjust if you moved the repo).
3. Bind a hotkey (e.g. `Alt+3`) in Settings → Hotkeys →
   "Obsidianizer Update".

On success you get a Notice with self-diagnostics: `Gallery ✓/✗ · Images ✓/✗`.
The template runs the CLI against the note's folder only (`--no-recursive`)
with `--adopt --vault-root --rel`, so a single press updates exactly one card
and never touches sibling cards. The same result is available without
Templater via the "Shell commands" plugin — both variants are described in
[obsidian/obsidianizer-refresh.md](obsidian/obsidianizer-refresh.md).

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