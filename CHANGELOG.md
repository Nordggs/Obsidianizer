# Obsidianizer

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