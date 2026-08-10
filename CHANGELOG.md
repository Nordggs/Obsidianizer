# Obsidianizer

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