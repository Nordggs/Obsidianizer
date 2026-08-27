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
from .config import Settings
from .guard import GuardError, check as guard_check
from .llm import LLMClient
from .md_processor import MdProcessor
from .obsidianize import ObsidianizeConfig, update_cards
from .registry import ProcessorRegistry
from .pipeline import run as run_pipeline
from .postprocess import enrich as postprocess_enrich


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
    p.add_argument("--model", help="модель Ollama для резюме/тегов")
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
    ai.add_argument("--model", help="модель Ollama для резюме/тегов")
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
        "--template",
        choices=("github", "classic"),
        default="github",
        help="шаблон карточки: github (Project Dashboard, по умолчанию) | classic",
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
        img_gallery=not args.no_gallery,
        vault_root=args.vault_root or "",
        template=args.template,
    )
    try:
        summary = update_cards(
            root,
            cfg,
            dry_run=args.dry_run,
            recursive=not args.no_recursive,
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
    return 0


def _make_llm(settings: Settings) -> LLMClient | None:
    """Build an LLM client from settings, or ``None`` when disabled."""
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
    llm = _make_llm(settings)
    if llm is None:
        logging.error("LLM отключён — включите его в config.yml (или не передавайте --no-llm)")
        return 2
    logging.info("Ollama: %s", settings.ollama["model"])

    report = postprocess_enrich(target, enriched, llm, prune=prune)

    print(f"[OK] AI-обработано: {report.processed}, пропущено: {report.skipped}")
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
        settings.ollama["model"] = args.model

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

    registry = ProcessorRegistry()
    registry.register(".md", MdProcessor)

    # Stage 1: import. Never calls the LLM; AI enrichment is stage 2.
    report = run_pipeline(
        registry,
        settings,
        None,
        dry_run=args.dry_run,
        prune=args.prune,
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

    if report.failed:
        return 3

    # Stage 2: AI post-processing of the freshly imported notes.
    if settings.llm_enabled and not args.dry_run:
        llm = _make_llm(settings)
        if llm is not None:
            logging.info("Ollama: %s", settings.ollama["model"])
            ai_report = postprocess_enrich(
                target,
                enriched,
                llm,
                prune=getattr(args, "prune_enriched", False),
            )
            print(
                f"[OK] AI-обработано: {ai_report.processed}, "
                f"пропущено: {ai_report.skipped}"
            )
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