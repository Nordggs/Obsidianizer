# Obsidianizer

## [0.3.0] - 2026-08-27

### Added
- Stable recovery point after a catastrophic `git stash` + `reset --hard`
  (full 3-tab GUI restored, 288 tests green). The card core
  (`obsidianize.py`) carries the v5.11/v5.12 structure with
  `RENDER_VERSION = 8` (Folders / Files / About / Gallery / Images / Notes).
- Recovery safety nets: branches `recovery-pre-v5.11` (pre-v5.11 state) and
  `recovery-current-v5.11` (state with v5.11/v5.12 CLI/UI bits), plus
  `backup-before-recovery` on the base commit; tagged `v0.3.0`.
- Two-stage output: `processed/` (import) and `enriched/` (import + LLM).
- Persistent GUI state: `config.yml` is the single store for paths, model,
  Ollama toggle and prune / dry-run flags. `Settings.save()` merges into the
  existing file and writes atomically; the GUI saves on every confirmed
  change, on run and on window close, so a reopened window restores exactly
  the state it was closed in. Each `Выбрать` picker opens in its own last-used
  directory with a nearest-existing-parent fallback.
- AI toggle: the «Использовать Ollama» checkbox became «AI-постобработка» —
  Ollama is treated as the implementation detail of the AI layer. When the
  toggle is off, the model / prompt settings hide inside a collapsible
  `#aiDetails` block; turning it on reveals the model combo box and the
  `Изменить…` prompt button, with a grey «Локальная модель через Ollama» note.
  Action buttons gained subtitles: «ОБРАБОТАТЬ» shows «Импорт + AI» /
  «Только импорт», the secondary AI button shows «Только повторный AI-прогон».
- Model picker + prompt editor behind the AI toggle: the model field is a
  combo box — free-text input plus a drop-down of installed Ollama models
  (custom list, not a `datalist`, so every model stays visible; `list_models`,
  refreshed on demand via `GET /api/tags`; names only, the current model is
  always kept, unreachable Ollama shows a status instead of breaking the UI).
  The `Изменить…` button opens a modal editor with import / AI tabs, per-kind
  `set_prompt` / `reset_prompt` (built-in `DEFAULT_PROMPT` / `DEFAULT_AI_PROMPT`
  are never overwritten; `{content}` stays the single substitution point).
- AI stage (`obsidianizer ai`): reads processed notes and writes the enriched
  vault; incremental via `ai_hash` marker — a note is skipped when its
  `ai_hash` matches the processed `source_hash`.
- `--enriched DIR` / `--prune-enriched` options; orphan pruning is marker-gated
  (only files carrying `ai_hash` whose pair is gone from processed, plus media
  no other note references).
- Separation guarantee: adjacent-stage folder pairs must not overlap
  (`(source, target)`, `(target, enriched)`, `(source, enriched)`).
- AI-stage events: `AI_SCAN_STARTED`, `AI_FILE_STARTED`, `AI_FILE_DONE`,
  `AI_FILE_SKIPPED`, `AI_FILE_ERROR`, `AI_FINISHED` (replaces the old
  `LLM_STARTED`).
- Topic builder («Объединить в тему…»): select several processed chats in a
  modal (checkbox list with search) and merge them into one knowledge topic
  note at `enriched/topics/<Name>.md`. A single LLM call produces
  NAME / SUMMARY / DECISIONS / KEY_FACTS / ARTIFACTS plus per-service source
  links back to the source chats; the file name comes from the model's NAME
  block (sanitized). Incremental via `topic_hash` (hash of the selected chats'
  `source_hash` values): an unchanged selection skips the LLM call. New
  `topic_prompt` template (third tab of the prompt editor), `TOPIC_*` events
  and bridge methods `list_chats()` / `create_topic()`.
- «ОБРАБОТАТЬ» is now a pure import: it no longer starts the AI stage
  automatically. AI runs only on explicit actions — the per-file
  «AI-постобработка» button or «Объединить в тему…». The button subtitle is
  fixed to «Только импорт».
- Auto-grouping («Авто-группировка»): cluster the whole processed collection
  into topics with no manual selection. A `map_prompt` pass groups the chat
  cards (title/source/date + snippet) into topics via `TOPIC:`/`IDS:` blocks;
  large collections are split into payload-sized chunks and already-discovered
  topic names are fed back so the same theme keeps its name. Each cluster of
  two or more chats then goes through the same `create_topic` path (incremental
  via `topic_hash`); single-chat clusters are skipped, existing topics are
  never deleted. New `map_prompt` template (fourth tab of the prompt editor),
  `TOPIC_MAP_STARTED` event and bridge method `group_all()`.

## [0.3.0] - Unreleased

### Added
- Desktop GUI (`obsidianizer ui`, pywebview): source/target pickers, Ollama
  toggle + model, dry-run/prune switches, progress bar, current file, color
  log, final summary, open-target button. Pure frontend over the same core.
- Honest stop: `pipeline.run` gains `cancel_check` (polled between files only;
  the current file — including a long Ollama call — is allowed to finish).
- Guaranteed single `FINISHED` in every path (normal, per-file error, cancel,
  fatal run-level error). `Report.cancelled` only on user cancel;
  `Report.critical_error` records fatal errors.
- Root launcher `Obsidianizer.py` / `Obsidianizer.bat`: double-click starts
  the GUI straight from the repo root — no package install required;
  `Obsidianizer.py --check` prints diagnostics without opening a window.

## [0.2.0] - Unreleased

### Added
- Bilingual documentation (RU + EN): `README.ru.md`, `AGENTS.ru.md`,
  `CHANGELOG.ru.md`, `docs/*.ru.md`; bilingual policy in `AGENTS.md`.
- Event contract (`events.py`): `SCAN_STARTED`, `FILE_STARTED`, `LLM_STARTED`,
  `FILE_DONE`, `FILE_SKIPPED`, `FILE_ERROR`, `FINISHED`. `pipeline.run` accepts
  an optional `on_event` callback; core stays presentation-agnostic.
- Placeholder for the future GUI shell (`ui.py`) and `assets/` directory.
- Architecture docs: "UI layer" section with the event protocol.

## [0.1.0] - Unreleased

### Added
- Repository scaffold: src layout, pyproject, tests, docs, AGENTS contract.
- Core pipeline: scan → extract → enrich → emit.
- Safety: guard (no source/target overlap), ownership manifest, atomic writes.
- Markdown processor for AI-chat raw exports (metadata extraction + summary/tags
  via Ollama, graceful degradation when the LLM is unavailable).
- Incremental processing via `source_hash` in the output frontmatter.
- `_index.md` generation for vault navigation.