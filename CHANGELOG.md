# Obsidianizer

## [0.1.0] - Unreleased

### Added
- Repository scaffold: src layout, pyproject, tests, docs, AGENTS contract.
- Core pipeline: scan → extract → enrich → emit.
- Safety: guard (no source/target overlap), ownership manifest, atomic writes.
- Markdown processor for AI-chat raw exports (metadata extraction + summary/tags
  via Ollama, graceful degradation when the LLM is unavailable).
- Incremental processing via `source_hash` in the output frontmatter.
- `_index.md` generation for vault navigation.