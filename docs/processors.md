# Processors

Processors are the only file-type-specific code in Obsidianizer. The core
pipeline (`scan → extract → enrich → emit`) never contains type-specific logic;
it only talks to the `Processor` interface.

## Interface (`base.py`)

```python
class Processor(ABC):
    extensions: frozenset[str]          # e.g. frozenset({".md"})

    def parse(self, path: pathlib.Path, rel_path: str) -> dict:
        """Extract flat metadata from the file.

        Returned dict keys are merged into the YAML frontmatter.
        raise ValueError on malformed content.
        """

    def body(self, path: pathlib.Path) -> str:
        """Return the original body verbatim (never transformed)."""

    def media_refs(self, path: pathlib.Path) -> list[str]:
        """Local media references used by this file.

        Paths are relative to the note's directory. Remote URLs return nothing.
        """
```

## Registry (`registry.py`)

Registration maps a file extension to a processor class:

```python
registry = ProcessorRegistry()
registry.register(".md", MdProcessor)
```

`registry.walk(source_root)` yields candidate `SourceFile` records — files the
pipeline will process. Anything not registered is ignored by the scan.

## Adding a new file type (e.g. SVG)

1. Create `svg_processor.py`:

```python
class SvgProcessor(Processor):
    extensions = frozenset({".svg"})

    def parse(self, path, rel_path) -> dict:
        # width/height via header sniff, title from <title> or filename
        return {"title": ..., "format": "svg"}

    def body(self, path) -> str:
        return path.read_text(encoding="utf-8")

    def media_refs(self, path) -> list[str]:
        return []   # or referenced raster assets
```

2. Register it once in the CLI/composition root:

```python
registry.register(".svg", SvgProcessor)
```

3. The core, emitter, manifest, index and prune machinery work unchanged.

## Rules for processors

- **Read-only.** Never modify `path`.
- **Deterministic.** Same file → same metadata.
- **Preserve the body.** Enrichment adds *around* the content, never rewrites it.
- **Fail locally.** A malformed file raises `ValueError`; the pipeline reports
  and continues with the rest of the batch.
- Return media references as *relative* paths; the emitter resolves them
  against the note's directory, then the source root.