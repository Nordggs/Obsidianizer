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

Pick source, processed (`Результат обработки`) and enriched (`AI-результат`)
folders with the native pickers, toggle Ollama, choose a model, optionally
enable dry-run / prune / `Удалять сироты из AI-результата`, then press
**Обработать** — this is a pure import, the LLM is never called. AI work is
always an explicit action: the per-chat **AI-постобработка** button,
**Объединить в тему…** to merge the selected chats into one
`enriched/topics/<Name>.md` note (the model names it, and an unchanged chat
set skips the call), or **Авто-группировка** to cluster the whole processed
collection into topics automatically. The window shows a progress bar, the
current file, a color-coded log and a final summary. **Стоп** stops the batch
after the current file finishes — an in-flight Ollama request is never
interrupted. The CLI remains available for automation.

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