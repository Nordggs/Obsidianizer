"""Future GUI shell (planned — not implemented yet).

This module is deliberately free of behaviour. The GUI is a later milestone;
what exists now is only the *contract surface*:

- The UI talks to the core through the same pipeline the CLI uses, passing
  settings and an ``on_event`` callback.
- Event names and payloads come from ``events.py``; the UI never redefines
  them. ``events.py`` is the single source of truth.
- The UI knows nothing about Markdown or file types. It only renders events
  and shows progress — the core decides what happens with the files.

Conceptual integration (when the GUI is built):

    report = pipeline.run(
        registry, settings, llm,
        dry_run=..., prune=...,
        on_event=ui.handle_event,
    )

Expected UI surface (see docs/architecture.md, "UI layer"):

    source/target pickers, Ollama model + LLM toggle, dry-run/prune toggles,
    progress bar, current file, log, final summary
    (processed / skipped / errors), open-target button.

The default GUI is desktop (pywebview-based); the CLI remains the
automation interface. Dependencies for the GUI live in the ``ui`` package
extra, never in the core install.
"""

from .events import Event, EventType  # noqa: F401 - contract, re-exported for consumers

__all__ = ["Event", "EventType"]