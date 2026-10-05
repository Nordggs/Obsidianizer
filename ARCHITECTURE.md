# Architecture — Obsidianizer v0.7.2

## Overview

Obsidianizer is a local tool that prepares project materials for
[Obsidian](https://obsidian.md) and keeps them working afterwards. It has no
cloud dependency: everything runs on the machine, the source tree is never
modified, and the optional AI layer talks to a local Ollama instance only.

The application bundles **three tools** that share one core. Two frontends
(pywebview GUI and CLI) drive the same modules, so behaviour is identical
regardless of how a run is started.

```
   Obsidianizer.py / .bat            obsidianizer  (console script)
        (GUI launcher)                obsidianizer-cli.bat
              │                               │
              ▼                               ▼
        ui.launch()                      cli.main()
        (webview window)                 (argparse)
              │                               │
              └───────────────┬───────────────┘
                              ▼
                        core modules
   ┌──────────────┬──────────────┬───────────────┬──────────────┐
   ▼              ▼              ▼               ▼              ▼
obsidianize.py  pipeline.py   postprocess.py  topics.py    search.py / review.py
folder cards    import stage  AI enrich       topic builder AI analysis
```

## Three tools

| # | Tool | Entry point | What it does |
|---|------|-------------|--------------|
| 1 | **Obsidianize** | GUI tab 1, `obsidianizer folders` | Scan an ordinary folder read-only, emit a Markdown card per folder, keep user data in a paired notes file |
| 2 | **Processing (Enrichment)** | GUI «Обработка» (Source/Detected/Enrichment/AI settings/Folder review/Tools), `obsidianizer` / `obsidianizer ai` | Import Markdown into `processed/`, then AI-enrich into `enriched/` |
| 3 | **AI tools** | Folder review (Processing section), `search` / `review` / `topics`, dedicated chat window | Local-LLM search, folder review, topic building, interactive chat |

## Module map (`src/obsidianizer/`)

| Module | Responsibility |
|--------|----------------|
| `cli.py` | argparse entry point (`ui` / `ai` / `folders` subcommands + global flags) |
| `ui.py` | pywebview bridge `UIApp`: tabs, events, settings, windows (main/chat/help), update check |
| `config.py` | `Settings` load/save/merge (`config.yml`), default prompts, `PROCESS_VERSION` |
| `events.py` | The single source of truth for `EventType` / `Event` |
| `obsidianize.py` | Folder-card engine: scan, freshness, rendering, notes/comments preservation |
| `pipeline.py` | Stage 1 import: scan → extract → emit → manifest/prune |
| `postprocess.py` | Stage 2 AI enrichment `processed/ → enriched/` |
| `registry.py`, `base.py`, `models.py` | Processor registry and data records |
| `md_processor.py` | Markdown processor (regex metadata, verbatim body, media refs) |
| `enrich.py` | Note/frontmatter/navigation composition for the pipeline |
| `emit.py`, `manifest.py`, `guard.py` | Atomic writes, ownership manifest + prune, path-overlap safety |
| `search.py` | Persistent chat index (lexical + embeddings) with incremental refresh |
| `review.py` | AI folder review → `<folder>_обзор.md` |
| `topics.py` | Topic builder (`create_topic`) and collection auto-grouping (`group_all`) |
| `llm.py` | Ollama client (`/api/generate`, `/api/chat`, `/api/embed`, `/api/tags`) |
| `integration.py` | Templater hotkey template install/repair and status |
| `i18n.py` | RU/EN strings and language switching |
| `nav_index.py` | `_index.md` generation for the processed/enriched trees |

## Tool 1 — Folder Obsidianizer (`obsidianize.py`)

Read-only scan produces a GitHub-style card per folder; the source tree is
never written (only `os.scandir` / `os.stat` are used).

```
scan_tree ─► update_cards ─► _process_folder ─┬─ card_status        (ok/stale/missing/conflict)
                                              ├─ _ensure_notes_file (user layer, written first)
                                              ├─ _sync_comments_to_notes
                                              └─ build_card ─► _render_dashboard ─► write_atomic
```

- **Card vs notes.** The card (`<folder>.md`) carries *service frontmatter only*
  (`obsidianizer: true`, `obsidianizer_hash`, `obsidianizer_template`,
  `obsidianizer_version`, `cssclasses`) and is fully regenerable. All user data
  lives in `<folder>_заметки.md`, embedded into the card as
  `![[<folder>_заметки]]`. The notes file is created once and never overwritten;
  it is written **before** the card so a crash cannot lose user data.
- **Frontmatter preservation.** `parse_frontmatter` uses PyYAML and normalizes
  non-string keys to strings, with a tolerant line-by-line fallback on
  `YAMLError`. `_user_props` keeps every non-service key; legacy names are
  remapped (`клиент → проект`, `дизайнер → контакт`).
- **Comments.** The Files table has editable `Comments` (plus the auto
  `Description (Auto)`); the Folders table has `Comments`. Table edits are
  synced into the notes frontmatter (`file_comments` / `folder_comments`) on
  every run — even when the card is skipped — so they survive regeneration.
- **Freshness.** `folder_fingerprint` is a sha1 over `(rel, size, content hash)`
  + subfolders + presence of the review file; mtime is deliberately excluded.
  A hidden manifest inside the card stores per-file hashes and the notes-data
  hash, so `card_status` reports `ok / stale / missing / conflict` and
  `card_diff` explains *what* changed.
- **Templater integration.** `integration.py` fills `{{CLI_PATH}}` into the
  template and installs it into the vault's Templater folder; the hotkey runs
  `folders --no-recursive --adopt --vault-root --rel` against the current note.

### Card structure

Repo header → `Folders` → `Files` → `About` → `Gallery` → `Images` →
`AI Review` → `Notes` → footer.

| Table | Columns |
|-------|---------|
| Folders | Name, Files, Size, Updated, Comments |
| Files | File, Type, Opens with, Modified, Size, Description (Auto), Comments |

## Tool 2 — Chat processing pipeline

Two fixed stages with physically separate outputs:

```
raw (SOURCE, read-only) ──import──► processed/ ──AI──► enriched/ ──► Obsidian vault
```

```
guard ─► registry.scan ─► processor.parse/body/media_refs ─► emit.write_note/copy_media
      ─► manifest (.obsidianizer-manifest.json) ─► [--prune]
      ─► postprocess.enrich ─► [--prune-enriched] ─► _index.md
```

- **guard** (`guard.py`) rejects overlapping path pairs: `same`, `second inside
  first`, `first inside second`; checked for `(source, target)`,
  `(target, enriched)`, `(source, enriched)`. Failure returns exit code 1.
  (`folders` intentionally has no guard — cards are written into the scanned
  folder by design.)
- **Import** (`pipeline.run`) is LLM-free; it writes notes and copies referenced
  media with atomic `.tmp → os.replace`.
- **Manifest/prune**: `--prune` deletes exactly `OLD − CURRENT` owned files;
  foreign files are never touched. The manifest is written last, atomically.
- **AI enrich** (`postprocess.enrich`) is a separate, explicit stage
  (`obsidianizer ai` or the GUI button). `processed/` is never written here.
- **Incrementality** is hash-based:
  - `source_hash` = sha1 of the raw file (stage 1 frontmatter);
  - `ai_hash` = `source_hash` of the analyzed note (stage 2 marker, the ownership
    basis for `--prune-enriched`);
  - `PROCESS_VERSION` (`config.py`) forces a rewrite when the output format
    changes, even if `source_hash` matches.

## Tool 3 — AI analysis

- **Search** (`search.py`): a persistent `ChatIndex` under
  `<enriched>/.obsidianizer/` (`index.json` + `embeddings.json`), refreshed
  incrementally by `source_hash`. Scores blend a lexical score (IDF-like token
  boost) with embedding cosine similarity; embedding failures degrade silently to
  lexical-only.
- **Review** (`review.py`): collect a folder payload → one LLM call →
  `build_review_markdown` → `<folder>_обзор.md` (atomic). The card embeds it in
  the `AI Review` section.
- **Topics** (`topics.py`): `create_topic` merges several chats into
  `enriched/topics/<Name>.md` (`topic_hash` = sha1 of the sorted selected
  `source_hash` values → skip on match). `group_all` clusters the whole
  collection via a map pass (`TOPIC:` / `IDS:` blocks, chunked) and reuses
  `create_topic` for each cluster of two or more chats.
- **LLM** (`llm.py`): all parsers degrade to empty values on failure; if Ollama
  is unavailable the rest of the application keeps working.

## GUI bridge (`ui.py`)

JavaScript calls `window.pywebview.api.*`; the backend runs on a worker thread
and streams progress back via events.

- **Windows**: `main` (`web/app.html` + `app.js`), a dedicated chat window
  (`web/chat.html` + `chat.js`), and a help window (`web/help.html`).
- **Tabs**: `Obsidianize`, `Обработка` (Source / Detected / Enrichment /
  AI settings / Folder review / Tools), plus the help button and the
  dedicated AI-chat window (its own chat-model editor). The language toggle
  (RU/EN) and the update indicator live in the status bar.
- **Event dispatch**: `_on_event` appends to `self.events` and pushes
  `window.pushEvent(payload)`; chat events go to the chat window only.
- **Persistence**: `Settings.load()` / `Settings.save()` merge into `config.yml`
  atomically. The file lives in `_user_data_dir()` — `%LOCALAPPDATA%\Obsidianizer`
  when frozen, the repo root when running from source. CLI flags override it.
- **Update indicator**: `check_update()` queries the GitHub Releases list
  (drafts skipped, pre-releases accepted) and lights a dot when a newer tag
  exists; it never auto-updates.

## Event contract (`events.py`)

```python
Event(type: EventType, path: str = "", index: int = 0,
      total: int = 0, message: str = "", data: dict = {})
```

`message` is a localized, human-readable detail; `data` carries
machine-readable payloads so a frontend never has to parse text.

| Family | Events |
|--------|--------|
| Batch (stage 1) | `SCAN_STARTED`, `FILE_STARTED`, `LLM_STARTED`, `FILE_DONE`, `FILE_SKIPPED`, `FILE_ERROR`, `FINISHED` |
| AI enrich (stage 2) | `AI_SCAN_STARTED`, `AI_FILE_STARTED`, `AI_FILE_DONE`, `AI_FILE_SKIPPED`, `AI_FILE_ERROR`, `AI_FINISHED` |
| Topic build | `TOPIC_SCAN_STARTED`, `TOPIC_MAP_STARTED`, `TOPIC_FILE_STARTED`, `TOPIC_FILE_DONE`, `TOPIC_FILE_ERROR`, `TOPIC_FINISHED` |
| Topic lifecycle | `TOPIC_UPDATED`, `TOPIC_RENAMED`, `TOPIC_DELETED` |
| AI chat | `CHAT_REPLY`, `CHAT_ERROR`, `CHAT_FOUND` |
| Folder Obsidianizer | `OBS_SCAN_STARTED`, `OBS_FOLDER_DONE`, `OBS_FINISHED`, `OBS_ERROR` |
| Folder review | `REVIEW_STARTED`, `REVIEW_FOLDER_DONE`, `REVIEW_FINISHED`, `REVIEW_ERROR` |

`<...>_FINISHED` is emitted exactly once on every path (normal completion,
per-file errors, cancel, fatal error).

## Safety invariants

- **Source is never modified** by any tool.
- **Path-overlap guard** for the pipeline stages (see above).
- **Deletions are ownership-gated**: the stage-1 manifest for `--prune`, the
  `ai_hash` marker for `--prune-enriched`, `type: topic` + `topic_id` for topic
  deletion. Foreign notes are never overwritten without `--force` / `--adopt`.
- **Atomic writes** everywhere (`.tmp → os.replace`): notes, cards, manifest,
  config, index, topic files.
- **Notes are the user's territory**: `<folder>_заметки.md` is created once and
  never rewritten (only its frontmatter may gain synced comments).

## Error handling and cancellation

- `update_cards` isolates each folder: a failure is logged with a traceback,
  recorded in `summary.failed`, emits `OBS_FOLDER_DONE` with action `failed`,
  and the run continues with the remaining folders. The CLI prints `failed`; the
  `OBS_FINISHED` payload contains it.
- The pipeline wraps each file in a per-file handler (`FILE_ERROR` /
  `AI_FILE_ERROR` / `TOPIC_FILE_ERROR`).
- `cancel_check` is polled **between files only** (never inside a file or an LLM
  call). On cancel, ownership of already-written files is kept, `--prune` is
  skipped and `_index.md` is not published.

## Build and release

- **PyInstaller** onedir build via `build-release.bat`
  (`Obsidianizer.spec` → `dist/Obsidianizer/`), with the web assets bundled and
  `Obsidianizer.exe.config` + `integration/` copied next to the exe.
- **Inno Setup** installer via `Obsidianizer.iss` (`release/Obsidianizer-Setup-<ver>.exe`).
- The portable archive is `release/Obsidianizer-<ver>.zip` (contents of
  `dist/Obsidianizer/`).

## File structure

```
Obsidianizer.py / .bat / obsidianizer-cli.bat   launch entry points
src/obsidianizer/                                package
  web/                                           HTML/JS/CSS for the GUI
tests/                                           pytest suite
docs/                                            user + architecture docs
integration/                                     Templater/shell integration payload
obsidian/                                        manual setup notes
Obsidianizer.spec / .iss / build-release.bat     build & packaging
config.example.yml                               documented defaults
```

## Tests

The suite (`tests/`, `pytest`) focuses on safety-critical paths first: guard,
manifest/prune, pipeline incrementality, postprocess, folder-card golden tests,
comments round-trips, i18n symmetry, integration install, and the UI bridge in
headless mode.

## Dependencies

- Python 3.10+
- `pyyaml>=6.0` — frontmatter/config parsing
- `httpx>=0.27` — Ollama HTTP client
- `pywebview>=5` (extra `ui`) — desktop GUI
