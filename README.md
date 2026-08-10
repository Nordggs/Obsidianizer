# Obsidianizer

| [Русский](README.ru.md) | **English** |

Local preprocessor that structures raw materials and prepares them for
[Obsidian](https://obsidian.md).

Point it at a folder of raw files (Markdown chat exports today, TXT/SVG/scripts
later), let it extract metadata, enrich each file with a summary and tags via a
local LLM (Ollama), copy referenced media, and write a clean, self-contained
result folder you can drop straight into your vault.

## Why

Working folders accumulate raw exports that are fine as-is but hard to browse
inside Obsidian. Obsidianizer wraps each raw file with a YAML frontmatter block
and a "business card" (summary, tags, dates, statistics) while keeping the
original content untouched — and leaves no trace in the source folder.

```
raw notes ──> Obsidianizer ──> processed folder ──> Obsidian vault
                 │
                 └── optional: local LLM (Ollama) for summary + tags
```

## Safety contract

- **Source is never modified.**
- **Deletions happen only via the ownership manifest.** `--prune` removes only
  files Obsidianizer itself created; foreign files are never touched.
- **`source` and `target` must not overlap** (`source == target`, target inside
  source, or source inside target are hard errors).
- **LLM is optional.** If Ollama is unavailable the files are still processed
  with metadata-only enrichment.

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

# 3. Real run
obsidianizer --source ./raw --target ./processed

# 4. Second run is cheap: unchanged files are skipped (by source hash).

# 5. Remove files that are no longer produced (only Obsidianizer-owned ones):
obsidianizer --source ./raw --target ./processed --prune
```

### CLI options

```
--source DIR    source folder (overrides config)
--target DIR    target folder (overrides config)
--model NAME    Ollama model to use for summary/tags
--no-llm        skip LLM enrichment entirely
--dry-run       compute and report, but write nothing
--prune         delete Obsidianizer-owned files no longer produced
```

## Graphical interface (primary)

The desktop GUI is the main way to work with Obsidianizer:

```bash
pip install -e '.[ui]'
obsidianizer ui
```

Pick the source and target folders with the native pickers, toggle Ollama,
choose a model, optionally enable dry-run / prune, then press **Обработать**.
The window shows a progress bar, the current file, a color-coded log and a
final summary. **Стоп** stops the batch after the current file finishes —
an in-flight Ollama request is never interrupted. The CLI remains available
for automation.

## Config

`config.yml` is read if present; otherwise defaults from `config.example.yml`
apply. CLI arguments always win.

## Project layout

```
src/obsidianizer/   package (importable, installable)
tests/              pytest suite (safety-critical paths first)
docs/               architecture + processor contract + development
assets/             placeholder for future GUI assets (.exe, icons)
```

## License

MIT. See [LICENSE](LICENSE).