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
from .registry import ProcessorRegistry
from .pipeline import run as run_pipeline


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
    p.add_argument("--target", help="папка-результат (переопределяет config.yml)")
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
    p.add_argument("--version", action="version", version=f"obsidianizer {__version__}")
    p.add_argument("--verbose", action="store_true", help="подробный лог")
    return p


def _log_format() -> str:
    return "%(asctime)s [%(levelname)s] %(message)s"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=_log_format(),
    )

    try:
        settings = Settings.load().apply_cli(
            source=args.source, target=args.target
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("%s", exc)
        return 2

    if args.no_llm:
        settings.disable_llm()
    if args.model:
        settings.ollama["model"] = args.model

    try:
        guard_check(settings.source, settings.target)
    except GuardError as exc:
        logging.error("%s", exc)
        return 1

    source = settings.source.resolve()
    target = settings.target.resolve()
    logging.info("source: %s", source)
    logging.info("target: %s", target)

    registry = ProcessorRegistry()
    registry.register(".md", MdProcessor)

    llm: LLMClient | None = None
    if settings.llm_enabled:
        llm = LLMClient(
            endpoint=settings.ollama["endpoint"],
            model=settings.ollama["model"],
            timeout=settings.ollama["timeout"],
            limit_chars=settings.ollama["limit_chars"],
            temperature=settings.ollama["temperature"],
            num_ctx=settings.ollama["num_ctx"],
            prompt=settings.ollama["prompt"],
        )
        logging.info("Ollama: %s", settings.ollama["model"])
    else:
        logging.info("LLM отключён (--no-llm)")

    report = run_pipeline(
        registry,
        settings,
        llm,
        dry_run=args.dry_run,
        prune=args.prune,
    )

    mode = "DRY-RUN (ничего не записано)" if args.dry_run else "OK"
    print(f"\n[{mode}] Обработано: {report.processed}, пропущено: {report.skipped}")
    print(f"Создано/обновлено файлов: {len(report.created)}")
    if report.pruned:
        print(f"Удалено по манифесту: {len(report.pruned)}")
        for rel in report.pruned:
            print(f"  - {rel}")
    if report.failed:
        print(f"Ошибок: {len(report.failed)}")
        for f in report.failed:
            print(f"  ! {f}")
    return 0 if not report.failed else 3


if __name__ == "__main__":
    sys.exit(main())