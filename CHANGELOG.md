# Obsidianizer

## [0.5.0] - 2026-08-28

### Added
- **Obsidian Integration** — one-button setup of the Templater hotkey:
  - 🔗 «Obsidian Integration» button on the Obsidianize tab: copies the
    update template into the vault's Templater folder **with the real
    Obsidianizer path substituted automatically** (no manual path editing,
    no PATH entries);
  - floating Help window gains an **🔗 Integration** tab with a live
    status checklist — vault ✓/✗, Templater ✓/✗, template ✓/✗, CLI ✓/✗ —
    and an Install / Repair button (an existing template is never
    overwritten without an explicit second click);
  - missing Templater is reported as guidance, not an error.
- **The frozen EXE doubles as the CLI**: `Obsidianizer.exe folders …`
  proxies into the same engine as `obsidianizer-cli.bat`, so installed
  builds need no bat files.
- `src/obsidianizer/integration.py`: CLI resolution (frozen vs source),
  Templater folder detection (reads the plugin's `data.json`, falls back
  to `<vault>/templates`), atomic template install with JS-escaped paths.

New feature release: the 0.4.x line (recovery, fixes, docs, restyle) is
complete and stable.

## [0.4.5] - 2026-08-28

### Fixed
- The splash screen rendered at its native 1408×768 size — larger than the
  startup window: the `#splash` CSS block was accidentally missing from
  v0.4.1 (the div and the fade-out JS were in place, the styles were not).
  The full splash stylesheet is now in place with the image capped at
  `min(70%, 900px)` width and 70% height — centered on the `#0b0f17`
  background, visibly inside the window, same feel as AI Chat Exporter.

## [0.4.4] - 2026-08-28

### Changed
- The startup window size now matches **AI Chat Exporter** (1300×750,
  background `#0b0f17`) — the splash screen renders identically in both
  apps instead of overflowing a smaller window.

### Added
- The "?" help is now a **floating native window** (like the AI-chat
  window): it can be dragged anywhere — including outside the main
  window's bounds — and stays on top. The "?" button passes the active
  tab; the help window has its own tab switcher (Obsidianize /
  Чат-обработка / AI-анализ), reads the initial section from the URL
  hash, and follows `showHelpTab` pushes when already open. Replaces the
  in-window modal, which could never leave the app's bounds.

## [0.4.3] - 2026-08-28

### Fixed
- The card-table resize divider broke after v0.4.2 (the tab became a
  flex column with its own scroll, and Chromium scroll anchoring shifted
  content mid-drag). The page returned to whole-window scrolling (settings,
  table and log scroll together, as requested) and the divider now uses an
  absolute cursor-position calculation, immune to scrolling.
- The status footer (version + scan summary) stays pinned via
  `position: sticky; bottom: 0` — visible on every tab while the whole
  window scrolls.

### Changed
- Scrollbars re-themed to the AI Chat Exporter palette (dark track
  `#0b0f17`, thumb `#2a3444` → `#4a5568` on hover) — applies to every
  scrollable area including the card table, log and modals.
- The template dropdown (`github/classic`) is dark-themed too
  (`select` + `option`), no more default white OS rendering.

## [0.4.2] - 2026-08-28

