# Development

> [Русский](development.ru.md) | **English**

## Environment

Requirements: Python 3.10+ (developed on 3.14), git.

```bash
git clone <repo> && cd Obsidianizer
python -m venv .venv
.venv\Scripts\activate          # Windows
# or: source .venv/bin/activate  # POSIX

pip install -e .[dev]
```

## Running tests

```bash
pytest
```

The suite is organized around safety first:

| Test file                | What it guards against                          |
|--------------------------|-------------------------------------------------|
| `test_guard.py`          | path-overlap refusals (data-destruction cases)  |
| `test_manifest.py`       | prune only owned files; atomic manifest writes  |
| `test_md_processor.py`   | metadata parsing; body preservation             |
| `test_registry.py`       | extension dispatch; unknown extensions          |
| `test_config.py`         | precedence CLI > config > defaults; save/load roundtrip + merge |
| `test_ui.py`             | GUI bridge: settings persistence, per-field folder pickers |
| `test_pipeline.py`       | incrementality; LLM degradation                |

## Fixtures

`tests/fixtures/` mirrors realistic raw layouts:

```
tests/fixtures/md/       sample raw Markdown (AI-chat header style)
tests/fixtures/media/    referenced binary assets
```

## Conventions

- `src` layout: the package must be consumed as an installed package, never as
  an ad-hoc local folder import.
- CLI argument handling lives only in `cli.py`.
- Paths are `pathlib.Path`; comparisons go through `Path.resolve()` and are
  case-normalized.
- All writes are atomic (`os.replace`).
- No cloud/AI-keys: the LLM client is local-Ollama only.
- Keep the core pipeline free of file-type specifics.

## Committing

See `AGENTS.md`. One logical change per commit; the project stays standalone.