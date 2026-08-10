# Obsidianizer Architecture

This document is the architecture contract for Obsidianizer. The pipeline shown
below is the fixed backbone; file types plug in as processors.

## Pipeline

```
                    ┌─────────────┐
                    │   SOURCE    │
                    │             │
                    │ MD TXT SVG  │
                    │ PY ...      │
                    └──────┬──────┘
                           │
                         SCAN
                           │
                           ▼
                     ┌───────────┐
                     │ REGISTRY  │
                     └─────┬─────┘
                           │
                      PROCESSOR
                           │
                           ▼
                       EXTRACT
                           │
                           ▼
                     AI ENRICHMENT
                           │
                           ▼
                        EMIT
                           │
                  ┌────────┴────────┐
                  ▼                 ▼
              Markdown           Assets
                  │                 │
                  └────────┬────────┘
                           ▼
                      OBSIDIAN
```

## Stages

### 1. guard

Path-overlap safety check, runs before anything else:

- `source == target` → refuse
- `target` inside `source` → refuse
- `source` inside `target` → refuse

Implemented in `guard.py`. Failure exits the process with an explanatory
message and a non-zero exit code.

### 2. scan

Walk the source folder recursively. A file is a candidate if its extension is
registered in the processor registry. Non-registered files are never treated as
notes.

### 3. extract

The registry dispatches to the processor registered for the file extension.
The processor:

- produces a flat metadata dict (title, dates, statistics, references, …);
- returns the original body verbatim (the raw content is preserved);
- returns local media references (paths used by this file).

### 4. enrich

- Build the YAML frontmatter (+ `source_hash` for incrementality).
- Build a human-readable "business card" block.
- Insert the original body unchanged below the card.
- Optional LLM step: send a bounded slice of the content to a local Ollama
  instance; parse `SUMMARY:` / `TAGS:`. On any failure degrade to empty values.

### 5. emit

- Atomically write every note (`*.tmp` → `os.replace()`).
- Copy each referenced media file into the matching relative location in the
  target folder (resolved first against the note's directory, then against the
  source root).
- Collect the current manifest (every file Obsidianizer created this run).

### 6. manifest + prune

- Read the OLD `.obsidianizer-manifest.json` before overwriting anything.
- If `--prune`: delete `OLD - CURRENT` only — never foreign files.
- Write the CURRENT manifest last, atomically, only after full success.

## Incrementality

Each output frontmatter carries:

```yaml
source_hash: <sha1 of raw file>
```

On a rerun, an output whose `source_hash` matches the current raw file is
skipped entirely (no LLM call, no rewrite). A changed source is re-extracted,
re-enriched, re-emitted.

## Filesystem invariants

- Source tree: never written, never removed.
- Target tree: written only via atomic replace; owned-file bookkeeping via the
  manifest.
- Manifest: `.obsidianizer-manifest.json` lives at the target root; git-ignored.
- Relative media paths in notes stay valid because the target mirrors the
  source's relative directory structure.

## Determinism

Output for identical inputs is identical: metadata extraction is regular-
expression based, LLM output is the only non-deterministic ingredient, and only
when explicitly enabled.