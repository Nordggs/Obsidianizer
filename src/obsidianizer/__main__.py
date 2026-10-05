"""Allow running as `python -m obsidianizer`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())