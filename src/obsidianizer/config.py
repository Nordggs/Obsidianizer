"""Settings handling.

Precedence: CLI arguments > config.yml > defaults.
config.yml is discovered relative to the current working directory; the
repository ships only config.example.yml (never a real config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.yml"

DEFAULT_PROMPT = """Ты анализируешь содержимое файла заметок.

Выполни два действия:
1. Напиши краткое саммари (1-2 предложения) — о чём материал, что обсуждалось.
2. Предложи 3-5 тегов (ключевых слов), отражающих тематику.

Ответ формулируй на русском языке. Названия технологий, библиотек и продуктов
оставляй в оригинальном написании.

Формат ответа строго:

SUMMARY:
<саммари>

TAGS:
тег1, тег2, тег3

Содержимое:
---
{content}
---"""


@dataclass
class Settings:
    source: Path = Path("./raw")
    target: Path = Path("./processed")
    # LLM
    llm_enabled: bool = True
    ollama: dict = field(
        default_factory=lambda: {
            "endpoint": "http://localhost:11434",
            "model": "deepseek-r1:14b",
            "timeout": 180,
            "limit_chars": 6000,
            "temperature": 0.2,
            "num_ctx": 8192,
            "prompt": DEFAULT_PROMPT,
        }
    )

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        """Load from an optional config file, falling back to defaults."""

        cfg_path = path or Path.cwd() / DEFAULT_CONFIG_NAME
        if not cfg_path.exists():
            return cls()
        try:
            import yaml

            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 - config must never crash the tool
            raise ConfigError(f"Не могу прочитать конфиг {cfg_path}: {exc}") from exc

        s = cls()
        if "source" in data:
            s.source = Path(str(data["source"]))
        if "target" in data:
            s.target = Path(str(data["target"]))
        if "ollama" in data and isinstance(data["ollama"], dict):
            s.ollama = {**s.ollama, **data["ollama"]}
        return s

    def apply_cli(self, *, source: str | None, target: str | None) -> "Settings":
        if source:
            self.source = Path(source)
        if target:
            self.target = Path(target)
        return self

    def disable_llm(self) -> "Settings":
        self.llm_enabled = False
        return self


class ConfigError(Exception):
    pass