"""Obsidianizer — точка запуска графического интерфейса.

Двойной клик по Obsidianizer.py (или `python Obsidianizer.py`) открывает GUI.
Код приложения живёт в src/obsidianizer/; этот файл — только тонкая точка
входа: он добавляет `src/` в sys.path и вызывает `obsidianizer.ui.launch()`.

Флаги:
    --check   проверить окружение и выйти без открытия окна (диагностика).

CLI (`obsidianizer ...`) остаётся для автоматизации и неизменен.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _print_check() -> int:
    """Diagnostics: version, package location, webview status. Never opens UI."""

    try:
        from obsidianizer import __version__
        from obsidianizer.ui import RESOURCES
    except Exception as exc:  # noqa: BLE001 - diagnostics must print, not crash
        print(f"Ошибка инициализации Obsidianizer: {exc}")
        return 1

    print(f"Obsidianizer {__version__}")
    print(f"пакет: {_SRC / 'obsidianizer'}")
    print(f"ресурсы GUI: {RESOURCES}")
    try:
        import webview

        print(f"webview: доступен ({getattr(webview, '__version__', '?')})")
    except ImportError:
        print("webview: НЕ установлен — запустите:  pip install -e '.[ui]'")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="Obsidianizer.py",
        description="Запуск графического интерфейса Obsidianizer.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="проверить окружение и выйти без открытия окна",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _print_check()

    try:
        import webview  # noqa: F401 - required by the GUI
    except ImportError:
        print(
            "Для графического интерфейса нужен extra 'ui'.\n"
            "Установите его командой:  pip install -e '.[ui]'",
            file=sys.stderr,
        )
        return 1

    try:
        from obsidianizer.ui import launch
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка инициализации Obsidianizer: {exc}", file=sys.stderr)
        return 1

    launch()
    return 0


if __name__ == "__main__":
    sys.exit(main())