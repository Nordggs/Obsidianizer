# Obsidianizer Architecture

> [Русский](architecture.ru.md) | **English**

This document is the architecture contract for Obsidianizer. The pipeline shown
below is the fixed backbone; file types plug in as processors.

## Pipeline

Two fixed stages with physically separate outputs:

```
            ┌─────────────┐
            │   SOURCE    │  raw/ — read-only archive
            └──────┬──────┘
                   │  STAGE 1: import (no LLM)
                   ▼
            ┌─────────────┐
            │  PROCESSED  │  import + metadata + media
            └──────┬──────┘
                   │  STAGE 2: AI post-processing
                   ▼
            ┌─────────────┐
            │ ENRICHED    │  + LLM summary/tags/topic/type
            └──────┬──────┘
                   ▼
               OBSIDIAN vault  (points at enriched/)
```

Stage 1 (LLM-free import):

```
                      ┌─────────────┐
                      │   SOURCE    │
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
                          EMIT
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
                Markdown           Assets
                    │                 │
                    └────────┬────────┘
                             ▼
                        PROCESSED
                             │   STAGE 2: postprocess.enrich
                             ▼
                        ENRICHED  ←  OBSIDIAN
```

Stage 2 (`postprocess.enrich`) reads every note in `processed/` and writes the
enriched copy into `enriched/`. `processed/` is never written by this stage.

## Stages

### 1. guard

Path-overlap safety check, runs before anything else, per adjacent pair:

- `first == second` → refuse
- `second` inside `first` → refuse
- `first` inside `second` → refuse

Applied triply on every run: `(source, target)`, `(target, enriched)` and
`(source, enriched)` (the AI stage must never be able to write into raw).
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

### 4. emit

- Atomically write every note (`*.tmp` → `os.replace()`).
- Copy each referenced media file into the matching relative location in the
  target folder (resolved first against the note's directory, then against the
  source root).
- Collect the current manifest (every file Obsidianizer created this run).

### 5. manifest + prune

- Read the OLD `.obsidianizer-manifest.json` before overwriting anything.
- If `--prune`: delete `OLD - CURRENT` only — never foreign files.
- Write the CURRENT manifest last, atomically, only after full success.

### 6. AI post-processing (`postprocess.enrich`)

Second stage, fully independent from the import pipeline. Called explicitly
(`obsidianizer ai`, the GUI "AI-постобработка" button) or chained right after
a real import when the LLM is enabled.

Signature:

```python
enrich(source_root: Path, target_root: Path, llm: LLMClient, *,
       prune: bool = False, on_event=..., cancel_check=...) -> EnrichReport
```

Per note:

1. Split the processed note into frontmatter/body; read `source_hash`.
2. **Fresh?** The enriched copy exists and `enriched.ai_hash == source_hash` →
   skip (no LLM call, no rewrite).
3. Otherwise `llm.analyze(body)` → summary + tags + topic + type; write the
   enriched note atomically with `ai_hash = source_hash`.
4. Copy locally-referenced media into enriched under the same relative layout,
   so image links stay valid inside the vault.

After the loop: prune orphans (optional) and rebuild `enriched/_index.md`.

Orphan pruning (`--prune-enriched`): removes enriched notes that carry the
`ai_hash` ownership marker and no longer have a pair in processed, plus media
only they referenced. Foreign vault files and still-referenced media are never
touched.

## Incrementality

Stage 1 frontmatter carries:

```yaml
source_hash: <sha1 of raw file>
```

On a rerun, an output whose `source_hash` matches the current raw file is
skipped entirely (no LLM call, no rewrite). A changed source is re-extracted,
re-emitted.

Stage 2 adds:

```yaml
ai_hash: <source_hash of the analyzed processed note>
```

The enriched copy is skipped when `ai_hash == processed.source_hash`. This
double protection makes the pipeline naturally incremental:

- new sources → imported → not yet enriched → go through Ollama;
- unchanged sources → enriched copies stay as-is (no re-calls);
- changed sources → import rewrites processed (new `source_hash`), the next AI
  stage sees the mismatch and re-enriches just that note;
- deleted sources → their enriched orphans are removed only by
  `--prune-enriched`.

A plain re-import can never destroy AI results: enriched lives in a separate
folder and keeps its marker until the AI stage repairs it.

