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
import sys
import threading
from dataclasses import replace
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
from .i18n import tr
from .llm import LLMClient, make_chat_client, make_enrichment_client
from .md_processor import MdProcessor
from .pipeline import Report
from .pipeline import run as run_pipeline
from .postprocess import EnrichReport
from .postprocess import enrich as postprocess_enrich
from .postprocess import split_file
from .registry import ProcessorRegistry
from .chat_detect import detect_chat_service, is_chat_export, make_chat_filter
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


def _user_data_dir() -> Path:
    """Writable directory for config, log, etc."""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Obsidianizer"
    return Path(__file__).resolve().parents[2]


def _integration_source_dir() -> Path | None:
    """Where the ``integration/`` templates ship for THIS install flavor.

    Order matters: LOCALAPPDATA (installer) first — it is the canonical
    user-writable spot; exe dir next — portable builds keep the folder
    next to the exe. A source checkout resolves through ``_user_data_dir``
    (repo root, which contains ``integration/``).
    """

    candidates = [_user_data_dir() / "integration"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "integration")
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def _lang_resolved(raw: str | None) -> str:
    """Effective UI language for ``raw`` setting ("" → OS locale)."""
    from .i18n import resolve_language

    return resolve_language(raw)


# ── Update check (GitHub Releases) ─────────────────────────────────────────

REPO_OWNER = "Nordggs"
REPO_NAME = "Obsidianizer"


def _version_tuple(v) -> tuple:
    """Numeric version tuple for tag comparison ("v0.6.3-beta1" → (0, 6, 3))."""

    parts = []
    for p in str(v).lstrip("v").replace("-", ".").split("."):
        if p.isdigit():
            parts.append(int(p))
        else:
            break
    return tuple(parts) or (0,)


def _fetch_latest_release() -> dict | None:
    """Latest published release from GitHub, or None on any failure.

    Uses the ``/releases`` LIST (newest first) and skips only drafts —
    unlike the reference ``/releases/latest`` endpoint, which never returns
    prereleases, and this project ships every release as Pre-release.
    A network/timeout/parse error degrades silently to None: an offline or
    rate-limited app must never break the UI.
    """

    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
            "?per_page=10",
            timeout=8,
        ) as r:
            data = _json.load(r)
    except Exception:  # noqa: BLE001 - offline / rate-limited / bad payload
        return None
    for rel in data or []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = (rel.get("tag_name") or "").strip()
        url = (rel.get("html_url") or "").strip()
        if tag:
            return {"tag": tag, "url": url}
    return None


LOG_FILE = _user_data_dir() / "obsidianizer.log"

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


def _make_registry(
    *,
    settings: Settings | None = None,
    selection: set[str] | None = None,
    selection_provided: bool = False,
    apply_chat: bool = True,
) -> ProcessorRegistry:
    from .exclusions import build_exclusion, build_filter

    filter_fn = None
    exclusion = None
    if settings is not None:
        filter_fn = build_filter(
            settings,
            apply_chat=apply_chat,
            selection=selection,
            selection_provided=selection_provided,
        )
        exclusion = build_exclusion(settings)
    elif selection_provided:
        filter_fn = make_chat_filter(
            chat_only=True,
            selection=selection,
            selection_provided=selection_provided,
        )
    reg = ProcessorRegistry(filter_fn=filter_fn, exclusion_filter=exclusion)
    reg.register(".md", MdProcessor)
    return reg


def _snapshot_settings(src: Settings) -> Settings:
    """Copy worker-bound settings, including exclusions and Source identity.

    TICKET-008C: ``source_namespace``/``config_path`` must survive the copy,
    otherwise AI-stage owners build a fresh namespace and reconciliation
    runs without a stable Index.
    """

    s = Settings()
    s.source = src.source
    s.target = src.target
    s.enriched = src.enriched
    s.llm_enabled = src.llm_enabled
    s.chat_only = src.chat_only
    s.prune = src.prune
    s.prune_enriched = src.prune_enriched
    s.dry_run = src.dry_run
    s.exclude_dirs = list(src.exclude_dirs)
    s.exclude_masks = list(src.exclude_masks)
    s.source_namespace = src.source_namespace
    s.config_path = src.config_path
    s.ai = replace(src.ai)
    s.ai_enrichment = replace(src.ai_enrichment)
    s.ai_chat = replace(src.ai_chat)
    return s


def _make_enrichment_llm(settings: Settings) -> LLMClient | None:
    """Build the AI Enrichment client (TICKET-010), or None when off."""

    return make_enrichment_client(settings)


