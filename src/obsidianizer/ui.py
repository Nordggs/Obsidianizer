"""Desktop GUI (pywebview) — the primary user interface.

The GUI is a *frontend only*: every operation goes through the same core the
CLI uses (guard -> pipeline.run -> manifest). It knows nothing about Markdown
and renders the ``events.py`` contract. Event names live in ``events.py``;
this file never redefines them.

Layout of the bridge:
- JS calls ``window.pywebview.api.*`` (see ``web/app.js``).
- The core pushes progress through ``on_event`` -> ``_on_event``, which
  forwards each ``Event`` to ``window.pushEvent(...)`` in the UI.
- A ``logging.Handler`` forwards log lines to ``window.pushLog(...)``.

The bridge is headless-testable: without a webview ``window`` attribute
(``self.window`` is ``None``) events accumulate in ``self.events`` and no
``evaluate_js`` call is attempted.

Stop semantics are honest: ``cancel()`` only sets a flag that ``pipeline.run``
polls *between files*. The current file (including a long Ollama call) is
allowed to finish; the UI says "Остановка… текущий файл будет завершён".
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from . import __version__
from .config import Settings
from .events import Event, EventType
from .guard import GuardError, check as guard_check
from .llm import LLMClient
from .md_processor import MdProcessor
from .pipeline import Report
from .pipeline import run as run_pipeline
from .registry import ProcessorRegistry

RESOURCES = Path(__file__).resolve().parent / "web"


class _UiLogHandler(logging.Handler):
    """Bridge standard library log lines into the UI."""

    def __init__(self, on_record) -> None:
        super().__init__()
        self._on_record = on_record

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._on_record(record.getMessage())
        except Exception:  # noqa: BLE001 - log plumbing must never raise
            pass


def _make_registry() -> ProcessorRegistry:
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    return reg


class UIApp:
    """Bridge between the web UI and the core pipeline (headless-testable)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self.window = None  # pywebview attaches the window here automatically
        self.events: list[Event] = []
        self._cancel = False
        self._busy = False

    # ── helpers ────────────────────────────────────────────────────────────

    def _push(self, js: str) -> None:
        if self.window is not None:
            try:
                self.window.evaluate_js(js)
            except Exception:  # noqa: BLE001 - UI round-trips must not raise
                pass

    def _push_event(self, event: Event) -> None:
        payload = json.dumps(
            {
                "type": event.type.value,
                "path": event.path,
                "index": event.index,
                "total": event.total,
                "message": event.message,
            }
        )
        self._push(f"window.pushEvent({payload})")

    def _log(self, message: str) -> None:
        self._push(f"window.pushLog({json.dumps(message)})")

    # ── JS-visible API ─────────────────────────────────────────────────────

    def defaults(self) -> dict:
        o = self.settings.ollama
        return {
            "version": __version__,
            "source": str(self.settings.source),
            "target": str(self.settings.target),
            "llm_enabled": self.settings.llm_enabled,
            "model": o["model"],
            "temperature": o["temperature"],
        }

    def choose_folder(self) -> str | None:
        """Open a native folder picker; returns the chosen path or None."""
        if self.window is None:
            return None
        import webview

        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        if isinstance(result, list):
            return str(result[0])
        return str(result)

    def set_paths(self, source: str, target: str) -> dict:
        src = Path(source).expanduser()
        tgt = Path(target).expanduser()
        try:
            guard_check(src, tgt)
        except GuardError as exc:
            return {"ok": False, "error": str(exc)}
        self.settings.source = src
        self.settings.target = tgt
        return {"ok": True}

    def set_llm(self, enabled: bool, model: str) -> None:
        self.settings.llm_enabled = bool(enabled)
        if model:
            self.settings.ollama["model"] = model

    def scan(self) -> dict:
        try:
            reg = _make_registry()
            files = reg.scan(self.settings.source.resolve())
            return {
                "ok": True,
                "total": len(files),
                "files": [{"path": c.rel_path} for c in files],
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def start_run(self, opts: dict | None = None) -> dict:
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        opts = opts or {}
        self._busy = True
        self._cancel = False
        s = Settings()
        s.source = self.settings.source
        s.target = self.settings.target
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        threading.Thread(
            target=self._run_worker,
            args=(s, bool(opts.get("dry_run")), bool(opts.get("prune"))),
            daemon=True,
            name="obsidianizer-run",
        ).start()
        return {"ok": True}

    def cancel(self) -> bool:
        self._cancel = True
        self._log("Остановка… текущий файл будет завершён")
        return True

    def open_target(self) -> bool:
        tgt = self.settings.target.resolve()
        if not tgt.exists():
            return False
        if os.name == "nt":
            os.startfile(tgt)  # type: ignore[attr-defined]
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(tgt)])
        return True

    # ── internals ──────────────────────────────────────────────────────────

    def _run_worker(self, settings: Settings, dry_run: bool, prune: bool) -> None:
        try:
            self.run_now(settings, dry_run=dry_run, prune=prune)
        finally:
            self._busy = False

    def run_now(
        self, settings: Settings, *, dry_run: bool = False, prune: bool = False
    ) -> Report:
        """Synchronous pipeline run used by the worker thread and by tests."""
        llm: LLMClient | None = None
        if settings.llm_enabled:
            o = settings.ollama
            llm = LLMClient(
                endpoint=o["endpoint"],
                model=o["model"],
                timeout=o["timeout"],
                limit_chars=o["limit_chars"],
                temperature=o["temperature"],
                num_ctx=o["num_ctx"],
                prompt=o["prompt"],
            )
        return run_pipeline(
            _make_registry(),
            settings,
            llm,
            dry_run=dry_run,
            prune=prune,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def _on_event(self, event: Event) -> None:
        self.events.append(event)
        self._push_event(event)

    @property
    def is_busy(self) -> bool:
        return self._busy


def launch(initial_source: str | None = None, initial_target: str | None = None) -> None:
    """Open the pywebview window (the primary user interface)."""
    import webview

    app = UIApp()
    if initial_source:
        app.settings.source = Path(initial_source)
    if initial_target:
        app.settings.target = Path(initial_target)

    handler = _UiLogHandler(lambda msg: app._log(msg))
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

    window = webview.create_window(
        "Obsidianizer",
        url=str(RESOURCES / "app.html"),
        js_api=app,
        width=960,
        height=760,
        min_size=(720, 560),
        background_color="#1e1e1e",
    )
    webview.start(debug=False, http_server=True)