## Filesystem invariants

- Source tree: never written, never removed.
- Target (processed) tree: written only by stage 1 via atomic replace;
  owned-file bookkeeping via the manifest. Never modified by stage 2.
- Enriched tree: written only by stage 2 (atomic replace); `ai_hash` marker is
  the ownership basis for `--prune-enriched`; `_index.md` is rebuilt here too.
- Manifest: `.obsidianizer-manifest.json` lives at the processed root;
  git-ignored.
- Relative media paths in notes stay valid because both output folders mirror
  the source's relative directory structure.

## Determinism

Output for identical inputs is identical: metadata extraction is regular-
expression based, LLM output is the only non-deterministic ingredient, and only
when explicitly enabled.

## UI layer

The desktop GUI is the primary user interface. It is a *frontend only*: every
operation goes through the same core the CLI uses. The GUI knows nothing about
Markdown/file types; it renders the event contract below.

```
                    ┌──────────────────────┐
                    │         UI           │
                    │  source / target     │
                    │  Ollama / model      │
                    │  options             │
                    │  progress / log      │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │         CLI          │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    CORE PIPELINE     │
                    └──────────────────────┘
```

- CLI and UI are two interchangeable frontends for the same core.
- The GUI is the primary user interface; the CLI is the automation interface.
- The core knows nothing about UI; the UI knows nothing about Markdown/file
  types. Communication goes through the event contract below.

### Persistent settings

`config.yml` is the single source of persistent user state for both the CLI and
the GUI:

- **read** — `Settings.load()` on startup fills the form (paths, model,
  Ollama toggle, prune / dry-run flags);
- **write** — `Settings.save()` runs on every confirmed change (`set_paths`,
  `set_llm`, `set_flags`), before a run starts and on window close. The write
  *merges* into the existing file (hand-edited keys survive) and is atomic
  (`.tmp` → `os.replace`). CLI flags still take precedence over the file.

Each `Выбрать` picker starts in its own last-used directory (source → source,
result → target, AI result → enriched). A saved path that no longer exists
degrades to the nearest existing parent instead of failing the dialog.

### Model picker & prompt editor

The AI layer is fronted by the `AI-постобработка` toggle (Ollama is an
implementation detail, not a separate concept). When the toggle is off, the
model / prompt settings collapse into the hidden `#aiDetails` block; switching
it on reveals them along with a grey «Локальная модель через Ollama» note. The
run buttons reflect the state: «ОБРАБОТАТЬ» shows «Импорт + AI» or
«Только импорт», and the secondary AI button shows «Только повторный AI-прогон»:

- **Model**: a free-text combo box with a drop-down of installed models
  (custom list, not a `datalist` — so every installed model is visible
  regardless of the current input text). The list is fetched from Ollama on
  demand (`list_models` → `GET /api/tags`) — names only, no chat content is
  ever sent. The current model is always kept in the list, so it survives both
  restarts and a temporarily unreachable Ollama (`Ollama недоступен` is shown
  instead of breaking the UI).
- **Prompts**: the `Изменить…` button opens a modal editor with three tabs
  (import `prompt`, AI `ai_prompt` and topic `topic_prompt`). `get_prompts`
  returns the templates plus their built-in defaults; `set_prompt` persists an
  edited template via `Settings.save()`; `reset_prompt` restores a kind to its
  `DEFAULT_PROMPT` / `DEFAULT_AI_PROMPT` / `DEFAULT_TOPIC_PROMPT` — the
  built-ins themselves are never overwritten, and `{content}` remains the
  single substitution point for the file/chat body.

### Topic builder (`topics.py`)

The topic builder merges several chats into a single knowledge note — a
second-layer summary that links back to its sources instead of copying them:

    processed/ (selected chats) → one LLM call → enriched/topics/<Name>.md

- **Selection**: the GUI modal (`list_chats` → checkbox list with search)
  sends relative processed paths to `create_topic`. Only files carrying the
  `source_hash` ownership marker are accepted.
- **Payload**: each chat becomes a `## Файл` block (title/source/date + body
  snippet). The character budget (`TOPIC_LIMIT_CHARS`, 16k) is distributed
  evenly so every chat is represented; a huge selection degrades to shorter
  snippets, never to an oversized single request.
