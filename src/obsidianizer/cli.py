"""Command-line entry point.

Argument parsing lives only here; the pipeline knows nothing about the CLI.
Precedence: CLI arguments > config.yml > defaults.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Settings, ensure_source_namespace
from .guard import GuardError, check as guard_check
from .llm import LLMClient, make_enrichment_client
from .md_processor import MdProcessor
from .obsidianize import ObsidianizeConfig, update_cards
from .registry import ProcessorRegistry
from .pipeline import run as run_pipeline
from .postprocess import enrich as postprocess_enrich
from .chat_detect import make_chat_filter
from .exclusions import build_exclusion, build_filter, parse_list
from .source_index import build_source_index


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="obsidianizer",
        description=(
            "Подготавливает сырые файлы (Markdown и др.) к использованию в Obsidian: "
            "извлекает метаданные, добавляет резюме/теги (локальная LLM, опционально) "
            "и пишет готовую папку, не трогая источник."
        ),
    )
    p.add_argument("--source", help="папка-источник (переопределяет config.yml)")
    p.add_argument("--target", help="папка-результат обработки (переопределяет config.yml)")
    p.add_argument("--enriched", help="папка AI-результата (переопределяет config.yml)")
    p.add_argument("--model", help="модель Ollama для резюме/тегов (AI Enrichment)")
    p.add_argument("--chat-model", help="модель Ollama для AI-чата (AI Chat)")
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="не вызывать LLM: только метаданные, без резюме/тегов",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="предпросмотр: считать и показать план, ничего не писать",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help=(
            "удалить только ранее созданные Obsidianizer файлы, которых больше нет "
            "в текущем результате (по манифесту владения); чужие файлы не трогаются"
        ),
    )
    p.add_argument(
        "--prune-enriched",
        action="store_true",
        help=(
            "на AI-этапе удалять сирот из папки AI-результата: enriched-заметки, "
            "которых больше нет в результате обработки, и media, на которые они "
            "единственные ссылались (только файлы с меткой владения ai_hash)"
        ),
    )
    p.add_argument(
        "--backfill-source-ids",
        action="store_true",
        help=(
            "дописать source_id в старые owned-заметки без ссылки "
            "(аддитивно, идемпотентно; только дописывание, без пересоздания)"
        ),
    )
    p.add_argument(
        "--chat-only",
        action="store_true",
        default=None,
        help="обрабатывать только файлы, распознанные как экспортированные AI-чаты (default: True)",
    )
    p.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        help="исключить каталог по имени на любой глубине (повторяемо, CSV); поверх config.yml",
    )
    p.add_argument(
        "--exclude-mask",
        action="append",
        default=None,
        help="исключить файлы по маске fnmatch (повторяемо, CSV); поверх config.yml",
    )
    p.add_argument("--version", action="version", version=f"obsidianizer {__version__}")
    p.add_argument("--verbose", action="store_true", help="подробный лог")

    sub = p.add_subparsers(dest="command", metavar="{ui,ai,folders}")
    ui = sub.add_parser("ui", help="запустить графический интерфейс")
    ui.add_argument("--source", help="папка-источник (стартовое значение в окне)")
    ui.add_argument("--target", help="папка-результат (стартовое значение в окне)")
    ui.add_argument("--enriched", help="папка AI-результата (стартовое значение в окне)")
    ai = sub.add_parser(
        "ai",
        help="AI-постобработка: processed → enriched, только LLM, без повторного импорта",
    )
    ai.add_argument("--target", help="папка результата обработки (переопределяет config.yml)")
    ai.add_argument("--enriched", help="папка AI-результата (переопределяет config.yml)")
    ai.add_argument("--model", help="модель Ollama для резюме/тегов (AI Enrichment)")
    ai.add_argument("--chat-model", help="модель Ollama для AI-чата (AI Chat)")
    ai.add_argument(
        "--prune-enriched",
        action="store_true",
        help="удалять сирот из папки AI-результата (см. глобальную опцию)",
    )
    folders = sub.add_parser(
        "folders",
        help=(
            "Folder Obsidianizer: создать/обновить Markdown-карточки для папки "
            "(карточка на каждую подпапку; исходные файлы НЕ трогаются)"
        ),
    )
    folders.add_argument("--path", required=True, help="папка для сканирования")
    folders.add_argument(
        "--dry-run",
        action="store_true",
        help="предпросмотр: показать план, ничего не писать",
    )
    folders.add_argument(
        "--force",
        action="store_true",
        help="перезаписывать чужую заметку с именем папки (без маркера obsidianizer)",
    )
    folders.add_argument(
        "--adopt",
        action="store_true",
        help="переименовать чужую заметку в <имя>_заметки.md (содержимое 1:1), "
        "затем создать карточку",
    )
    folders.add_argument(
        "--rebuild",
        action="store_true",
        help="принудительно пересоздать СВОИ карточки по актуальным правилам "
        "текущей версии (миграция старой структуры); чужие заметки без "
        "маркера не трогаются (остаются конфликтом)",
    )
    folders.add_argument(
        "--no-recursive",
        action="store_true",
        help="создать карточку только для самой папки, без подпапок",
    )
    folders.add_argument(
        "--no-gallery",
        action="store_true",
        help="не генерировать блок img-gallery (изображения всё равно в таблице)",
    )
    folders.add_argument(
        "--vault-root",
        help="корень Obsidian vault — нужен для img-gallery (путь в блоке)",
    )
    folders.add_argument(
        "--gallery-prefix",
        default="",
        help="префикс пути vault для img-gallery, когда рабочая папка вне vault "
        "(например: PROJECT/OBSIDIAN/Objects)",
    )
    folders.add_argument(
        "--rel",
        default="",
        help="vault-relative путь сканируемой папки (для ссылки «Вверх» при "
        "локальном обновлении из Obsidian)",
    )
    folders.add_argument(
        "--template",
        choices=("github", "classic"),
        default="github",
        help="шаблон карточки: github (Project Dashboard, по умолчанию) | classic",
    )
    folders.add_argument(
        "--include-md",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="включить .md файлы пользователей в таблицу файлов (по умолчанию: включено)",
    )
    folders.add_argument(
        "--select",
        default=None,
        help="обработать только выбранные каталоги (rel через запятую, '' = корень; "
        "иначе — прежнее поведение; невыбранные/исключённые пропускаются, "
        "родители выбранных создаются как навигационные контейнеры)",
    )
    return p


def _log_format() -> str:
    return "%(asctime)s [%(levelname)s] %(message)s"


def _run_folders(args: argparse.Namespace) -> int:
    """`obsidianizer folders`: read-only scan + card create/update."""

    root = Path(args.path).resolve()
    logging.info("папка: %s", root)
    if not root.is_dir():
        logging.error("Папка не существует: %s", root)
        return 1
    cfg = ObsidianizeConfig(
        force=args.force,
        adopt=getattr(args, "adopt", False),
        rebuild=getattr(args, "rebuild", False),
        img_gallery=not args.no_gallery,
        vault_root=args.vault_root or "",
        gallery_prefix=getattr(args, "gallery_prefix", "") or "",
        rel_root=getattr(args, "rel", "") or "",
        template=args.template,
        include_md=getattr(args, "include_md", True),
    )
    raw_select = getattr(args, "select", None)
    selection_provided = raw_select is not None
    selection: set[str] | None = None
    if selection_provided:
        from .obsidianize import _norm_sel_rel

        selection = set()
        for chunk in parse_list(raw_select):
            n = _norm_sel_rel(chunk)
            if n is None:
                logging.error("Некорректный --select rel: %r", chunk)
                return 2
            selection.add(n)
        # Validate against discovery rels (union rule, read-only).
        try:
            from .exclusions import build_tab1_exclusion
            from .obsidianize import discover_dir_rels, scan_tree

            settings = Settings.load()
            excl = build_tab1_exclusion(settings)
            known = set(scan_tree(root, cfg).keys()) | {
                str(d.get("rel") or "")
                for d in discover_dir_rels(
                    root, excl, list(settings.exclude_dirs or [])
                )
            }
            unknown = sorted(r for r in selection if r not in known)
            if unknown:
                logging.error("Неизвестные --select rel: %s", ", ".join(unknown))
                return 2
        except Exception as exc:  # noqa: BLE001 — validation is best-effort
            logging.warning("Проверка --select пропущена: %s", exc)
    try:
        summary = update_cards(
            root,
            cfg,
            dry_run=args.dry_run,
            recursive=not args.no_recursive,
            selection=selection,
            selection_provided=selection_provided,
        )
    except NotADirectoryError as exc:
        logging.error("%s", exc)
        return 1

    mode = "DRY-RUN (ничего не записано)" if args.dry_run else "OK"
    print(f"\n[{mode}] Папок просканировано: {summary.scanned}")
    print(f"Создано карточек: {summary.created}")
    print(f"Обновлено: {summary.updated}")
    print(f"Пропущено (без изменений): {summary.skipped}")
    if summary.conflicts:
        print(
            f"Конфликтов (чужая заметка; не перезаписана — нужен --force): "
            f"{len(summary.conflicts)}"
        )
        for conflict in summary.conflicts:
            print(f"  ! {conflict}")
    if summary.failed:
        print(
            f"Ошибок (папка пропущена, см. лог с traceback): "
            f"{len(summary.failed)}"
        )
        for rel in summary.failed:
            print(f"  x {rel}")
    return 0


def _make_enrichment_llm(settings: Settings) -> LLMClient | None:
    """Build the AI Enrichment client (TICKET-010), or ``None`` when disabled."""
    return make_enrichment_client(settings)


def _build_owner_index(settings: Settings):
    """Build the live SourceIndex for AI-stage owners (TICKET-008C).

    Same construction as ``pipeline.run`` (root + namespace + exclusions);
    feeds reconciliation and id-prune. Namespace persists only into an
    existing config file, never into a fresh path.
    """

    from pathlib import Path as _Path

    namespace, _ = ensure_source_namespace(
        settings,
        persist=bool(
            settings.config_path and _Path(str(settings.config_path)).exists()
        ),
    )
    return build_source_index(
        settings.source.resolve(), namespace, build_exclusion(settings)
    )


def _run_ai(settings: Settings, *, prune: bool = False) -> int:
    """`obsidianizer ai`: AI post-processing only, no re-import."""
    target = settings.target.resolve()
    enriched = settings.enriched.resolve()
    logging.info("processed: %s", target)
    logging.info("enriched: %s", enriched)
    if not target.is_dir():
        logging.error("Папка результата обработки не существует: %s", target)
        return 1
    llm = _make_enrichment_llm(settings)
    if llm is None:
        logging.error("LLM отключён — включите его в config.yml (или не передавайте --no-llm)")
        return 2
    logging.info("Ollama: %s", settings.ai_enrichment.model)

    report = postprocess_enrich(
        target, enriched, llm, prune=prune,
        exclusion_filter=build_exclusion(settings),
        source_index=_build_owner_index(settings),
    )

    print(f"[OK] AI-обработано: {report.processed}, пропущено: {report.skipped}")
    if report.reconcile_skipped:
        print("Reconcile пропущен: нет SourceIndex")
    if report.retired:
        print(f"Помечено retired: {len(report.retired)}")
        for rel in report.retired:
            print(f"  ~ {rel}")
    if report.transfers:
        print(f"Continuity-переносов: {len(report.transfers)}")
        for t in report.transfers:
            print(f"  > {t}")
    if report.pruned:
        print(f"Удалено сирот из AI-результата: {len(report.pruned)}")
        for rel in report.pruned:
            print(f"  - {rel}")
    if report.failed:
        print(f"Ошибок: {len(report.failed)}")
        for f in report.failed:
            print(f"  ! {f}")
    if report.cancelled:
        print("Отменено")
    if report.critical_error:
        print(f"Критическая ошибка: {report.critical_error}")
        return 3
    return 0 if not report.failed else 3


def _run_ui(args: argparse.Namespace) -> int:
    try:
        import webview  # noqa: F401 - dependency check only
    except ImportError:
        print(
            "Графический интерфейс требует установки extra 'ui'.\n"
            "Установите его командой:  pip install -e '.[ui]'",
            file=sys.stderr,
        )
        return 2
    from .ui import launch

    launch(
        initial_source=args.source,
        initial_target=args.target,
        initial_enriched=args.enriched,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ui":
        return _run_ui(args)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=_log_format(),
    )
    if args.command == "folders":
        return _run_folders(args)

    try:
        settings = Settings.load().apply_cli(
            source=args.source, target=args.target, enriched=args.enriched
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("%s", exc)
        return 2

    if args.no_llm:
        settings.disable_llm()
    if args.model:
        settings.ai_enrichment.model = args.model
    if getattr(args, "chat_model", None):
        settings.ai_chat.model = args.chat_model

    if args.command == "ai":
        return _run_ai(settings, prune=getattr(args, "prune_enriched", False))

    try:
        guard_check(settings.source, settings.target)
        guard_check(settings.target, settings.enriched)
        guard_check(settings.source, settings.enriched)  # AI must never write into raw
    except GuardError as exc:
        logging.error("%s", exc)
        return 1

    source = settings.source.resolve()
    target = settings.target.resolve()
    enriched = settings.enriched.resolve()
    logging.info("source: %s", source)
    logging.info("target: %s", target)
    logging.info("enriched: %s", enriched)

    # Determine chat_only from CLI flags or settings.
    if getattr(args, "chat_only", None) is not None:
        settings.chat_only = args.chat_only

    # CLI exclusions layer on top of config.yml (repeatable/CSV).
    cli_dirs: list[str] = []
    for raw in getattr(args, "exclude_dir", None) or []:
        cli_dirs.extend(parse_list(raw))
    cli_masks: list[str] = []
    for raw in getattr(args, "exclude_mask", None) or []:
        cli_masks.extend(parse_list(raw))
    if cli_dirs:
        settings.exclude_dirs = list(settings.exclude_dirs) + [
            d for d in cli_dirs if d not in settings.exclude_dirs
        ]
    if cli_masks:
        settings.exclude_masks = list(settings.exclude_masks) + [
            m for m in cli_masks if m not in settings.exclude_masks
        ]

    registry = ProcessorRegistry(
        filter_fn=build_filter(settings),
        exclusion_filter=build_exclusion(settings),
    )
    registry.register(".md", MdProcessor)

    # Prune-guard: disable prune when chat_only is active.
    effective_prune = args.prune and not settings.chat_only
    if args.prune and settings.chat_only:
        logging.warning("Prune disabled: chat_only is active (set chat_only: false in config.yml to override)")

    # Stage 1: import. Never calls the LLM; AI enrichment is stage 2.
    report = run_pipeline(
        registry,
        settings,
        dry_run=args.dry_run,
        prune=effective_prune,
    )

    mode = "DRY-RUN (ничего не записано)" if args.dry_run else "OK"
    print(f"\n[{mode}] Импортировано: {report.processed}, пропущено: {report.skipped}")
    print(f"Создано/обновлено файлов: {len(report.created)}")
    if report.pruned:
        print(f"Удалено по манифесту: {len(report.pruned)}")
        for rel in report.pruned:
            print(f"  - {rel}")
    if report.failed:
        print(f"Ошибок: {len(report.failed)}")
        for f in report.failed:
            print(f"  ! {f}")

    # TICKET-008B: additive backfill of source_id into old owned notes.
    # Independent of the Stage 1 outcome; never on dry-run (no writes).
    if getattr(args, "backfill_source_ids", False) and not args.dry_run:
        from .enrichment import backfill_source_ids

        _bf_total = 0
        _bf_unresolved: list[dict] = []
        for _owned in (target, enriched):
            _bf = backfill_source_ids(
                _owned,
                source_root=source,
                namespace=str(settings.source_namespace or ""),
                exclusion=build_exclusion(settings),
            )
            _bf_total += len(_bf["migrated"])
            _bf_unresolved.extend(
                {"rel": u["rel"], "reason": u["reason"], "in": _owned.name}
                for u in _bf["unresolved"]
            )
        print(f"[OK] Backfill source_id: связано {_bf_total}")
        for u in _bf_unresolved:
            print(f"  ? {u['in']}/{u['rel']}: {u['reason']}")

    if report.failed:
        return 3

    # Stage 2: AI post-processing of the freshly imported notes.
    if settings.llm_enabled and not args.dry_run:
        llm = _make_enrichment_llm(settings)
        if llm is not None:
            logging.info("Ollama: %s", settings.ai_enrichment.model)
            ai_report = postprocess_enrich(
                target,
                enriched,
                llm,
                prune=getattr(args, "prune_enriched", False),
                exclusion_filter=build_exclusion(settings),
                source_index=_build_owner_index(settings),
            )
            print(
                f"[OK] AI-обработано: {ai_report.processed}, "
                f"пропущено: {ai_report.skipped}"
            )
            if ai_report.retired:
                print(f"Помечено retired: {len(ai_report.retired)}")
                for rel in ai_report.retired:
                    print(f"  ~ {rel}")
            if ai_report.transfers:
                print(f"Continuity-переносов: {len(ai_report.transfers)}")
                for t in ai_report.transfers:
                    print(f"  > {t}")
            if ai_report.pruned:
                print(f"Удалено сирот из AI-результата: {len(ai_report.pruned)}")
                for rel in ai_report.pruned:
                    print(f"  - {rel}")
            if ai_report.failed:
                print(f"Ошибок AI: {len(ai_report.failed)}")
                for f in ai_report.failed:
                    print(f"  ! {f}")
                return 3
            if ai_report.critical_error:
                print(f"Критическая ошибка AI: {ai_report.critical_error}")
                return 3
    else:
        logging.info("LLM отключён — AI-постобработка пропущена")
    return 0


if __name__ == "__main__":
    sys.exit(main())