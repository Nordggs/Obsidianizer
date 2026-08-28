"""Desktop GUI (pywebview) — the primary user interface.

The GUI is a *frontend only*: every operation goes through the same core the
CLI uses (guard -> pipeline.run -> manifest; postprocess.enrich for the AI
stage). It knows nothing about Markdown and renders the ``events.py``
contract. Event names live in ``events.py``; this file never redefines them.

Layout of the bridge:
- JS calls ``window.pywebview.api.*`` (see ``web/app.js``).
- The core pushes progress through ``on_event`` -> ``_on_event``, which
  forwards each ``Event`` to ``window.pushEvent(...)`` in the UI.
- A ``logging.Handler`` forwards log lines to ``window.pushLog(...)``.

Processing is two-stage and sequential, with physically separate outputs:
1. Import (fast, never calls the LLM) via ``run_now`` -> ``settings.target``
   (processed);
2. AI post-processing (``run_ai_now`` -> ``postprocess.enrich``) reading
   processed and writing ``settings.enriched``. Incremental: notes whose
   enriched ``ai_hash`` matches the processed ``source_hash`` are skipped, so
   an interrupted stage simply resumes and a re-import can never destroy AI
   results;
3. Topic builds (``run_topic_now`` -> ``topics.create_topic``): several
   selected chats merged into a single knowledge topic under
   ``enriched/topics``. Incremental via ``topic_hash`` (a hash of the selected
   chats' ``source_hash`` values);
4. Auto-grouping (``run_group_all_now`` -> ``topics.group_all``): the whole
   processed collection is clustered into topics by the LLM (no manual
   selection); each cluster of two or more chats goes through the same
   ``create_topic`` path.

The bridge is headless-testable: without a webview window (``self._window``
is ``None``) events accumulate in ``self.events`` and no ``evaluate_js``
call is attempted.

Stop semantics are honest: ``cancel()`` only sets a flag that pipeline/
postprocess poll *between files*. The current file (including a long Ollama
call) is allowed to finish; the UI says "Остановка… текущий файл будет
завершён".
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_AI_PROMPT,
    DEFAULT_CHAT_PROMPT,
    DEFAULT_FOLDERS_ANALYZE_PROMPT,
    DEFAULT_PROMPT,
    DEFAULT_TOPIC_MAP_PROMPT,
    DEFAULT_TOPIC_PROMPT,
    Settings,
)
from .events import Event, EventType
from .guard import GuardError, check as guard_check
from .llm import LLMClient
from .md_processor import MdProcessor
from .pipeline import Report
from .pipeline import run as run_pipeline
from .postprocess import EnrichReport
from .postprocess import enrich as postprocess_enrich
from .postprocess import split_file
from .registry import ProcessorRegistry
from .search import ChatIndex, SearchCandidate
from .topics import GroupReport, TopicReport
from .topics import chats_without_topic as find_chats_without_topic
from .topics import collect_chat_cards
from .topics import create_topic as build_topic
from .topics import delete_topic as build_delete_topic
from .topics import get_topic as build_get_topic
from .topics import group_all as build_group
from .topics import list_topics as build_list_topics
from .topics import rename_topic as build_rename_topic
from .topics import update_topic as build_update_topic

RESOURCES = Path(__file__).resolve().parent / "web"
LOG_FILE = Path(__file__).resolve().parents[2] / "obsidianizer.log"

logger = logging.getLogger("obsidianizer.ui")


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


def _make_llm(settings: Settings) -> LLMClient | None:
    """Build the AI post-processing client from settings, or None when off."""

    if not settings.llm_enabled:
        return None
    o = settings.ollama
    return LLMClient(
        endpoint=o["endpoint"],
        model=o["model"],
        timeout=o["timeout"],
        limit_chars=o["limit_chars"],
        temperature=o["temperature"],
        num_ctx=o["num_ctx"],
        prompt=o["prompt"],
        ai_prompt=o.get("ai_prompt", ""),
        topic_prompt=o.get("topic_prompt", ""),
        map_prompt=o.get("map_prompt", ""),
        chat_prompt=o.get("chat_prompt", ""),
        embed_model=o.get("embed_model", ""),
        chat_model=o.get("chat_model", ""),
    )


def _nearest_existing_dir(path: Path) -> Path:
    """Return ``path`` if it exists, otherwise the nearest existing parent.

    Keeps the folder picker from opening on a stale/never-created path (e.g. a
    saved config pointing at a folder that was deleted) and never lets a bad
    value crash the dialog.
    """

    p = Path(path).expanduser()
    if not p.is_absolute():
        try:
            p = p.resolve()
        except OSError:
            p = Path.cwd()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _find_vault_root(path: Path) -> str:
    """Walk up from ``path`` until a vault marker (.obsidian) is found."""

    cur = path.resolve()
    while True:
        if (cur / ".obsidian").is_dir():
            return str(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return ""


class UIApp:
    """Bridge between the web UI and the core pipeline (headless-testable)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        # pywebview 6 does NOT attach the window to js_api automatically;
        # ``launch()`` binds it explicitly. Kept private: the pywebview JS-API
        # generator walks js_api attributes recursively, and a public Window
        # reference would drive it into WebView2 COM objects (recursion, noise).
        # Stays None in headless tests.
        self._window = None
        # Separate AI-chat window (created lazily by ``open_chat_window``).
        self._chat_window = None
        # Separate floating help window (created lazily by ``open_help_window``).
        self._help_window = None
        self.events: list[Event] = []
        self._cancel = False
        self._busy = False
        # AI assistant chat state (history survives across turns, not restarts)
        self._chat_history: list[dict] = []
        self._chat_busy = False
        # Notes attached to the assistant context (lives here so both the main
        # window and the dedicated chat window share it).
        self._chat_context: list[str] = []
        # Search index for the assistant (lazy; invalidated on path change)
        self._index: ChatIndex | None = None
        self._last_found: list[dict] = []

    # ── helpers ────────────────────────────────────────────────────────────

    def _push(self, js: str, window: object | None = None) -> None:
        target = window if window is not None else self._window
        if target is not None:
            try:
                target.evaluate_js(js)
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
        # Chat events go to the dedicated chat window (never the main one).
        if event.type in (EventType.CHAT_REPLY, EventType.CHAT_FOUND, EventType.CHAT_ERROR):
            self._push(f"window.pushEvent({payload})", self._chat_window)
        else:
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
            "enriched": str(self.settings.enriched),
            "llm_enabled": self.settings.llm_enabled,
            "model": o["model"],
            "chat_model": o.get("chat_model", ""),
            "temperature": o["temperature"],
            "prune": self.settings.prune,
            "prune_enriched": self.settings.prune_enriched,
            "dry_run": self.settings.dry_run,
            "obsidianize_dir": self.settings.obsidianize_dir,
            "obsidianize_vault_root": self.settings.obsidianize_vault_root,
            "obsidianize_gallery_prefix": self.settings.obsidianize_gallery_prefix,
            "obsidianize_template": self.settings.obsidianize_template,
        }

    def choose_folder(self, kind: str = "source") -> str | None:
        """Open a native folder picker; returns the chosen path or None.

        Each picker starts in *its own* last-used directory: source starts in
        ``settings.source``, target in ``settings.target``, enriched in
        ``settings.enriched``. A missing saved path degrades to the nearest
        existing parent.
        """
        if self._window is None:
            return None
        import webview

        current = {
            "source": self.settings.source,
            "target": self.settings.target,
            "enriched": self.settings.enriched,
            "obsidianize": (
                Path(self.settings.obsidianize_dir)
                if self.settings.obsidianize_dir
                else self.settings.source
            ),
        }.get(kind, self.settings.source)
        start = _nearest_existing_dir(current)
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER, directory=str(start)
        )
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return str(result[0])
        return str(result)

    def _save_settings(self) -> None:
        """Persist settings to config.yml; a failed write must not break the UI."""

        try:
            self.settings.save()
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.warning("Не удалось сохранить настройки: %s", exc)

    def save_settings(self) -> None:
        """Public persistence hook (e.g. bound to the window-closed event)."""

        self._save_settings()

    def set_paths(self, source: str, target: str, enriched: str) -> dict:
        src = Path(source).expanduser()
        tgt = Path(target).expanduser()
        enc_raw = Path(enriched).expanduser() if enriched and enriched.strip() else tgt.parent / "enriched"
        enc = enc_raw
        try:
            guard_check(src, tgt)
            guard_check(tgt, enc)
            guard_check(src, enc)  # AI must never be able to write into raw
        except GuardError as exc:
            return {"ok": False, "error": str(exc)}
        self.settings.source = src
        self.settings.target = tgt
        self.settings.enriched = enc
        self._index = None  # paths changed — the search index must be rebuilt
        self._last_found = []
        self._save_settings()
        logger.info("Paths set: source=%s target=%s enriched=%s", src, tgt, enc)
        return {"ok": True}

    def set_llm(self, enabled: bool, model: str, chat_model: str = "") -> None:
        self.settings.llm_enabled = bool(enabled)
        if model:
            self.settings.ollama["model"] = model
        if chat_model:
            self.settings.ollama["chat_model"] = chat_model
        self._save_settings()

    def set_flags(self, dry_run: bool, prune: bool, prune_enriched: bool) -> None:
        self.settings.dry_run = bool(dry_run)
        self.settings.prune = bool(prune)
        self.settings.prune_enriched = bool(prune_enriched)
        self._save_settings()

    # ── Folder Obsidianizer (tab 1) ────────────────────────────────────────

    def set_obsidianize_dir(self, path: str) -> dict:
        """Remember the folder to scan (persisted in settings)."""
        self.settings.obsidianize_dir = str(path).strip()
        self._save_settings()
        return {"ok": True}

    def set_obsidianize_vault_root(self, path: str) -> dict:
        """Remember the vault root used for the img-gallery block."""
        self.settings.obsidianize_vault_root = str(path).strip()
        self._save_settings()
        return {"ok": True}

    def set_obsidianize_gallery_prefix(self, prefix: str) -> dict:
        """Remember the fallback vault-path prefix for the img-gallery block
        (used when the working tree is outside the vault / vault_root empty)."""
        self.settings.obsidianize_gallery_prefix = str(prefix).strip().strip("/")
        self._save_settings()
        return {"ok": True}

    def set_obsidianize_template(self, template: str) -> dict:
        """Remember the card template (github | classic)."""
        if template not in ("github", "classic"):
            return {"ok": False, "error": f"неизвестный шаблон: {template}"}
        self.settings.obsidianize_template = str(template).strip()
        self._save_settings()
        return {"ok": True}

    def set_int_vault(self, path: str) -> dict:
        """Remember the Integration vault folder (persisted in settings)."""
        self.settings.obsidianize_dir = str(path).strip()
        self._save_settings()
        return {"ok": True}

    def obs_scan(self, path: str = "") -> dict:
        """Read-only scan: folder tree + card statuses + change details.

        Writes nothing. For every folder with an existing card the response
        carries ``changes`` — a short human-readable list (добавлен/удалён/
        изменён файл, структура папок, данные проекта) produced by comparing
        the card's hidden manifest with the current state.
        """
        from .obsidianize import (
            ObsidianizeConfig,
            card_diff,
            card_path_for,
            card_status,
            format_changes,
            notes_file_path,
            scan_tree,
        )

        path = str(path or "").strip() or str(self.settings.obsidianize_dir).strip()
        if not path:
            return {"ok": False, "error": "не выбрана папка"}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"Папка не существует: {root}"}
        try:
            cfg = ObsidianizeConfig(template=self.settings.obsidianize_template)
            tree = scan_tree(root, cfg)
            logger.info("Obs scan: root=%s folders=%d", root, len(tree))
            folders = []
            for rel, folder in tree.items():
                counts = {cat: 0 for cat in ("drafting", "tables", "docs", "images", "other")}
                known: set[str] = set()
                for cat in ("drafting", "tables", "docs", "images"):
                    exts = set(cfg.categories.get(cat, []))
                    known |= exts
                    counts[cat] = sum(1 for f in folder.files if f.ext in exts)
                counts["other"] = sum(1 for f in folder.files if f.ext not in known and f.ext != "md")

                card_p = card_path_for(folder)
                notes_prev: str | None = None
                notes_p = notes_file_path(folder)
                if notes_p.exists():
                    try:
                        notes_prev = notes_p.read_text(encoding="utf-8")
                    except OSError:
                        notes_prev = None

                status = card_status(card_p, folder, template=cfg.template, notes_prev=notes_prev)

                changes: list[str] = []
                if status == "stale" and card_p.is_file():
                    try:
                        diff = card_diff(
                            card_p.read_text(encoding="utf-8"), folder, notes_prev
                        )
                    except OSError:
                        diff = None
                    changes = format_changes(diff) if diff else ["карточка устарела"]
                    if not changes:
                        changes = ["изменилось содержимое проекта"]
                    logger.info(
                        "Obs scan diff: rel=%s status=%s added=%d removed=%d "
                        "changed=%d folders_changed=%s notes_changed=%s",
                        rel,
                        status,
                        len(diff["added"]) if diff else -1,
                        len(diff["removed"]) if diff else -1,
                        len(diff["changed"]) if diff else -1,
                        diff.get("folders_changed") if diff else None,
                        diff.get("notes_changed") if diff else None,
                    )

                folders.append(
                    {
                        "rel": rel,
                        "path": str(folder.path),
                        "files": len(folder.files),
                        "subfolders": len(folder.subfolders),
                        "images": len(folder.images),
                        "categories": counts,
                        "card": status,
                        "changes": changes,
                        "adoptable": status == "conflict" and not notes_p.exists(),
                    }
                )
            changed_n = sum(1 for f in folders if f["card"] in ("stale", "missing"))
            return {
                "ok": True,
                "root": str(root),
                "folders": folders,
                "summary": (
                    f"Проверено: {len(folders)} · "
                    f"без изменений: {len(folders) - changed_n} · "
                    f"требуют обновления: {changed_n}"
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def obs_obsidianize(self, opts: dict | None = None) -> dict:
        """Create/refresh the Markdown cards in a background thread.

        ``opts``: ``{"path": str, "recursive": bool, "gallery": bool,
        "vault_root": str, "force": bool}``. Progress arrives via the
        ``OBS_*`` events; the source folder is never modified.
        """
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        opts = opts or {}
        path = str(opts.get("path") or "").strip() or str(self.settings.obsidianize_dir).strip()
        if not path:
            return {"ok": False, "error": "не выбрана папка"}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"Папка не существует: {root}"}
        from .obsidianize import ObsidianizeConfig

        vault_root = str(opts.get("vault_root") or self.settings.obsidianize_vault_root)
        gallery_prefix = str(
            opts.get("gallery_prefix") or self.settings.obsidianize_gallery_prefix
        )
        # If the scanned folder lives inside an Obsidian vault and neither
        # vault_root nor gallery_prefix is set, detect the vault root so the
        # Gallery section appears the same way as with the hotkey update.
        if not vault_root and not gallery_prefix:
            vault_root = _find_vault_root(root)

        cfg = ObsidianizeConfig(
            force=bool(opts.get("force")),
            adopt=bool(opts.get("adopt")),
            img_gallery=bool(opts.get("gallery", True)),
            vault_root=vault_root,
            gallery_prefix=gallery_prefix,
            template=str(opts.get("template") or self.settings.obsidianize_template),
        )
        self.settings.obsidianize_dir = str(root)
        self._save_settings()
        self._busy = True
        self._cancel = False
        logger.info(
            "Obsidianize: %s recursive=%s gallery=%s", root, bool(opts.get("recursive", True)), cfg.img_gallery
        )
        threading.Thread(
            target=self._run_obs_worker,
            args=(root, cfg, bool(opts.get("recursive", True))),
            daemon=True,
            name="obsidianizer-obs",
        ).start()
        return {"ok": True}

    def _run_obs_worker(self, root: Path, cfg, recursive: bool) -> None:
        from .obsidianize import scan_tree, update_cards

        try:
            tree = scan_tree(root, cfg)
            total = len(tree) if recursive else 1
            self._on_event(
                Event(EventType.OBS_SCAN_STARTED, total=total, message=str(root))
            )
            counter = {"n": 0}

            def on_progress(rel: str, action: str) -> None:
                counter["n"] += 1
                self._on_event(
                    Event(
                        EventType.OBS_FOLDER_DONE,
                        path=rel,
                        index=counter["n"],
                        total=total,
                        message=action,
                    )
                )

            summary = update_cards(
                root, cfg, on_progress=on_progress, recursive=recursive
            )
            self._on_event(
                Event(
                    EventType.OBS_FINISHED,
                    message=json.dumps(
                        {
                            "scanned": summary.scanned,
                            "created": summary.created,
                            "updated": summary.updated,
                            "skipped": summary.skipped,
                            "conflicts": summary.conflicts,
                        }
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - errors surface as events
            logger.error("Obsidianize failed: %s", exc)
            self._on_event(Event(EventType.OBS_ERROR, message=str(exc)))
        finally:
            self._busy = False

    def obs_open_folder(self, path: str = "") -> dict:
        """Open the scanned folder in the system file manager."""
        if self._window is None:
            return {"ok": False, "error": "нет окна"}
        target = Path(path or self.settings.obsidianize_dir).expanduser()
        if not target.is_dir():
            return {"ok": False, "error": f"Папка не существует: {target}"}
        try:
            os.startfile(str(target))
        except AttributeError:
            import subprocess
            import sys

            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(target)])
        return {"ok": True}

    # ── Obsidian integration (Templater) ──────────────────────────────────

    def obs_integration_status(self, opts: dict | None = None) -> dict:
        """✓/✗ snapshot of the Obsidian integration for the GUI/help window.

        ``opts``: ``{"vault": str}`` — falls back to the Obsidianize folder.
        """
        from .integration import integration_status

        opts = opts or {}
        vault = str(
            opts.get("vault") or self.settings.obsidianize_dir or ""
        ).strip()
        if not vault:
            return {
                "ok": True,
                "vault": "",
                "vault_found": False,
                "templater_installed": False,
                "templates_folder": None,
                "template_installed": False,
                "cli_path": "",
                "cli_exists": False,
            }
        res = integration_status(Path(vault).expanduser())
        res["ok"] = True
        return res

    def obs_integration_install(self, opts: dict | None = None) -> dict:
        """Install/repair the Templater update template into the vault.

        ``opts``: ``{"vault": str, "repair": bool}``. When no vault is given,
        a native folder picker opens (prefilled with the Obsidianize folder).
        """
        from .integration import install_obsidian_integration

        opts = opts or {}
        vault = str(opts.get("vault") or self.settings.obsidianize_dir or "").strip()
        if not vault:
            picked = self.choose_folder()
            if not picked:
                return {"ok": False, "error": "Папка не выбрана"}
            vault = picked

        res = install_obsidian_integration(
            Path(vault).expanduser(), repair=bool(opts.get("repair"))
        )
        if res.get("ok"):
            self.settings.obsidianize_dir = vault
            self._save_settings()
            logger.info("Obsidian integration installed: %s", res.get("target"))
        return res

    # ── AI folder review (tab 3) ──────────────────────────────────────────

    def review_run(self, opts: dict | None = None) -> dict:
        """Generate ``<folder>_обзор.md`` for the selected folders in a thread.

        ``opts``: ``{"path": str, "rels": [str], "include_text": bool}``.
        Uses the chat model (``ollama.chat_model``, falls back to the main
        one). Progress arrives via the ``REVIEW_*`` events. Source files are
        never modified — only the review files are written.
        """
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        opts = opts or {}
        path = str(opts.get("path") or "").strip() or str(self.settings.obsidianize_dir).strip()
        if not path:
            return {"ok": False, "error": "не выбрана папка"}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"Папка не существует: {root}"}
        rels = [str(r) for r in (opts.get("rels") or []) if r is not None]
        if not rels:
            return {"ok": False, "error": "выберите хотя бы одну папку"}
        llm = _make_llm(self.settings)
        if llm is None:
            return {"ok": False, "error": "LLM отключён — включите «AI-постобработку»"}
        self.settings.obsidianize_dir = str(root)
        self._save_settings()
        self._busy = True
        self._cancel = False
        logger.info(
            "Folder review: %s (%d папок, include_text=%s)",
            root,
            len(rels),
            bool(opts.get("include_text", True)),
        )
        threading.Thread(
            target=self._run_review_worker,
            args=(root, rels, bool(opts.get("include_text", True)), llm),
            daemon=True,
            name="obsidianizer-review",
        ).start()
        return {"ok": True}

    def _run_review_worker(self, root: Path, rels: list[str], include_text: bool, llm) -> None:
        from .obsidianize import ObsidianizeConfig, scan_tree
        from .review import build_review_markdown, build_request, collect_payload, save_review

        try:
            prompt = str(
                self.settings.ollama.get("folders_prompt")
                or DEFAULT_FOLDERS_ANALYZE_PROMPT
            )
            cfg = ObsidianizeConfig()
            tree = scan_tree(root, cfg)
            model = str(getattr(llm, "chat_model", "") or getattr(llm, "model", ""))
            self._on_event(
                Event(EventType.REVIEW_STARTED, total=len(rels), message=str(root))
            )
            ok_files: list[str] = []
            err_count = 0
            for i, rel in enumerate(rels, start=1):
                folder = tree.get(rel)
                if folder is None:
                    err_count += 1
                    self._on_event(
                        Event(
                            EventType.REVIEW_FOLDER_DONE,
                            path=rel,
                            index=i,
                            total=len(rels),
                            message="error",
                        )
                    )
                    continue
                try:
                    payload = collect_payload(folder, include_text=include_text, cfg=cfg)
                    request = build_request([payload], include_text=include_text)
                    reply = llm.chat([{"role": "user", "content": request}], prompt)
                    if not reply or not reply.strip():
                        raise RuntimeError("модель не ответила")
                    target = save_review(
                        folder, build_review_markdown(reply.strip(), model=model)
                    )
                    ok_files.append(str(target))
                    self._on_event(
                        Event(
                            EventType.REVIEW_FOLDER_DONE,
                            path=rel,
                            index=i,
                            total=len(rels),
                            message="ok",
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one folder must not kill the run
                    err_count += 1
                    logger.error("Folder review failed: %s: %s", rel, exc)
                    self._on_event(
                        Event(
                            EventType.REVIEW_FOLDER_DONE,
                            path=rel,
                            index=i,
                            total=len(rels),
                            message="error",
                        )
                    )
            self._on_event(
                Event(
                    EventType.REVIEW_FINISHED,
                    message=json.dumps(
                        {
                            "ok": len(ok_files),
                            "errors": err_count,
                            "files": ok_files,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - errors surface as events
            logger.error("Folder review run failed: %s", exc)
            self._on_event(Event(EventType.REVIEW_ERROR, message=str(exc)))
        finally:
            self._busy = False

    def list_models(self) -> dict:
        """Fetch installed Ollama models. Sends no chat content — names only.

        ``ok: False`` (Ollama unreachable) must not break the UI: the caller
        keeps the current model and shows a status message instead.
        """
        o = self.settings.ollama
        client = LLMClient(
            endpoint=o["endpoint"],
            model=o["model"],
            timeout=10.0,
        )
        models = client.list_models()
        if models is None:
            return {"ok": False, "error": "Ollama недоступен"}
        return {"ok": True, "models": models}

    def get_prompts(self) -> dict:
        """Return the prompt templates plus their built-in defaults.

        Pure local read — no Ollama call, no content is ever sent.
        """
        o = self.settings.ollama
        return {
            "prompt": o.get("prompt", ""),
            "ai_prompt": o.get("ai_prompt", ""),
            "topic_prompt": o.get("topic_prompt", ""),
            "map_prompt": o.get("map_prompt", ""),
            "default_prompt": DEFAULT_PROMPT,
            "default_ai_prompt": DEFAULT_AI_PROMPT,
            "default_topic_prompt": DEFAULT_TOPIC_PROMPT,
            "default_map_prompt": DEFAULT_TOPIC_MAP_PROMPT,
        }

    def set_prompt(self, kind: str, value: str) -> dict:
        if kind not in ("prompt", "ai_prompt", "topic_prompt", "map_prompt"):
            return {"ok": False, "error": "Неизвестный промпт"}
        self.settings.ollama[kind] = value
        self._save_settings()
        return {"ok": True}

    def reset_prompt(self, kind: str) -> dict:
        """Restore a prompt to its built-in default (never overwritten)."""

        default = {
            "prompt": DEFAULT_PROMPT,
            "ai_prompt": DEFAULT_AI_PROMPT,
            "topic_prompt": DEFAULT_TOPIC_PROMPT,
            "map_prompt": DEFAULT_TOPIC_MAP_PROMPT,
        }.get(kind)
        if default is None:
            return {"ok": False, "error": "Неизвестный промпт"}
        self.settings.ollama[kind] = default
        self._save_settings()
        return {"ok": True, "value": default}

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

    def list_chats(self) -> dict:
        """List the processed chats available for topic merging.

        Scans ``settings.target`` and returns compact cards: relative path,
        title, service, date, message count and summary. Foreign notes without
        the ``source_hash`` ownership marker are excluded.
        """
        try:
            cards = collect_chat_cards(self.settings.target.resolve())
            return {"ok": True, "files": cards}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def list_topics(self) -> dict:
        """List the topic notes under ``settings.enriched/topics``."""
        try:
            topics = build_list_topics(self.settings.enriched.resolve())
            return {"ok": True, "topics": topics}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def get_topic(self, topic_id: str) -> dict:
        """Return one topic note (body + frontmatter summary)."""
        try:
            topic = build_get_topic(self.settings.enriched.resolve(), str(topic_id))
            if topic is None:
                return {"ok": False, "error": "Тема не найдена"}
            return {"ok": True, "topic": topic}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def rename_topic(self, topic_id: str, new_name: str) -> dict:
        """Rename a topic note in place (keeps ``topic_id``)."""
        try:
            return build_rename_topic(
                self.settings.enriched.resolve(),
                str(topic_id),
                str(new_name),
                on_event=self._on_event,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def delete_topic(self, topic_id: str) -> dict:
        """Delete a topic note (ownership marker verified)."""
        try:
            return build_delete_topic(
                self.settings.enriched.resolve(),
                str(topic_id),
                on_event=self._on_event,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def chats_without_topic(self) -> dict:
        """Chat cards not referenced by any topic note."""
        try:
            cards = find_chats_without_topic(
                self.settings.target.resolve(),
                self.settings.enriched.resolve(),
            )
            return {"ok": True, "files": cards}
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
        s.enriched = self.settings.enriched
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        ai = bool(opts.get("ai"))
        prune_ai = bool(opts.get("prune_ai"))
        logger.info(
            "Run started: source=%s target=%s enriched=%s llm=%s dry_run=%s "
            "prune=%s ai=%s prune_ai=%s",
            s.source,
            s.target,
            s.enriched,
            s.llm_enabled,
            bool(opts.get("dry_run")),
            bool(opts.get("prune")),
            ai,
            prune_ai,
        )
        self._save_settings()
        threading.Thread(
            target=self._run_worker,
            args=(s, bool(opts.get("dry_run")), bool(opts.get("prune")), ai, prune_ai),
            daemon=True,
            name="obsidianizer-run",
        ).start()
        return {"ok": True}

    def start_ai(self, opts: dict | None = None) -> dict:
        """Start the AI post-processing stage alone (skip re-import)."""
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        opts = opts or {}
        s = Settings()
        s.source = self.settings.source
        s.target = self.settings.target
        s.enriched = self.settings.enriched
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        prune_ai = bool(opts.get("prune_ai"))
        self._busy = True
        self._cancel = False
        logger.info(
            "AI run started: target=%s enriched=%s llm=%s prune_ai=%s",
            s.target,
            s.enriched,
            s.llm_enabled,
            prune_ai,
        )
        self._save_settings()
        threading.Thread(
            target=self._run_ai_worker,
            args=(s, prune_ai),
            daemon=True,
            name="obsidianizer-ai",
        ).start()
        return {"ok": True}

    def create_topic(self, opts: dict | None = None) -> dict:
        """Start a topic build: merge the selected chats into one topic note.

        ``opts``: ``{"files": [rel, ...]}`` — relative paths inside
        ``settings.target``. Runs in a background thread; progress arrives via
        the ``TOPIC_*`` events.
        """
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        opts = opts or {}
        rel_files = opts.get("files") or []
        if not isinstance(rel_files, list) or not rel_files:
            return {"ok": False, "error": "не выбраны файлы"}
        rel_files = [str(f).replace("\\", "/") for f in rel_files]
        s = Settings()
        s.source = self.settings.source
        s.target = self.settings.target
        s.enriched = self.settings.enriched
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        self._busy = True
        self._cancel = False
        logger.info(
            "Topic build started: %d файл(ов) → %s", len(rel_files), s.enriched
        )
        self._save_settings()
        threading.Thread(
            target=self._run_topic_worker,
            args=(s, rel_files),
            daemon=True,
            name="obsidianizer-topic",
        ).start()
        return {"ok": True}

    def group_all(self) -> dict:
        """Start an auto-grouping run: cluster the whole processed collection
        into topic notes (no manual selection).

        The clustering pass runs in a background thread; progress arrives via
        the ``TOPIC_*`` events (``TOPIC_MAP_STARTED`` first, then one
        ``TOPIC_FILE_*`` per created topic).
        """
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        s = Settings()
        s.source = self.settings.source
        s.target = self.settings.target
        s.enriched = self.settings.enriched
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        self._busy = True
        self._cancel = False
        logger.info(
            "Авто-группировка: target=%s enriched=%s llm=%s",
            s.target, s.enriched, s.llm_enabled,
        )
        self._save_settings()
        threading.Thread(
            target=self._run_group_worker,
            args=(s,),
            daemon=True,
            name="obsidianizer-group",
        ).start()
        return {"ok": True}

    def update_topic(self, opts: dict | None = None) -> dict:
        """Regenerate an existing topic with a new chat selection (background).

        ``opts``: ``{"topic_id": str, "files": [rel, ...]}``. The topic keeps
        its name and ``topic_id``; only the knowledge card is recomputed.
        Progress arrives via the ``TOPIC_*`` events.
        """
        if self._busy:
            return {"ok": False, "error": "запуск уже выполняется"}
        opts = opts or {}
        topic_id = str(opts.get("topic_id") or "")
        rel_files = opts.get("files") or []
        if not topic_id:
            return {"ok": False, "error": "не указана тема"}
        if not isinstance(rel_files, list) or not rel_files:
            return {"ok": False, "error": "не выбраны файлы"}
        rel_files = [str(f).replace("\\", "/") for f in rel_files]
        s = Settings()
        s.source = self.settings.source
        s.target = self.settings.target
        s.enriched = self.settings.enriched
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        self._busy = True
        self._cancel = False
        logger.info(
            "Topic update: %s ← %d файл(ов) → %s", topic_id, len(rel_files), s.enriched
        )
        self._save_settings()
        threading.Thread(
            target=self._run_update_topic_worker,
            args=(s, topic_id, rel_files),
            daemon=True,
            name="obsidianizer-topic-update",
        ).start()
        return {"ok": True}

    def cancel(self) -> bool:
        self._cancel = True
        self._log("Остановка… текущий файл будет завершён")
        return True

    # ── AI assistant chat ─────────────────────────────────────────────────

    def chat_history(self) -> dict:
        """Return the current assistant chat history (for UI restore)."""

        return {"ok": True, "messages": [dict(m) for m in self._chat_history]}

    def chat_clear(self) -> dict:
        """Drop the assistant chat history and the attached notes."""

        self._chat_history = []
        self._chat_context = []
        self._push("window.chatContextChanged && window.chatContextChanged()", self._chat_window)
        return {"ok": True}

    def chat_found(self) -> dict:
        """Return the candidate chats found for the last assistant question."""

        return {"ok": True, "files": self._last_found}

    def chat_context(self) -> dict:
        """Return the notes attached to the assistant context."""

        return {"ok": True, "rels": list(self._chat_context)}

    def set_chat_context(self, rels: list[str]) -> dict:
        """Replace the notes attached to the assistant context.

        Pushes ``chatContextChanged`` to the chat window so its chips refresh.
        """
        norm = [str(r).replace("\\", "/") for r in rels if str(r).strip()]
        self._chat_context = norm
        self._push("window.chatContextChanged && window.chatContextChanged()", self._chat_window)
        logger.info("Chat context set: %d заметок", len(norm))
        return {"ok": True, "count": len(norm)}

    def send_chat_context_request(self) -> dict:
        """Bridge "Attach notes…" from the chat window to the main topic picker.

        The main window opens its note picker in chat-context mode; on confirm
        it calls ``set_chat_context`` back.
        """
        self._push("window.openChatAttach && window.openChatAttach()")
        return {"ok": True}

    def open_chat_window(self) -> dict:
        """Open (or refocus) the dedicated AI-chat window.

        Idempotent: reusing an already-open window, creating one on demand.
        The chat UI lives in its own native window so it can be dragged beyond
        the main window's bounds.
        """
        if self._window is None:
            # Headless (tests / CLI) — nothing to attach a window to.
            return {"ok": True, "opened": False}
        import webview

        if self._chat_window is not None and not getattr(self._chat_window, "closed", False):
            try:
                self._chat_window.show()
            except Exception:  # noqa: BLE001 - best-effort focus
                pass
            return {"ok": True, "opened": False}

        window = webview.create_window(
            "AI-чат",
            url=str(RESOURCES / "chat.html"),
            js_api=self,
            width=680,
            height=760,
            min_size=(460, 520),
            background_color="#1e1e1e",
        )
        self._chat_window = window
        try:
            window.events.closed += lambda: setattr(self, "_chat_window", None)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        return {"ok": True, "opened": True}

    def open_help_window(self, tab: str = "obsidianize") -> dict:
        """Open (or refocus) the floating help window.

        Idempotent, mirrors ``open_chat_window``: the help lives in its own
        native window so it can be dragged beyond the main window's bounds.
        ``tab`` selects the initial section: obsidianize | chat | ai.
        """
        if self._window is None:
            # Headless (tests / CLI) — nothing to attach a window to.
            return {"ok": True, "opened": False}
        import webview

        if self._help_window is not None and not getattr(self._help_window, "closed", False):
            try:
                self._help_window.show()
            except Exception:  # noqa: BLE001 - best-effort focus
                pass
            self._push(
                f"window.showHelpTab && window.showHelpTab({json.dumps(tab)})",
                self._help_window,
            )
            return {"ok": True, "opened": False}

        safe = tab if tab in ("obsidianize", "chat", "ai") else "obsidianize"
        window = webview.create_window(
            "Obsidianizer — Справка",
            url=str(RESOURCES / "help.html") + "#" + safe,
            js_api=self,
            width=600,
            height=700,
            min_size=(420, 420),
            background_color="#0b0f17",
        )
        self._help_window = window
        try:
            window.events.closed += lambda: setattr(self, "_help_window", None)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        return {"ok": True, "opened": True}

    def close_help_window(self) -> dict:
        """Close the floating help window (button inside help.html)."""
        if self._help_window is not None:
            try:
                self._help_window.destroy()
            except Exception:  # noqa: BLE001 - already gone
                pass
            self._help_window = None
        return {"ok": True}

    def send_chat_topic_request(self, rels: list[str]) -> dict:
        """Bridge "Add found chats to topic" from the chat window to the main UI.

        The chat window has no topic editor; it forwards the selected rels to
        ``window.openTopicFromFound(rels)`` in the main window instead.
        """
        norm = [str(r).replace("\\", "/") for r in rels if str(r).strip()]
        if not norm:
            return {"ok": False, "error": "нет выбранных чатов"}
        payload = json.dumps(norm, ensure_ascii=False)
        self._push(f"window.openTopicFromFound({payload})")
        return {"ok": True, "count": len(norm)}

    def _get_index(self, settings: Settings) -> ChatIndex:
        """Build (or reuse) the search index for the current settings paths."""

        target = settings.target.resolve()
        index_root = settings.enriched.resolve() / ".obsidianizer"
        llm = _make_llm(settings)
        if (
            self._index is None
            or self._index.target != target
            or self._index.index_root != index_root
        ):
            self._index = ChatIndex(
                target,
                index_root,
                llm=llm,
                embed_model=str(settings.ollama.get("embed_model") or "nomic-embed-text:latest"),
                top_k=int(settings.ollama.get("search_top_k") or 30),
            )
        else:
            self._index.llm = llm
            self._index.embed_model = str(
                settings.ollama.get("embed_model") or self._index.embed_model
            )
            self._index.top_k = int(settings.ollama.get("search_top_k") or self._index.top_k)
        return self._index

    def _candidate_payload(self, cand) -> dict:
        return {
            "rel": cand.rel,
            "title": cand.title,
            "service": cand.service,
            "date": cand.date,
            "score": cand.score,
            "snippet": cand.snippet,
            "fragments": cand.fragments,
            "matched": cand.matched,
            "partial": cand.partial,
            "full": bool(cand.full),
        }

    @staticmethod
    def _build_search_block(
        found: list[SearchCandidate], limit: int = 10, frag_chars: int = 600
    ) -> str:
        """Turn the found candidates into a compact analyst context block.

        Only the top ``limit`` candidates reach the model (the full list stays
        in the UI): each carries the best matching chunk as its fragment — the
        most relevant place in the dialog, not the head of the file.
        """

        partial = any(c.partial for c in found)
        lines = [
            f"Результаты поиска по коллекции: всего {len(found)} чат(ов), "
            f"ниже топ-{min(limit, len(found))} по убыванию релевантности. "
            "Отвечай, опираясь на эти найденные чаты:"
        ]
        if partial:
            lines.append(
                "Часть результатов — частичные совпадения: не все слова запроса "
                "встретились в одном чате."
            )
        for i, c in enumerate(found[:limit], 1):
            lines.append(f"{i}. {c.title} ({c.rel})")
            if c.snippet:
                lines.append(f"   Фрагмент: {c.snippet[:frag_chars]}")
        return "\n".join(lines)

    def chat_send(self, opts: dict | None = None) -> dict:
        """Send one user message to the assistant (background).

        ``opts``: ``{"message": str, "context": [rel, ...]}`` — ``context``
        lists processed notes attached to the system prompt. The reply arrives
        via the ``CHAT_REPLY`` event (``CHAT_ERROR`` on failure).
        """
        if self._chat_busy:
            return {"ok": False, "error": "ассистент уже отвечает"}
        opts = opts or {}
        message = str(opts.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "пустое сообщение"}
        context = opts.get("context")
        if context is None:
            context = list(self._chat_context)
        if not isinstance(context, list):
            context = []
        context = [str(r).replace("\\", "/") for r in context if str(r).strip()]
        s = Settings()
        s.source = self.settings.source
        s.target = self.settings.target
        s.enriched = self.settings.enriched
        s.llm_enabled = self.settings.llm_enabled
        s.ollama = dict(self.settings.ollama)
        self._chat_busy = True
        logger.info(
            "Chat: сообщение #%d, контекст: %d заметок",
            len(self._chat_history) + 1,
            len(context),
        )
        threading.Thread(
            target=self._run_chat_worker,
            args=(s, message, context),
            daemon=True,
            name="obsidianizer-chat",
        ).start()
        return {"ok": True}

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

    def open_enriched(self) -> bool:
        enc = self.settings.enriched.resolve()
        if not enc.exists():
            return False
        if os.name == "nt":
            os.startfile(enc)  # type: ignore[attr-defined]
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(enc)])
        return True

    def open_note(self, rel: str) -> bool:
        """Open one processed note (``rel``) with the OS default app.

        Used by the chat "sources" chips: click a found chat → open its file.
        """
        rel_norm = str(rel).replace("\\", "/").lstrip("/")
        candidate = self.settings.target.resolve().joinpath(*rel_norm.split("/"))
        if not candidate.is_file():
            return False
        if os.name == "nt":
            os.startfile(candidate)  # type: ignore[attr-defined]
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(candidate)])
        return True

    # ── internals ──────────────────────────────────────────────────────────

    def _run_worker(
        self,
        settings: Settings,
        dry_run: bool,
        prune: bool,
        ai: bool,
        prune_ai: bool,
    ) -> None:
        try:
            self.run_now(settings, dry_run=dry_run, prune=prune)
            if ai and not dry_run:
                self.run_ai_now(settings, prune=prune_ai)
        finally:
            self._busy = False

    def _run_ai_worker(self, settings: Settings, prune_ai: bool) -> None:
        try:
            self.run_ai_now(settings, prune=prune_ai)
        finally:
            self._busy = False

    def _run_topic_worker(self, settings: Settings, rel_files: list[str]) -> None:
        try:
            self.run_topic_now(settings, rel_files)
        finally:
            self._busy = False

    def _run_group_worker(self, settings: Settings) -> None:
        try:
            self.run_group_all_now(settings)
        finally:
            self._busy = False

    def _run_update_topic_worker(self, settings: Settings, topic_id: str, rel_files: list[str]) -> None:
        try:
            self.run_update_topic_now(settings, topic_id, rel_files)
        finally:
            self._busy = False

    def run_now(
        self, settings: Settings, *, dry_run: bool = False, prune: bool = False
    ) -> Report:
        """Synchronous import run used by the worker thread and by tests.

        Import is intentionally LLM-free: AI enrichment happens in the second
        stage (``run_ai_now`` / ``postprocess.enrich``).
        """
        return run_pipeline(
            _make_registry(),
            settings,
            None,  # no LLM during import
            dry_run=dry_run,
            prune=prune,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def run_ai_now(self, settings: Settings, *, prune: bool = False) -> EnrichReport:
        """Synchronous AI post-processing run used by workers and tests.

        Reads ``settings.target`` (processed) and writes ``settings.enriched``.
        """
        llm = _make_llm(settings)
        if llm is None:
            report = EnrichReport()
            report.critical_error = "LLM отключён — включите «AI-постобработку»"
            self._on_event(Event(type=EventType.AI_FINISHED, message=report.critical_error))
            return report
        return postprocess_enrich(
            settings.target.resolve(),
            settings.enriched.resolve(),
            llm,
            prune=prune,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def run_topic_now(self, settings: Settings, rel_files: list[str]) -> TopicReport:
        """Synchronous topic build used by the worker thread and by tests.

        Merges the selected chats from ``settings.target`` into one topic note
        under ``settings.enriched/topics``.
        """
        llm = _make_llm(settings)
        if llm is None:
            report = TopicReport()
            report.critical_error = "LLM отключён — включите «AI-постобработку»"
            self._on_event(
                Event(type=EventType.TOPIC_FINISHED, message=report.critical_error)
            )
            return report
        return build_topic(
            settings.target.resolve(),
            settings.enriched.resolve(),
            rel_files,
            llm,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def run_group_all_now(self, settings: Settings) -> GroupReport:
        """Synchronous auto-grouping run used by the worker thread and by tests.

        Clusters the whole ``settings.target`` collection into topics under
        ``settings.enriched/topics`` (no manual selection).
        """
        llm = _make_llm(settings)
        if llm is None:
            report = GroupReport()
            report.critical_error = "LLM отключён — включите «AI-постобработку»"
            self._on_event(
                Event(type=EventType.TOPIC_FINISHED, message=report.critical_error)
            )
            return report
        return build_group(
            settings.target.resolve(),
            settings.enriched.resolve(),
            llm,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def run_update_topic_now(self, settings: Settings, topic_id: str, rel_files: list[str]) -> TopicReport:
        """Synchronous topic update used by the worker thread and by tests.

        Regenerates an existing topic (``settings.enriched/topics``) over a new
        chat selection from ``settings.target``.
        """
        llm = _make_llm(settings)
        if llm is None:
            report = TopicReport()
            report.critical_error = "LLM отключён — включите «AI-постобработку»"
            self._on_event(
                Event(type=EventType.TOPIC_FINISHED, message=report.critical_error)
            )
            return report
        return build_update_topic(
            settings.enriched.resolve(),
            settings.target.resolve(),
            topic_id,
            rel_files,
            llm,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def _run_chat_worker(self, settings: Settings, message: str, context: list[str]) -> None:
        try:
            self.run_chat_now(settings, message, context)
        finally:
            self._chat_busy = False

    def run_chat_now(self, settings: Settings, message: str, context: list[str]) -> str:
        """Synchronous assistant turn used by the worker thread and by tests.

        Searches the collection first, feeds the found candidates to the model
        as an analyst, stores the reply and pushes ``CHAT_REPLY`` /
        ``CHAT_ERROR`` events for the UI. The found sources arrive separately
        via ``CHAT_FOUND`` (deterministic — never parsed from the LLM text).
        """
        llm = _make_llm(settings)
        if llm is None:
            error = "LLM отключён — включите «AI-постобработку»"
            self._on_event(Event(type=EventType.CHAT_ERROR, message=error))
            return ""
        system = str(settings.ollama.get("chat_prompt") or DEFAULT_CHAT_PROMPT)

        found: list[SearchCandidate] = []
        try:
            index = self._get_index(settings)
            index.refresh()
            found = index.search(message)
        except Exception as exc:  # noqa: BLE001 - search must never kill the chat
            logger.warning("Поиск по коллекции недоступен: %s", exc)
            found = []

        if found:
            full_k = int(settings.ollama.get("search_full_k") or 3)
            for c in found[:full_k]:
                c.full = True
        self._last_found = [self._candidate_payload(c) for c in found]

        if found:
            frag_chars = int(settings.ollama.get("search_frag_chars") or 600)
            system += "\n\n" + self._build_search_block(found, frag_chars=frag_chars)
            full_text = self._build_full_sources(
                settings,
                found[:full_k],
                int(settings.ollama.get("search_full_chars") or 9000),
            )
            if full_text:
                system += (
                    "\n\nПолные тексты наиболее релевантных чатов (для вопросов по "
                    "содержимому диалогов ищи итоговое решение ближе к концу):\n\n"
                    + full_text
                )
            self._on_event(
                Event(
                    type=EventType.CHAT_FOUND,
                    message=json.dumps(self._last_found, ensure_ascii=False),
                )
            )

        context_text = self._build_chat_context(settings, context)
        if context_text:
            system += (
                "\n\nКонтекст из твоего хранилища заметок (отвечай с опорой на него, "
                "если вопрос его касается):\n\n" + context_text
            )
        self._chat_history.append({"role": "user", "content": message})
        reply = llm.chat(self._chat_history, system)
        if not reply:
            self._chat_history.pop()  # a failed turn leaves no trace
            error = "Модель не ответила — проверьте Ollama и модель"
            self._on_event(Event(type=EventType.CHAT_ERROR, message=error))
            return ""
        self._chat_history.append({"role": "assistant", "content": reply})
        self._on_event(Event(type=EventType.CHAT_REPLY, message=reply))
        return reply

    def _build_chat_context(
        self, settings: Settings, rels: list[str], budget: int | None = None
    ) -> str:
        """Read the attached processed notes into a compact context block.

        ``budget`` caps the total characters (distributed evenly over the
        notes) so long dialogs cannot blow past ``num_ctx``.
        """

        target = settings.target.resolve()
        limit = int(settings.ollama.get("limit_chars") or 6000)
        per_rel = limit
        if budget is not None and rels:
            per_rel = min(limit, max(1, budget // len(rels)))
        blocks: list[str] = []
        for rel in rels:
            candidate = target.joinpath(*rel.split("/"))
            try:
                if not candidate.is_file():
                    continue
                parsed = split_file(candidate.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if parsed is None:
                continue
            meta, body = parsed
            if not meta.get("source_hash"):
                continue
            title = str(meta.get("title") or rel)
            blocks.append(f"### {title} ({rel})\n{body[:per_rel].strip()}")
        return "\n\n---\n\n".join(blocks)

    def _build_full_sources(self, settings: Settings, candidates, budget: int) -> str:
        """Full context for the top-K candidates: relevant chunks + head fallback.

        The candidate's fragments (its best matching chunks) carry the decisive
        part of a long dialog; the head of the file is appended as fallback
        context when the budget allows. ``budget`` is split evenly.
        """

        if not candidates:
            return ""
        target = settings.target.resolve()
        per = max(1, budget // len(candidates))
        blocks: list[str] = []
        for c in candidates:
            parts: list[str] = []
            for frag in (c.fragments or [])[:5]:
                if frag.strip():
                    parts.append(frag[: max(200, per // 2)])
            remaining = max(0, per - sum(len(p) for p in parts))
            body_head = ""
            path = target.joinpath(*c.rel.split("/"))
            try:
                if path.is_file():
                    parsed = split_file(path.read_text(encoding="utf-8", errors="replace"))
                    if parsed is not None:
                        meta, body = parsed
                        if meta.get("source_hash") and body:
                            body_head = body[: max(200, remaining)]
            except OSError:
                body_head = ""
            if body_head:
                parts.append(body_head)
            if not parts:
                parts.append("(текст недоступен)")
            blocks.append(f"### {c.title} ({c.rel})\n" + "\n\n…\n\n".join(parts))
        return "\n\n---\n\n".join(blocks)

    def _on_event(self, event: Event) -> None:
        self.events.append(event)
        self._push_event(event)
        if event.type is EventType.SCAN_STARTED:
            logger.info("Scan started: %d files", event.total)
        elif event.type is EventType.FILE_STARTED:
            logger.info("Processing: %d/%d %s", event.index, event.total, event.path)
        elif event.type is EventType.FILE_ERROR:
            logger.error("File error: %s: %s", event.path, event.message)
        elif event.type is EventType.FINISHED:
            logger.info("Run finished: %s", event.message)
        elif event.type is EventType.AI_SCAN_STARTED:
            logger.info("AI scan started: %d files", event.total)
        elif event.type is EventType.AI_FILE_STARTED:
            logger.info("AI processing: %d/%d %s", event.index, event.total, event.path)
        elif event.type is EventType.AI_FILE_ERROR:
            logger.error("AI file error: %s: %s", event.path, event.message)
        elif event.type is EventType.AI_FINISHED:
            logger.info("AI run finished: %s", event.message)
        elif event.type is EventType.TOPIC_SCAN_STARTED:
            logger.info("Topic scan started: %d файл(ов)", event.total)
        elif event.type is EventType.TOPIC_MAP_STARTED:
            logger.info("Auto-grouping map started: %d чат(ов)", event.total)
        elif event.type is EventType.TOPIC_FILE_STARTED:
            logger.info("Topic collect: %d/%d %s", event.index, event.total, event.path)
        elif event.type is EventType.TOPIC_FILE_ERROR:
            logger.error("Topic collect error: %s: %s", event.path, event.message)
        elif event.type is EventType.TOPIC_UPDATED:
            logger.info("Topic updated: %s", event.message)
        elif event.type is EventType.TOPIC_RENAMED:
            logger.info("Topic renamed: %s → %s", event.path, event.message)
        elif event.type is EventType.TOPIC_DELETED:
            logger.info("Topic deleted: %s", event.message)
        elif event.type is EventType.TOPIC_FINISHED:
            logger.info("Topic build finished: %s", event.message)
        elif event.type is EventType.CHAT_REPLY:
            logger.info("Chat reply received (%d chars)", len(event.message))
        elif event.type is EventType.CHAT_FOUND:
            logger.info("Chat search found: %s", event.message[:120])
        elif event.type is EventType.CHAT_ERROR:
            logger.error("Chat error: %s", event.message)
        elif event.type is EventType.OBS_SCAN_STARTED:
            logger.info("Obsidianize scan: %s (%d папок)", event.message, event.total)
        elif event.type is EventType.OBS_FOLDER_DONE:
            logger.info("Obsidianize: %d/%d %s → %s", event.index, event.total, event.path, event.message)
        elif event.type is EventType.OBS_FINISHED:
            logger.info("Obsidianize finished: %s", event.message)
        elif event.type is EventType.OBS_ERROR:
            logger.error("Obsidianize error: %s", event.message)
        elif event.type is EventType.REVIEW_STARTED:
            logger.info("Folder review: %s (%d папок)", event.message, event.total)
        elif event.type is EventType.REVIEW_FOLDER_DONE:
            logger.info("Review %d/%d: %s → %s", event.index, event.total, event.path, event.message)
        elif event.type is EventType.REVIEW_FINISHED:
            logger.info("Folder review finished: %s", event.message)
        elif event.type is EventType.REVIEW_ERROR:
            logger.error("Folder review error: %s", event.message)

    @property
    def is_busy(self) -> bool:
        return self._busy


def launch(
    initial_source: str | None = None,
    initial_target: str | None = None,
    initial_enriched: str | None = None,
) -> None:
    """Open the pywebview window (the primary user interface)."""
    import webview

    app = UIApp()
    if initial_source:
        app.settings.source = Path(initial_source)
    if initial_target:
        app.settings.target = Path(initial_target)
    if initial_enriched:
        app.settings.enriched = Path(initial_enriched)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(h, "_obsidianizer_log", False) for h in root.handlers):
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler._obsidianizer_log = True
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
        root.addHandler(file_handler)

    handler = _UiLogHandler(lambda msg: app._log(msg))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    logger.info("GUI started: v%s", __version__)

    window = webview.create_window(
        "Obsidianizer",
        url=str(RESOURCES / "app.html"),
        js_api=app,
        width=1300,
        height=750,
        min_size=(720, 560),
        background_color="#0b0f17",
    )
    app._window = window  # pywebview 6 does not set js_api.window automatically
    try:
        window.events.closed += app.save_settings  # last state survives the close
    except Exception:  # noqa: BLE001 - best-effort; every change is already saved
        logger.warning("Не удалось подписаться на событие закрытия окна")
    webview.start(debug=False, http_server=True)