### Changed
- The status footer is now **pinned to the bottom of the window** on every
  tab (the tab content scrolls inside its own area, mirroring AI Chat
  Exporter's layout) — the scan summary ("проверено N · требуют обновления:
  M") never scrolls away again.
- The version marker moved to the **bottom-left corner** of that same
  footer (11px, `#4a5568`, selectable — exact AI Chat Exporter style),
  always visible on all tabs.
- The header subtitle now matches the AI Chat Exporter style exactly
  (10px, 0.6 opacity, caps text, no letter-spacing).

## [0.4.1] - 2026-08-28

### Changed
- Full visual restyle to match **AI Chat Exporter**: the palette moved from
  the neutral VS theme to the ACE dark-blue one (`#0b0f17` background,
  `#121826→#0e1420` gradient panels with 14px radius, `#1f2a3a` borders),
  the accent is now `#2f6bff`, status colors `#0ea56a`/`#f0b400`/`#ff5555`,
  and the font is Inter (system-ui fallback). Buttons, inputs, checkboxes,
  dropdowns, topic/chat lists, modals (12px radius, `#2a3345` border) and
  ~30 hardcoded colors were re-skinned to the same palette.
- Header is a topbar now: `Obsidianizer` + `LOCAL PROJECT CARDS` subtitle
  (AI Chat Exporter style), version moved to the right edge.

### Added
- Splash screen on GUI start — `Obsidianizer Заставка.png` (fullscreen,
  fades out after ~1.8 s), mirroring AI Chat Exporter's splash.
- `Obsidianizer.ico` in the repo root (multi-size, generated from
  `Obsidianizer ярлык.png`) for the Windows shortcut icon, plus
  `<link rel="icon">` and `icon.png` in the web assets; README documents
  how to attach the icon to a shortcut.

## [0.4.0] - 2026-08-28

### Added
- Context-sensitive Help: a "?" button pinned to the right of the tab bar
  opens a plain-language reference for the **active** tab (Obsidianize /
  Чат-обработка / AI-анализ). The icon next to the "?" follows the current
  tab; the modal is closable via ×, backdrop click or Esc and is draggable.
- The Obsidianize tab got the Obsidian crystal mark (inline SVG, purple
  gradient) instead of the generic folder emoji; the chat 💬 and robot 🤖
  marks stay.

First minor release: the 0.3.x line (recovery → fixes → docs) is complete,
feature development continues on 0.4.x.

## [0.3.9] - 2026-08-28

### Added
- Draggable divider between the Obsidianize scan table and the log: with
  hundreds of cards the table no longer pushes the processing results off
  screen — pull the divider up/down to resize the table area (120 px … 80%
  of the window), the chosen height persists across GUI restarts.
- The scan table scrolls internally and keeps its header row visible
  (sticky header) while scrolling long lists.

## [0.3.8] - 2026-08-28

### Documentation release
- README (EN+RU): new «Common situations» subsection in Folder Obsidianizer —
  plain-language answers (no code, no flags) to the questions every new user
  hits: a folder without a card, an accidental Obsidian-created note
  (Ctrl+click) and how adoption turns it into the notes file, the never-
  overwritten notes promise, honest stale marking after notes edits, no
  update pressure, and zero writes until an explicit run.
- `docs/obsidianize.md` + `.ru.md`: the same scenarios woven into the user
  flow sections with the actual status vocabulary (missing / conflict /
  stale / ok).

## [0.3.7] - 2026-08-28

### Documentation release
- README (EN+RU): new «Folder Obsidianizer» and «Obsidian integration
  (Templater hotkey)» sections — what project cards are, where the
  `_заметки.md` user layer lives, the 4-step hotkey setup (template
  location, `cli.bat` path, hotkey, self-diagnostics Notice), the `folders`
  CLI block with every flag, and the 3-tab GUI description (Obsidianize /
  Чат-обработка / AI-анализ).
- `docs/obsidianize.md` + `.ru.md` fully rewritten for the current
  RENDER_VERSION=8 card (Folders / Files / About / Gallery / Images /
  AI Review / Notes semantics, `⬆ Up` escaped row, Gallery vs Images rules,
  files-table sorting), a zero-to-fragrant user flow, the Templater and
  Shell commands refresh paths, and a technical appendix (manifest `base`,
  card-folder basis, deterministic legacy alignment, `--no-recursive`
  aggregates, `obsidianizer.log` scan diffs).
- `obsidian/obsidianizer-refresh.md` actualized: `--rel` in both hotkey
  variants, self-diagnostics Notice, explicit "replace the cli path"
  instruction, updated in-card section list.
- `AGENTS.md`: Folder Obsidianizer section synced with the current renderer
  (v5 structure, RENDER_VERSION = 8, basis independence, scoped-update
  aggregates, test coverage).

Release summary: v0.3.4 fixed the broken `⬆ Up` table row, v0.3.5 fixed the
Scanner ↔ Templater basis desync (no more phantom «добавлен/удалён»), v0.3.6
fixed `--no-recursive` zeroing the Folders table. 12 test-vault cards rebuilt
and verified (all `version: 8`, manifest `base` present, Up rows 4-cell,
no zero aggregates).

## [0.3.6] - 2026-08-28

### Fixed
- `--no-recursive` (Templater hotkey) zeroed the Folders table: the tree was
  trimmed to the root entry BEFORE `folder_stats`, so every subfolder row
  rendered `0 / 0 B / empty` while a GUI (recursive) run showed real values.
  Stats are now computed on the full subtree; the trim only scopes the
  card-writing loop. Regression test pins real aggregates and the
  "only the root card is written" contract.

## [0.3.5] - 2026-08-28

### Fixed
- Scanner ↔ Templater basis desync: manifest file keys and the folder
  fingerprint silently depended on the scan root. A card updated from its
  own folder (Templater hotkey) and then scanned from the project root
  produced «добавлен/удалён» for every file and a permanent stale status.
  Fix, both sides now compare in the card-folder basis:
  - `folder_fingerprint` keys files card-relatively (basis-independent;
    existing cards become stale once and regenerate on the next update);
  - `_manifest_payload` writes card-relative keys plus a `base` field
    (the card rel from the update root, for diagnostics);
  - `card_diff` aligns legacy manifests (no `base`) with a deterministic
    prefix rule: if any stored key carries the scan-root prefix, strip it
    from every key — no intersection counting.
- `_MANIFEST_RE` gained `re.DOTALL` so a multiline JSON dump can never
  silently break manifest parsing (defensive; current dumps are single-line).

### Added
- `obs_scan` logs every stale-card diff to `obsidianizer.log` (scan root,
  folder rel, added/removed/changed counters) for post-mortem checks.

## [0.3.4] - 2026-08-27

### Fixed
- The "⬆ Up" row in the Folders table rendered a raw alias pipe
  (`[[../X|Up]]`), which the Markdown table parser treated as a cell
  separator — the row split into 6 cells and broke the table. The pipe is
  now escaped (`[[../X\|Up]]`), matching the folder-row pattern; the table
  keeps exactly 4 cells. Test extended with a structural cell-count check;
  the root card (no Up row) is covered by the existing test.

## [0.3.3] - 2026-08-27

### Added
- Folder Obsidianizer UI (v5.12): «Базовый путь галереи (для проектов вне
  Vault)» input with persistence, «Принять существующую заметку как
  заметки» checkbox (adopt), ⚠ change rows under stale folders in the scan
  table (from `obs_scan` `changes`), a hint row for adoptable foreign
  notes, and a one-line scan summary in the status bar. Values restore from
  `defaults()` and persist on change.

## [0.3.2] - 2026-08-27

### Added
- Folder Obsidianizer backend for v5.12 GUI parity: automatic vault root
  detection (`_find_vault_root` walks up to `.obsidian`) when neither
  vault_root nor gallery_prefix is set; `set_obsidianize_gallery_prefix`
  bridge method; `obs_obsidianize` passes `adopt` and `gallery_prefix`.
- Smarter scan (`obs_scan`): per-folder `changes` list produced from the
  card's hidden manifest diff (`card_diff` / `format_changes` — added /
  removed / changed files, folder structure, project data), `adoptable`
  flag for foreign notes without a notes file, and a one-line summary
  (checked / unchanged / need update).

## [0.3.1] - 2026-08-27

### Added
- `obsidianizer folders` flags `--adopt`, `--gallery-prefix`, `--rel`
  (v5.12 infrastructure): adopt a foreign note into `<имя>_заметки.md`
  (1:1 rename), fallback vault path prefix for the `img-gallery` block when
  the project lives outside the vault, and the vault-relative root path for
  the "⬆ Up" link on local single-card updates. Unblocks the Obsidian
  Templater hotkey, which already passes these flags.

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