def _make_chat_llm(settings: Settings) -> LLMClient | None:
    """Build the AI Chat client (TICKET-010), or None when off."""

    return make_chat_client(settings)


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
        # TICKET-007: last built SourceIndex (preview/run availability layer;
        # consumers switch to it in 008/009).
        self._last_source_index = None

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
                "data": event.data,
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
        return {
            "version": __version__,
            "source": str(self.settings.source),
            "target": str(self.settings.target),
            "enriched": str(self.settings.enriched),
            "llm_enabled": self.settings.llm_enabled,
            "chat_only": self.settings.chat_only,
            "model": self.settings.ai_enrichment.model,
            "chat_model": self.settings.ai_chat.model,
            "temperature": self.settings.ai.temperature,
            "prune": self.settings.prune,
            "prune_enriched": self.settings.prune_enriched,
            "dry_run": self.settings.dry_run,
            "obsidianize_dir": self.settings.obsidianize_dir,
            "integration_vault": self.settings.integration_vault,
            "obsidianize_vault_root": self.settings.obsidianize_vault_root,
            "obsidianize_gallery_prefix": self.settings.obsidianize_gallery_prefix,
            "obsidianize_template": self.settings.obsidianize_template,
            "language": self.settings.language,
            "lang_resolved": _lang_resolved(self.settings.language),
            # TICKET-011: GUI exclusion fields are initialised from Settings.
            "exclude_dirs": list(self.settings.exclude_dirs),
            "exclude_masks": list(self.settings.exclude_masks),
        }

    def set_language(self, lang: str) -> dict:
        """Persist the UI language ("ru" | "en" | "" = auto) and apply it live."""
        from .i18n import set_language as _set_lang

        self.settings.language = str(lang or "").strip().lower()
        _set_lang(self.settings.language)
        self._save_settings()
        return {"ok": True, "lang_resolved": _lang_resolved(self.settings.language)}

    def check_update(self) -> dict:
        """Compare the running version with the latest GitHub release.

        Errors and unknown releases degrade to ``{"available": False}`` —
        the UI dot simply stays hidden. Pre-releases count (this project
        ships every release as Pre-release); drafts are ignored by the
        fetcher.
        """

        rel = _fetch_latest_release()
        if not rel:
            return {"available": False, "current": __version__}
        if _version_tuple(rel["tag"]) > _version_tuple(__version__):
            return {
                "available": True,
                "current": __version__,
                "latest": rel["tag"],
                "url": rel["url"],
            }
        return {"available": False, "current": __version__}

    def open_external(self, url: str) -> dict:
        """Open a URL in the system browser (http/https only)."""

        import webbrowser

        if isinstance(url, str) and url.startswith(("http://", "https://")):
            try:
                webbrowser.open(url)
            except Exception as exc:  # noqa: BLE001 - never break the UI
                logger.warning("open_external failed: %s", exc)
        return {"ok": True}

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
            logger.warning(tr("log.settings_save_failed", exc=exc))

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
            self.settings.ai_enrichment.model = model
        if chat_model:
            self.settings.ai_chat.model = chat_model
        self._save_settings()

    def set_exclusions(self, dirs, masks) -> dict:
        """Persist the GUI exclusion fields (TICKET-006 wiring, TICKET-011).

        ``dirs``/``masks`` accept a list or a CSV/whitespace string. Excluded
        means "not discovered, not processed, not deleted" — this only wires
        the existing exclusion mechanism into the GUI.
        """

        from .exclusions import parse_list

        self.settings.exclude_dirs = parse_list(dirs)
        self.settings.exclude_masks = parse_list(masks)
        self._save_settings()
        return {
            "ok": True,
            "exclude_dirs": list(self.settings.exclude_dirs),
            "exclude_masks": list(self.settings.exclude_masks),
        }

    def set_chat_model(self, model: str) -> dict:
        """Persist the AI Chat model (edited in the chat window, TICKET-011)."""

        value = str(model or "").strip()
        if value:
            self.settings.ai_chat.model = value
        self._save_settings()
        return {"ok": True, "chat_model": self.settings.ai_chat.model}

    def set_flags(self, dry_run: bool, prune: bool, prune_enriched: bool) -> None:
        self.settings.dry_run = bool(dry_run)
        self.settings.prune = bool(prune)
        self.settings.prune_enriched = bool(prune_enriched)
        self._save_settings()

    def set_chat_only(self, enabled: bool) -> None:
        self.settings.chat_only = bool(enabled)
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
            return {"ok": False, "error": tr("err.unknown_template", template=template)}
        self.settings.obsidianize_template = str(template).strip()
        self._save_settings()
        return {"ok": True}

    def set_int_vault(self, path: str) -> dict:
        """Remember the Integration vault folder (persisted in settings)."""
        self.settings.integration_vault = str(path).strip()
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
            _category_of,
            _compute_content_hashes,
            card_diff,
            card_path_for,
            card_status,
            discover_dir_rels,
            format_changes,
            notes_file_path,
            scan_tree,
        )
        from .exclusions import (
            DEFAULT_EXCLUDE_DIRS,
            build_tab1_exclusion,
            exclusion_kind,
        )

        path = str(path or "").strip() or str(self.settings.obsidianize_dir).strip()
        if not path:
            return {"ok": False, "error": tr("err.no_folder")}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": tr("err.folder_missing", path=root)}
        try:
            cfg = ObsidianizeConfig(template=self.settings.obsidianize_template)
            tree = scan_tree(root, cfg)
            logger.info("Obs scan: root=%s folders=%d", root, len(tree))
            folders = []
            user_dirs = list(getattr(self.settings, "exclude_dirs", []) or [])
            excl = build_tab1_exclusion(self.settings)
            excluded_lookup: dict[str, str] = {}
            try:
                for item in discover_dir_rels(root, excl, user_dirs):
                    rel_d = str(item.get("rel") or "")
                    name_d = rel_d.rsplit("/", 1)[-1] if rel_d else ""
                    excluded_lookup[rel_d] = str(
                        item.get("kind") or exclusion_kind(name_d, user_dirs)
                    )
            except Exception:  # noqa: BLE001 — discovery never breaks scan
                excluded_lookup = {}
            strict_rels = set(tree.keys())

            def _kind_for(rel: str) -> str | None:
                if rel in excluded_lookup:
                    return excluded_lookup[rel]
                name = (rel.rsplit("/", 1)[-1] if rel else "")
                if not rel:
                    return None
                if excl.is_excluded_dir(rel):
                    return (
                        "system"
                        if name in DEFAULT_EXCLUDE_DIRS
                        else "user"
                    )
                if name.startswith("."):
                    return "hidden-only"
                return None

            for rel, folder in tree.items():
                kind = _kind_for(rel)
                excluded_flag = kind is not None
                counts = {cat: 0 for cat in ("drafting", "tables", "docs", "images", "other")}
                for f in folder.files:
                    counts[_category_of(f.ext, cfg)] += 1

                card_p = card_path_for(folder)
                notes_prev: str | None = None
                notes_p = notes_file_path(folder)
                if notes_p.exists():
                    try:
                        notes_prev = notes_p.read_text(encoding="utf-8")
                    except OSError:
                        notes_prev = None

                # Content hashes once per folder: reused by the status check
                # and the diff so every file is read at most one time.
                content_hashes = _compute_content_hashes(folder)
                status = card_status(
                    card_p,
                    folder,
                    template=cfg.template,
                    notes_prev=notes_prev,
                    content_hashes=content_hashes,
                )

                changes: list[str] = []
                if status == "stale" and card_p.is_file():
                    try:
                        diff = card_diff(
                            card_p.read_text(encoding="utf-8"),
                            folder,
                            notes_prev,
                            content_hashes,
                        )
                    except OSError:
                        diff = None
                    changes = format_changes(diff) if diff else [tr("scan.card_stale")]
                    if not changes:
                        changes = [tr("scan.content_changed")]
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
                        "excluded": excluded_flag,
                        "kind": kind,
                        "checked_default": not excluded_flag,
                    }
                )
            # excluded_dirs: ALL excluded rels (union rule), two groups:
            # (a) outside the strict tree (own dicts, no counters/card_status);
            # (b) inside the strict tree (merge key only — counters live in
            # folders[]; the GUI merges by rel with "excluded wins" and
            # dedups counters by rel; review_scan keeps reading folders[]).
            excluded_dirs: list[dict] = []
            for rel_d, kind_d in excluded_lookup.items():
                if rel_d in strict_rels:
                    excluded_dirs.append(
                        {
                            "rel": rel_d,
                            "kind": kind_d,
                            "checked_default": False,
                            "in_tree": True,
                        }
                    )
                else:
                    excluded_dirs.append(
                        {
                            "rel": rel_d,
                            "kind": kind_d,
                            "checked_default": False,
                            "card": "excluded",
                            "in_tree": False,
                        }
                    )
            changed_n = sum(1 for f in folders if f["card"] in ("stale", "missing"))
            return {
                "ok": True,
                "root": str(root),
                "folders": folders,
                "excluded_dirs": excluded_dirs,
                "rules": {
                    "system": list(DEFAULT_EXCLUDE_DIRS),
                    "user_dirs": list(getattr(self.settings, "exclude_dirs", []) or []),
                    "masks": list(getattr(self.settings, "exclude_masks", []) or []),
                },
                "summary": tr(
                    "scan.summary",
                    checked=len(folders),
                    unchanged=len(folders) - changed_n,
                    changed=changed_n,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def review_scan(self, path: str = "") -> dict:
        """Filtered folder list for AI review (tab 3) — TICKET-006.

        Same response form as ``obs_scan`` (``{ok, root, folders, summary}``,
        ``folders=[{rel, files, card, ...}]`` — the ``renderReviewFolders``
        contract), but excluded rels are dropped. Frozen ``obs_scan``
        (tab 1) is untouched; this method reuses it and filters the
        response so card statuses can never diverge.
        """
        from .exclusions import build_exclusion

        res = self.obs_scan(path)
        if not res.get("ok"):
            return res
        excl = build_exclusion(self.settings)
        folders = res.get("folders") or []
        kept = [f for f in folders if not excl.excluded(str(f.get("rel") or ""))]
        res["folders"] = kept
        # Tab-1 selection fields must not leak into the tab-3 contract.
        res.pop("excluded_dirs", None)
        res.pop("rules", None)
        return res

    def obs_obsidianize(self, opts: dict | None = None) -> dict:
        """Create/refresh the Markdown cards in a background thread.

        ``opts``: ``{"path": str, "recursive": bool, "gallery": bool,
        "vault_root": str, "force": bool, "adopt": bool, "rebuild": bool,
        "selection": [rel...], "selection_provided": bool}``.
        Progress arrives via the ``OBS_*`` events; the source folder is
        never modified. ``rebuild`` regenerates own cards even when up to
        date (structure migration); foreign notes still conflict.
        TICKET-013: ``selection`` filters only card generation (plus the
        ancestor container chain); absent ``selection_provided`` = legacy.
        """
        if self._busy:
            return {"ok": False, "error": tr("err.busy")}
        opts = opts or {}
        path = str(opts.get("path") or "").strip() or str(self.settings.obsidianize_dir).strip()
        if not path:
            return {"ok": False, "error": tr("err.no_folder")}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": tr("err.folder_missing", path=root)}
        from .obsidianize import ObsidianizeConfig, _norm_sel_rel

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
            rebuild=bool(opts.get("rebuild")),
            img_gallery=bool(opts.get("gallery", True)),
            vault_root=vault_root,
            gallery_prefix=gallery_prefix,
            template=str(opts.get("template") or self.settings.obsidianize_template),
        )
        self.settings.obsidianize_dir = str(root)
        self._save_settings()
        self._busy = True
        self._cancel = False
        selection_provided = bool(opts.get("selection_provided", False))
        raw_sel = opts.get("selection", None)
        selection: set[str] | None = None
        if selection_provided:
            selection = set()
            if raw_sel:
                for r in raw_sel:
                    n = _norm_sel_rel(r)
                    if n is not None:
                        selection.add(n)
        logger.info(
            "Obsidianize: %s recursive=%s gallery=%s selection=%s", root, bool(opts.get("recursive", True)), cfg.img_gallery,
            ("off" if not selection_provided else sorted(selection or [])),
        )
        threading.Thread(
            target=self._run_obs_worker,
            args=(root, cfg, bool(opts.get("recursive", True)), selection, selection_provided),
            daemon=True,
            name="obsidianizer-obs",
        ).start()
        return {"ok": True}

    def _run_obs_worker(
        self,
        root: Path,
        cfg,
        recursive: bool,
        selection: set[str] | None = None,
        selection_provided: bool = False,
    ) -> None:
        from .obsidianize import scan_tree, update_cards

        try:
            tree = scan_tree(root, cfg)
            total = len(tree) if recursive else 1
            if selection_provided:
                if not (selection or set()):
                    total = 0
                else:
                    expanded = set(selection or set())
                    for rel in list(selection or set()):
                        if rel == "":
                            continue
                        parts = rel.split("/")
                        for i in range(1, len(parts)):
                            expanded.add("/".join(parts[:i]))
                        expanded.add("")
                    total = len(expanded)
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
                root,
                cfg,
                on_progress=on_progress,
                recursive=recursive,
                selection=selection,
                selection_provided=selection_provided,
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
                            "failed": summary.failed,
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
            return {"ok": False, "error": tr("err.no_window")}
        target = Path(path or self.settings.obsidianize_dir).expanduser()
        if not target.is_dir():
            return {"ok": False, "error": tr("err.folder_missing", path=target)}
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
            opts.get("vault") or self.settings.integration_vault or ""
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
        vault = str(opts.get("vault") or self.settings.integration_vault or "").strip()
        if not vault:
            picked = self.choose_folder()
            if not picked:
                return {"ok": False, "error": tr("err.no_folder_picked")}
            vault = picked

        res = install_obsidian_integration(
            Path(vault).expanduser(), repair=bool(opts.get("repair"))
        )
        if res.get("ok"):
            self.settings.integration_vault = vault
            self._save_settings()
            logger.info("Obsidian integration installed: %s", res.get("target"))
        return res

    def extract_templates(self, opts: dict | None = None) -> dict:
        """Copy the integration templates into a user-chosen folder.

        ``opts``: ``{"target": str, "force": bool}``. Existing files in the
        target are never overwritten silently: a name collision aborts with
        ``conflicts`` and the caller must repeat with ``force=True`` (the
        same Repair pattern as ``obs_integration_install``). The decision is
        atomic — either nothing is copied, or everything is.
        """
        import shutil

        opts = opts or {}
        target = str(opts.get("target") or "").strip()
        if not target:
            return {"ok": False, "error": tr("err.no_folder_picked")}
        src = _integration_source_dir()
        if src is None:
            return {
                "ok": False,
                "error": tr("err.integration_missing"),
            }
        dst = Path(target).expanduser()
        files = sorted(f for f in src.iterdir() if f.is_file())
        if not files:
            return {"ok": False, "error": tr("err.no_template_files", src=src)}

        conflicts = [f.name for f in files if (dst / f.name).exists()]
        if conflicts and not opts.get("force"):
            return {
                "ok": False,
                "conflicts": conflicts,
                "error": tr("err.files_exist"),
            }

        try:
            dst.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, dst / f.name)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        logger.info(
            "Integration templates extracted to %s (%d files, force=%s)",
            dst, len(files), bool(opts.get("force")),
        )
        return {
            "ok": True,
            "target": str(dst),
            "copied": [f.name for f in files],
            "overwritten": conflicts if opts.get("force") else [],
        }

    # ── AI folder review (tab 3) ──────────────────────────────────────────

    def review_run(self, opts: dict | None = None) -> dict:
        """Generate ``<folder>_обзор.md`` for the selected folders in a thread.

        ``opts``: ``{"path": str, "rels": [str], "include_text": bool}``.
        Uses the chat model (``ollama.chat_model``, falls back to the main
        one). Progress arrives via the ``REVIEW_*`` events. Source files are
        never modified — only the review files are written.
        """
        if self._busy:
            return {"ok": False, "error": tr("err.busy")}
        opts = opts or {}
        path = str(opts.get("path") or "").strip() or str(self.settings.obsidianize_dir).strip()
        if not path:
            return {"ok": False, "error": tr("err.no_folder")}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": tr("err.folder_missing", path=root)}
        rels = [str(r) for r in (opts.get("rels") or []) if r is not None]
        if not rels:
            return {"ok": False, "error": tr("err.pick_folder")}
        llm = _make_enrichment_llm(self.settings)
        if llm is None:
            return {"ok": False, "error": tr("err.llm_disabled")}
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
        from .exclusions import ExclusionFilter
        from .obsidianize import ObsidianizeConfig, scan_tree
        from .review import build_review_markdown, build_request, collect_payload, save_review

        try:
            prompt = str(
                self.settings.ai_enrichment.folders_prompt
                or DEFAULT_FOLDERS_ANALYZE_PROMPT
            )
            # TICKET-006: user exclude_dirs union the frozen tab-1 defaults
            # (never replace them); masks act via the payload predicate.
            defaults = ObsidianizeConfig().exclude
            union_dirs = list(dict.fromkeys(
                list(defaults) + list(self.settings.exclude_dirs)
            ))
            cfg = ObsidianizeConfig(exclude=union_dirs)
            excl = ExclusionFilter(
                list(self.settings.exclude_dirs), list(self.settings.exclude_masks)
            )
            kept, dropped = excl.split([str(r) for r in rels])
            tree = scan_tree(root, cfg)
            model = str(getattr(llm, "chat_model", "") or getattr(llm, "model", ""))
            self._on_event(
                Event(EventType.REVIEW_STARTED, total=len(kept), message=str(root))
            )
            ok_files: list[str] = []
            err_count = 0
            excluded: list[str] = list(dropped)
            if dropped:
                logger.info(
                    "Folder review: excluded rels skipped: %s", ", ".join(dropped)
                )
                for i, rel in enumerate(dropped, start=1):
                    self._on_event(
                        Event(
                            EventType.REVIEW_FOLDER_DONE,
                            path=rel,
                            index=i,
                            total=len(rels),
                            message="excluded",
                        )
                    )
            for i, rel in enumerate(kept, start=1):
                if excl.excluded(rel):
                    excluded.append(rel)
                    self._on_event(
                        Event(
                            EventType.REVIEW_FOLDER_DONE,
                            path=rel,
                            index=i,
                            total=len(rels),
                            message="excluded",
                        )
                    )
                    continue
                folder = tree.get(rel)
                if folder is None:
                    # Excluded dirs are pruned from scan_tree via the union
                    # cfg; an unknown rel degrades to a skip, never a crash.
                    if excl.is_excluded_dir(rel):
                        excluded.append(rel)
                        self._on_event(
                            Event(
                                EventType.REVIEW_FOLDER_DONE,
                                path=rel,
                                index=i,
                                total=len(rels),
                                message="excluded",
                            )
                        )
                        continue
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
                    payload = collect_payload(
                        folder, include_text=include_text, cfg=cfg,
                        exclusion_filter=excl,
                    )
                    request = build_request([payload], include_text=include_text)
                    reply = llm.chat([{"role": "user", "content": request}], prompt)
                    if not reply or not reply.strip():
                        raise RuntimeError(tr("err.model_no_reply"))
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
                            "excluded": excluded,
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
        client = LLMClient(
            endpoint=self.settings.ai.endpoint,
            model=self.settings.ai_enrichment.model,
            timeout=10.0,
        )
        models = client.list_models()
        if models is None:
            return {"ok": False, "error": tr("err.ollama_unavailable")}
        return {"ok": True, "models": models}

    def _prompt_target(self, kind: str):
        """(group, attribute, default) for a prompt kind, or ``None``.

        TICKET-010: enrichment prompts (incl. ``folders_prompt``) live in
        ``ai_enrichment``; ``chat_prompt`` lives in ``ai_chat``.
        """

        enr, chat = self.settings.ai_enrichment, self.settings.ai_chat
        return {
            "prompt": (enr, "prompt", DEFAULT_PROMPT),
            "ai_prompt": (enr, "ai_prompt", DEFAULT_AI_PROMPT),
            "topic_prompt": (enr, "topic_prompt", DEFAULT_TOPIC_PROMPT),
            "map_prompt": (enr, "map_prompt", DEFAULT_TOPIC_MAP_PROMPT),
            "folders_prompt": (enr, "folders_prompt", DEFAULT_FOLDERS_ANALYZE_PROMPT),
            "chat_prompt": (chat, "chat_prompt", DEFAULT_CHAT_PROMPT),
        }.get(kind)

    def get_prompts(self) -> dict:
        """Return the prompt templates plus their built-in defaults.

        Pure local read — no Ollama call, no content is ever sent.
        """
        enr, chat = self.settings.ai_enrichment, self.settings.ai_chat
        return {
            "prompt": enr.prompt,
            "ai_prompt": enr.ai_prompt,
            "topic_prompt": enr.topic_prompt,
            "map_prompt": enr.map_prompt,
            "folders_prompt": enr.folders_prompt,
            "chat_prompt": chat.chat_prompt,
            "default_prompt": DEFAULT_PROMPT,
            "default_ai_prompt": DEFAULT_AI_PROMPT,
            "default_topic_prompt": DEFAULT_TOPIC_PROMPT,
            "default_map_prompt": DEFAULT_TOPIC_MAP_PROMPT,
            "default_folders_prompt": DEFAULT_FOLDERS_ANALYZE_PROMPT,
            "default_chat_prompt": DEFAULT_CHAT_PROMPT,
        }

    def set_prompt(self, kind: str, value: str) -> dict:
        target = self._prompt_target(kind)
        if target is None:
            return {"ok": False, "error": tr("err.unknown_prompt")}
        group, attr, _default = target
        setattr(group, attr, value)
        self._save_settings()
        return {"ok": True}

    def reset_prompt(self, kind: str) -> dict:
        """Restore a prompt to its built-in default (never overwritten)."""

        target = self._prompt_target(kind)
        if target is None:
            return {"ok": False, "error": tr("err.unknown_prompt")}
        group, attr, default = target
        setattr(group, attr, default)
        self._save_settings()
        return {"ok": True, "value": default}

    def scan(self) -> dict:
        try:
            # Preview applies only exclusions (no chat filter) so non-chats
            # survive — TICKET-004 protection.
            reg = _make_registry(settings=self.settings, apply_chat=False)
            source_root = self.settings.source.resolve()
            files = reg.scan(source_root)
            # TICKET-007: build the SourceIndex beside the candidate list
            # (availability layer; the preview response shape is unchanged).
            from .config import ensure_source_namespace
            from .exclusions import build_exclusion
            from .source_index import build_source_index

            namespace, _ = ensure_source_namespace(
                self.settings,
                persist=bool(
                    self.settings.config_path
                    and Path(str(self.settings.config_path)).exists()
                ),
            )
            self._last_source_index = build_source_index(
                source_root, namespace, build_exclusion(self.settings)
            )
            results = []
            for c in files:
                text = ""
                try:
                    text = c.abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
                results.append({
                    "path": c.rel_path,
                    "is_chat": is_chat_export(text) if text else False,
                    "service": detect_chat_service(text) if text else None,
                })
            agg = self._detection_aggregate()
            return {
                "ok": True,
                "total": len(results),
                "files": results,
                # TICKET-011 (additive): full Detection statistics over the
                # SourceIndex — distinct from ``files`` (the processable
                # candidates of the current scenario). ``detected_total`` is an
                # additive alias of ``aggregate.total``.
                "aggregate": agg,
                "detected_total": agg["total"],
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _detection_aggregate(self) -> dict:
        """Detection statistics over the last SourceIndex (TICKET-011)."""

        from .source_index import Kind

        agg = {
            "chats": 0, "documents": 0, "images": 0, "cad": 0,
            "tables": 0, "folders": 0, "total": 0,
        }
        index = self._last_source_index
        if index is None:
            return agg
        type_key = {
            "chat_export": "chats",
            "document": "documents",
            "image": "images",
            "cad": "cad",
            "spreadsheet": "tables",
        }
        for item in index.items.values():
            agg["total"] += 1
            if item.kind == Kind.FOLDER:
                agg["folders"] += 1
                continue
            key = type_key.get(item.file_type)
            if key:
                agg[key] += 1
        return agg

    def list_chats(self) -> dict:
        """List the processed chats available for topic merging.

        Scans ``settings.target`` and returns compact cards: relative path,
        title, service, date, message count and summary. Foreign notes without
        the ``source_hash`` ownership marker are excluded.
        """
        try:
            from .exclusions import build_exclusion

            cards = collect_chat_cards(
                self.settings.target.resolve(), build_exclusion(self.settings)
            )
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
                return {"ok": False, "error": tr("err.topic_missing")}
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
            from .exclusions import build_exclusion

            cards = find_chats_without_topic(
                self.settings.target.resolve(),
                self.settings.enriched.resolve(),
                build_exclusion(self.settings),
            )
            return {"ok": True, "files": cards}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def start_run(self, opts: dict | None = None) -> dict:
        if self._busy:
            return {"ok": False, "error": tr("err.busy")}
        opts = opts or {}
        self._busy = True
        self._cancel = False
        s = _snapshot_settings(self.settings)
        ai = bool(opts.get("ai"))
        prune_ai = bool(opts.get("prune_ai"))
        selection = set(opts.get("selection") or [])
        selection_provided = bool(opts.get("selection_provided"))
        logger.info(
            "Run started: source=%s target=%s enriched=%s llm=%s dry_run=%s "
            "prune=%s ai=%s prune_ai=%s chat_only=%s selection=%s",
            s.source,
            s.target,
            s.enriched,
            s.llm_enabled,
            bool(opts.get("dry_run")),
            bool(opts.get("prune")),
            ai,
            prune_ai,
            s.chat_only,
            selection_provided,
        )
        self._save_settings()
        threading.Thread(
            target=self._run_worker,
            args=(s, bool(opts.get("dry_run")), bool(opts.get("prune")), ai, prune_ai, selection, selection_provided),
            daemon=True,
            name="obsidianizer-run",
        ).start()
        return {"ok": True}

    def start_ai(self, opts: dict | None = None) -> dict:
        """Start the AI post-processing stage alone (skip re-import)."""
        if self._busy:
            return {"ok": False, "error": tr("err.busy")}
        opts = opts or {}
        s = _snapshot_settings(self.settings)
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
            return {"ok": False, "error": tr("err.busy")}
        opts = opts or {}
        rel_files = opts.get("files") or []
        if not isinstance(rel_files, list) or not rel_files:
            return {"ok": False, "error": tr("err.no_files")}
        rel_files = [str(f).replace("\\", "/") for f in rel_files]
        s = _snapshot_settings(self.settings)
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
            return {"ok": False, "error": tr("err.busy")}
        s = _snapshot_settings(self.settings)
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
            return {"ok": False, "error": tr("err.busy")}
        opts = opts or {}
        topic_id = str(opts.get("topic_id") or "")
        rel_files = opts.get("files") or []
        if not topic_id:
            return {"ok": False, "error": tr("err.no_topic")}
        if not isinstance(rel_files, list) or not rel_files:
            return {"ok": False, "error": tr("err.no_files")}
        rel_files = [str(f).replace("\\", "/") for f in rel_files]
        s = _snapshot_settings(self.settings)
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
        self._log(tr("status.stopping"))
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
        ``tab`` selects the initial section: obsidianize | proc | integration.
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

        safe = tab if tab in ("obsidianize", "proc", "integration") else "obsidianize"
        window = webview.create_window(
            "Obsidianizer — Справка",
            url=str(RESOURCES / "help.html"),
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
        if safe != "obsidianize":
            import threading
            def _set_tab() -> None:
                import time
                time.sleep(0.4)
                self._push(
                    f"window.showHelpTab && window.showHelpTab({json.dumps(safe)})",
                    window,
                )
            threading.Thread(target=_set_tab, daemon=True).start()
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
            return {"ok": False, "error": tr("err.no_chats")}
        payload = json.dumps(norm, ensure_ascii=False)
        self._push(f"window.openTopicFromFound({payload})")
        return {"ok": True, "count": len(norm)}

    def _get_index(self, settings: Settings) -> ChatIndex:
        """Build (or reuse) the search index for the current settings paths."""

        from .exclusions import build_exclusion

        target = settings.target.resolve()
        index_root = settings.enriched.resolve() / ".obsidianizer"
        llm = _make_chat_llm(settings)
        chat = settings.ai_chat
        exclusion = build_exclusion(settings)
        if (
            self._index is None
            or self._index.target != target
            or self._index.index_root != index_root
        ):
            self._index = ChatIndex(
                target,
                index_root,
                llm=llm,
                embed_model=str(chat.embed_model or "nomic-embed-text:latest"),
                top_k=int(chat.search_top_k or 30),
                exclusion_filter=exclusion,
            )
        else:
            self._index.llm = llm
            self._index.exclusion_filter = exclusion
            self._index.embed_model = str(
                chat.embed_model or self._index.embed_model
            )
            self._index.top_k = int(chat.search_top_k or self._index.top_k)
        return self._index

    def _candidate_payload(self, cand) -> dict:
        return {
            "rel": cand.rel,
            "source_id": getattr(cand, "source_id", ""),
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
            return {"ok": False, "error": tr("err.assistant_busy")}
        opts = opts or {}
        message = str(opts.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": tr("err.empty_message")}
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
        s.ai = replace(self.settings.ai)
        s.ai_enrichment = replace(self.settings.ai_enrichment)
        s.ai_chat = replace(self.settings.ai_chat)
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
        selection: set[str] | None = None,
        selection_provided: bool = False,
    ) -> None:
        try:
            self.run_now(
                settings,
                dry_run=dry_run,
                prune=prune,
                selection=selection,
                selection_provided=selection_provided,
            )
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
        self,
        settings: Settings,
        *,
        dry_run: bool = False,
        prune: bool = False,
        selection: set[str] | None = None,
        selection_provided: bool = False,
    ) -> Report:
        """Synchronous import run used by the worker thread and by tests.

        Import is intentionally LLM-free: AI enrichment happens in the second
        stage (``run_ai_now`` / ``postprocess.enrich``).
        """
        # Prune-guard: disable prune when filter/selection is active.
        filter_active = selection_provided or settings.chat_only
        effective_prune = prune and not filter_active
        if prune and filter_active:
            logger.warning(
                "Prune disabled: chat filter / manual selection is active"
            )
        return run_pipeline(
            _make_registry(
                settings=settings,
                selection=selection,
                selection_provided=selection_provided,
            ),
            settings,
            dry_run=dry_run,
            prune=effective_prune,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
        )

    def run_ai_now(self, settings: Settings, *, prune: bool = False) -> EnrichReport:
        """Synchronous AI post-processing run used by workers and tests.

        Reads ``settings.target`` (processed) and writes ``settings.enriched``.
        Builds the live SourceIndex from settings (TICKET-008C owner) for
        reconciliation and id-prune.
        """
        from .config import ensure_source_namespace
        from .exclusions import build_exclusion
        from .source_index import build_source_index

        llm = _make_enrichment_llm(settings)
        if llm is None:
            report = EnrichReport()
            report.critical_error = tr("err.llm_disabled")
            self._on_event(Event(type=EventType.AI_FINISHED, message=report.critical_error))
            return report
        namespace, _ = ensure_source_namespace(
            settings,
            persist=bool(
                settings.config_path and Path(str(settings.config_path)).exists()
            ),
        )
        return postprocess_enrich(
            settings.target.resolve(),
            settings.enriched.resolve(),
            llm,
            prune=prune,
            on_event=self._on_event,
            cancel_check=lambda: self._cancel,
            exclusion_filter=build_exclusion(settings),
            source_index=build_source_index(
                settings.source.resolve(), namespace, build_exclusion(settings)
            ),
        )

    def run_topic_now(self, settings: Settings, rel_files: list[str]) -> TopicReport:
        """Synchronous topic build used by the worker thread and by tests.

        Merges the selected chats from ``settings.target`` into one topic note
        under ``settings.enriched/topics``.
        """
        from .exclusions import build_exclusion

        llm = _make_enrichment_llm(settings)
        if llm is None:
            report = TopicReport()
            report.critical_error = tr("err.llm_disabled")
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
            exclusion_filter=build_exclusion(settings),
        )

    def run_group_all_now(self, settings: Settings) -> GroupReport:
        """Synchronous auto-grouping run used by the worker thread and by tests.

        Clusters the whole ``settings.target`` collection into topics under
        ``settings.enriched/topics`` (no manual selection).
        """
        from .exclusions import build_exclusion

        llm = _make_enrichment_llm(settings)
        if llm is None:
            report = GroupReport()
            report.critical_error = tr("err.llm_disabled")
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
            exclusion_filter=build_exclusion(settings),
        )

    def run_update_topic_now(self, settings: Settings, topic_id: str, rel_files: list[str]) -> TopicReport:
        """Synchronous topic update used by the worker thread and by tests.

        Regenerates an existing topic (``settings.enriched/topics``) over a new
        chat selection from ``settings.target``.
        """
        from .exclusions import build_exclusion

        llm = _make_enrichment_llm(settings)
        if llm is None:
            report = TopicReport()
            report.critical_error = tr("err.llm_disabled")
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
            exclusion_filter=build_exclusion(settings),
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
        llm = _make_chat_llm(settings)
        if llm is None:
            error = tr("err.llm_disabled")
            self._on_event(Event(type=EventType.CHAT_ERROR, message=error))
            return ""
        system = str(settings.ai_chat.chat_prompt or DEFAULT_CHAT_PROMPT)

        found: list[SearchCandidate] = []
        try:
            index = self._get_index(settings)
            index.refresh()
            found = index.search(message)
        except Exception as exc:  # noqa: BLE001 - search must never kill the chat
            logger.warning("Поиск по коллекции недоступен: %s", exc)
            found = []

        if found:
            full_k = int(settings.ai_chat.search_full_k or 3)
            for c in found[:full_k]:
                c.full = True
        self._last_found = [self._candidate_payload(c) for c in found]

        if found:
            frag_chars = int(settings.ai_chat.search_frag_chars or 600)
            system += "\n\n" + self._build_search_block(found, frag_chars=frag_chars)
            full_text = self._build_full_sources(
                settings,
                found[:full_k],
                int(settings.ai_chat.search_full_chars or 9000),
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
            error = tr("err.model_no_reply_check")
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
        limit = int(
            settings.ai_chat.limit_chars or settings.ai.limit_chars or 6000
        )
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
                parts.append(tr("status.text_unavailable"))
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

    from .i18n import set_language as _i18n_set
    _i18n_set(app.settings.language)  # UI language ≠ data language

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(h, "_obsidianizer_log", False) for h in root.handlers):
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
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