- **Contract**: one LLM call returns NAME / SUMMARY / DECISIONS / KEY_FACTS /
  ARTIFACTS (`parse_topic_response`, degraded to empty values on failure). The
  file name comes from the model's NAME block (`sanitize_name`), so the user
  never types a topic name.
- **Output**: `enriched/topics/<Name>.md` with `topic_hash` + `chats` in
  frontmatter, a knowledge card (Суть / Решения / Ключевые факты /
  Артефакты) and per-service `[[wiki]]` links back to the chats. `_index.md`
  is rebuilt so the topic appears in navigation.
- **Incrementality**: `topic_hash` = sha1 of the sorted selected
  `source_hash` values. An existing topic with the same hash skips the LLM
  call; a changed selection re-runs and rewrites the topic.

**Auto-grouping** (`topics.group_all`, «Авто-группировка») removes the manual
selection: the whole processed collection is clustered by the LLM in one map
pass, then each cluster of two or more chats goes through the same
`create_topic` path:

- A `map_prompt` call receives the chat cards (`[idx] title (service, date)`
  plus a short body snippet); the answer is repeated `TOPIC:` / `IDS:` blocks
  (`parse_topic_map`). Indices are 1-based *global* collection indices; a chat
  belongs to the first matching topic only.
- Large collections are split into payload-sized chunks
  (`MAP_CHUNK_CHARS`); already-discovered topic names are fed back into the
  next chunk so the same theme keeps its name.
- Single-chat clusters are counted and skipped (`one_chat`); existing topics
  are never deleted — a changed clustering only adds/refreshes topics.
- The GUI drives the run via `group_all()`; `TOPIC_MAP_STARTED` opens the map
  pass, then one `TOPIC_FILE_*` per created topic.

The per-file AI pass stays available as an explicit «AI-постобработка» action,
but «ОБРАБОТАТЬ» no longer triggers AI automatically — import and AI are
separate conscious steps.

### Event contract (`events.py`)

`events.py` is the single source of truth. The core emits through an optional
`on_event` callback on `pipeline.run`; listeners (CLI, GUI) render as they
like.

```python
Event(type=EventType, path=..., index=..., total=..., message=...)
```

| Event         | Meaning                                       |
|---------------|----------------------------------------------|
| `SCAN_STARTED`| batch discovered; `path` = source root       |
| `FILE_STARTED`| processing a file; `index`/`total` progress  |
| `FILE_DONE`   | file processed successfully                  |
| `FILE_SKIPPED`| skipped: unchanged via `source_hash`         |
| `FILE_ERROR`  | file failed; `message` = error text          |
| `FINISHED`    | batch over; `message` = summary counts       |
| `AI_SCAN_STARTED` | stage 2 discovered the processed batch   |
| `AI_FILE_STARTED` | AI pass on a processed note              |
| `AI_FILE_DONE`    | enriched note written                    |
| `AI_FILE_SKIPPED` | enriched copy already fresh              |
| `AI_FILE_ERROR`   | note failed on stage 2                   |
| `AI_FINISHED`     | stage 2 over; `message` = AI counts      |

The same `Event` renders differently per frontend:

- GUI: `✓ Обработан 17 из 120`
- CLI: `[17/120] ✓ filename.md`

Future frontends only subscribe to these events; the core pipeline does not
change.

### Stop semantics

`pipeline.run` accepts an optional `cancel_check: Callable[[], bool]`. It is
polled **between files only** — never inside a file and never during an LLM
call. The current file is always allowed to finish (an Ollama request may take
up to 180 s); the UI announces "Остановка… текущий файл будет завершён" instead
of promising an instant stop.

On cancel:

- `Report.cancelled` is set to `True` (only ever from this path);
- ownership of already-written files is preserved via the manifest (written
  only when at least one file was produced);
- `--prune` is skipped and `_index.md` is not published (an unfinished scan
  must not delete anything or leave a partial navigation index);
- `FINISHED` is still emitted exactly once, with an "отменено…" summary.

A fatal, run-level error (any exception outside a single-file handler) sets
`Report.critical_error` and keeps `Report.cancelled` as `False`; `FINISHED` is
still emitted exactly once ("критическая ошибка: …"). In every path — normal
completion, per-file errors, cancel, fatal error — `FINISHED` arrives exactly
once.