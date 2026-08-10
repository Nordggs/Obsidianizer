# Obsidianizer Development Rules

## Purpose

Obsidianizer is a standalone local tool that prepares raw materials for use in
Obsidian: it structures files, enriches them with metadata and (optionally)
LLM-generated summaries/tags, and emits an Obsidian-ready output folder — while
never touching the source.

It must stay independent of AI Chat Exporter. The two projects do not share
code; communication happens only through the filesystem (a `--source` path).

## Critical principles

1. Never modify source files. Source is read-only.
2. Never delete files outside the Obsidianizer ownership manifest.
3. `source` and `target` must never overlap (`source == target`, target inside
   source, source inside target are all hard errors).
4. LLM is optional. A failure must never stop processing.
5. Processors are registered through the processor registry.
6. The core pipeline must not contain file-type-specific logic.
7. New file types must be implemented as processors, never as core hacks.
8. Generated output must be deterministic where possible.
9. The manifest is the ownership journal. It is written atomically and only
   after all other operations succeed.
10. Exit the whole batch cleanly when a single file is malformed: report it,
    continue with the rest.

## Architecture

```
scan → extract → enrich → emit → manifest
```

- `scan` — walk the source folder; candidates are files with registered
  extensions (registry).
- `extract` — the processor for the file type produces metadata and preserves
  the original body.
- `enrich` — build YAML frontmatter + a human-readable "business card"; call
  the LLM only if configured and available.
- `emit` — atomically write outputs, copy referenced media.
- `manifest` — ownership journal; `--prune` deletes only `old - current`.

Incrementality: every output carries a `source_hash` (SHA-1 of the raw file);
unchanged sources are skipped on the next run.

## Safety

### Path overlap guard (`guard.py`)

Reject before doing anything:

- `source == target`
- `target` is inside `source`
- `source` is inside `target`

Compare `Path.resolve()` values, normalized for case on all platforms. Never
attempt to "smartly" resolve an overlap — refuse to run.

### Prune (`manifest.py`)

- Every file created by Obsidianizer (notes + copied media + `_index.md`) is
  recorded in `.obsidianizer-manifest.json` inside the target folder.
- Read the OLD manifest before writing any new one.
- `prune(old, current)` deletes only `OLD - CURRENT`.
- Never delete a file that is not in the manifest.
- New manifest is written last, atomically (`.tmp` → `os.replace()`), only on
  success.
- No manifest + `--prune` → delete nothing, just create the current manifest.

### Writes (`emit.py`)

- Every output file is written atomically: `*.tmp` then `os.replace()`.
- Skip unchanged files via `source_hash` (no LLM re-calls for them).
- Media is copied per referenced path, never guessed.

## LLM contract (`llm.py`)

- Local only (Ollama `/api/generate`). No API keys, no cloud.
- Strict parse contract: `SUMMARY:` block and `TAGS:` line list.
- All exceptions are caught and degrade to empty summary/tags.

## Project structure

```
src/obsidianizer/    package (src layout — installable, importable)
  cli.py             entry point; argument parsing is isolated here
  config.py          precedence: CLI > config.yml > defaults
  guard.py           path-overlap guard
  registry.py        extension -> Processor
  base.py            Processor ABC
  md_processor.py    Markdown processor (primary type)
  llm.py             local LLM client
  enrich.py          frontmatter + business card composer
  emit.py            atomic writer + media copier
  manifest.py        ownership journal + prune
  index.py           _index.md generator
  models.py          dataclasses
tests/               pytest; fixtures under tests/fixtures/
docs/                architecture, processors, development
```

## Commands

```bash
python -m obsidianizer --source ./raw --target ./processed --dry-run
obsidianizer --source ./raw --target ./processed --prune   # after `pip install .`
pytest                  # safety-critical tests run from the repo root
```

## Committing

- Keep the history clean and atomic: one logical change per commit.
- The project must remain standalone — do not vendor or reference AI Chat
  Exporter internals.