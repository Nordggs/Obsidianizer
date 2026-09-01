"""Settings handling.

Precedence: CLI arguments > config.yml > defaults.
config.yml is discovered relative to the current working directory; the
repository ships only config.example.yml (never a real config).

``Settings`` is also the single source of persistent GUI state: the UI reads
it on startup and writes it back via ``save()`` (merge + atomic write), so a
closed-and-reopened window restores every path and checkbox.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.yml"


def _user_data_dir() -> Path:
    """Writable directory for config, log, etc."""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Obsidianizer"
    return Path(__file__).resolve().parents[2]

# Bumped whenever the produced-note format changes. The pipeline writes it
# into every frontmatter and reprocesses files carrying an older version, so a
# code change invalidates stale processed notes without manual cleanup.
PROCESS_VERSION = 3

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


DEFAULT_AI_PROMPT = """Проанализируй диалог.

SUMMARY:
Кратко (3–5 предложений) опиши тему разговора, главные выводы и принятые
решения. Если есть незавершённые задачи или следующие шаги — укажи их.
Не пересказывай разговор по порядку и не выдумывай информацию.

TAGS:
Дай 3–7 коротких тегов по теме разговора.

TOPIC:
Короткая тема разговора (2–4 слова).

TYPE:
Один из: обсуждение, вопрос, планирование, обучение, отладка, проект, идея, другое.

Содержимое:
---
{content}
---"""


DEFAULT_TOPIC_PROMPT = """Проанализируй несколько разговоров, объединённых в один файл
(каждый помечен заголовком «## Файл»). Составь по всей группе единую справку.

NAME:
Короткое название темы (2–5 слов, на русском, без кавычек и спецсимволов).

SUMMARY:
3–5 предложений: что обсуждалось во всех разговорах вместе и главные выводы.

DECISIONS:
- Список решений, принятых в этих разговорах. Каждый пункт с новой строки
  с «- ». Если решений нет — напиши «- Нет».

KEY_FACTS:
- Список ключевых фактов и договорённостей. Каждый пункт с новой строки
  с «- ». Если фактов нет — напиши «- Нет».

ARTIFACTS:
- Список артефактов: файлы, скрипты, функции и объекты, о которых шла речь.
  Каждый пункт с новой строки с «- ». Если артефактов нет — напиши «- Нет».

Не выдумывай информацию — используй только то, что есть в разговорах.

Содержимое:
---
{content}
---"""


DEFAULT_TOPIC_MAP_PROMPT = """Ниже список чатов с номерами. Сгруппируй их в темы по содержанию.

Каждый чат относится только к одной теме. Чаты, которые не подходят ни к
одной теме, не включай ни в какую. Названия тем формулируй коротко (2–5 слов,
на русском). Если уже есть «Уже выделенные темы» — используй те же названия,
когда тема совпадает.

Формат ответа строго, по одному блоку на тему:

TOPIC:
<название темы>

IDS:
<номера чатов через запятую>

Не выдумывай номера — используй только те, что есть в списке.

Содержимое:
---
{content}
---"""


DEFAULT_CHAT_PROMPT = """Ты — локальный AI-ассистент в Obsidian-хранилище пользователя.

Твоя главная задача — анализировать результаты поиска по коллекции чатов и
помогать ориентироваться в ней. В системный контекст тебе подаются найденные
чаты (заголовок, путь rel, фрагмент) в порядке убывания релевантности, а для
самых релевантных — и полные тексты диалогов.

Правила ответа:
- Отвечай на русском языке, кратко и по делу.
- Отвечай ТОЛЬКО на основании предоставленных источников. Если вопрос
  касается хранилища: скажи, сколько чатов нашлось, перечисли релевантные с
  путём (rel) и краткой причиной, почему они подходят; отметь наиболее
  вероятные. Ссылайся ТОЛЬКО на чаты из переданного списка — не выдумывай rel
  и не добавляй чаты, которых нет в списке.
- Если вопрос требует конкретного решения или итога (что выбрали, какой
  вариант, что решили) — ищи ответ в содержимом диалогов, особенно ближе к
  концу обсуждения, где обычно зафиксировано итоговое решение. Приведи
  подтверждающий фрагмент из диалога.
- Если спрашивают про выбор снаряжения или билда (реликвия, руна, амулет,
  оружие, специализация) — ищи в полных текстах таблицы «Снаряжение»/«Билд»
  и называй конкретные предметы из них (например «Реликвия: Реки»).
- Проверяй ВСЕ переданные источники, а не только первый: ответ может лежать
  во втором-третьем чате.
- Если найдены лишь частичные совпадения — так и скажи и покажи то, что есть.
- Если результатов нет совсем — честно сообщи об этом и предложи уточнить
  запрос (другие слова, синонимы, другой провайдер).
- Если вопрос не касается хранилища — отвечай из общих знаний.
- Не говори «информации недостаточно», не сделав попытки опереться на
  переданные результаты поиска."""


DEFAULT_FOLDERS_ANALYZE_PROMPT = """Ты — архитектурный аналитик проектной документации.
Проанализируй папку проекта по данным ниже: карточка проекта (если есть), состав
файлов по категориям, подпапки, содержимое текстовых файлов (если включено).

Дай структурированный обзор на русском языке в формате Markdown:

## Назначение
Что это за проект и чем занимается (по карточке и содержимому).

## Состав
Кратко: категории файлов с примерами названий, подпапки, общее число файлов.
Не перечисляй каждый файл — только характерные примеры и их количество.

## Ключевые детали
Что известно из текстовых материалов: параметры, решения, характеристики,
используемые материалы/оборудование. Если текстов нет — так и напиши.

## Незакрытые вопросы и риски
Чего не хватает, что противоречиво или требует внимания. Если таких пунктов
нет — напиши «Явных проблем не выявлено».

Правила:
- Используй ТОЛЬКО данные из карточки и файлов — не выдумывай факты.
- Если информации мало — честно напиши, чего не хватает.
- Не упоминай «карточку проекта» как источник в самом обзоре.
- Без маркдаун-блоков и оглавления; заголовки секций — как в шаблоне выше."""


@dataclass
class Settings:
    source: Path = Path("./raw")
    target: Path = Path("./processed")
    # Final AI-enriched vault (result of the second stage). A sibling of the
    # plain result by default; must never live inside ``target`` (the AI stage
    # would re-read its own output).
    enriched: Path = Path("./enriched")
    # Persistent GUI flags. Written back by the UI so a reopened window shows
    # exactly the state it was closed in; also usable from config.yml.
    llm_enabled: bool = True
    prune: bool = False
    prune_enriched: bool = False
    dry_run: bool = False
    # Folder Obsidianizer tab (persisted GUI state)
    obsidianize_dir: str = ""
    integration_vault: str = ""  # Obsidian vault root for the Integration tab
    obsidianize_vault_root: str = ""
    obsidianize_gallery_prefix: str = ""  # fallback vault path prefix for img-gallery
    obsidianize_template: str = "github"  # github (Project Dashboard) | classic
    # UI language: "ru" | "en"; "" = auto-detect from the OS locale.
    # Data (Markdown cards, prompts) is never localized — see i18n.py.
    language: str = ""
    # Where ``save()`` writes (when given); falls back to CWD config.yml.
    # ``load()`` pins this to the file it actually read.
    config_path: Path | None = None
    # LLM
    ollama: dict = field(
        default_factory=lambda: {
            "endpoint": "http://localhost:11434",
            "model": "deepseek-r1:14b",
            "timeout": 180,
            "limit_chars": 6000,
            "temperature": 0.2,
            "num_ctx": 8192,
            "prompt": DEFAULT_PROMPT,
            "ai_prompt": DEFAULT_AI_PROMPT,
            "topic_prompt": DEFAULT_TOPIC_PROMPT,
            "map_prompt": DEFAULT_TOPIC_MAP_PROMPT,
            "chat_prompt": DEFAULT_CHAT_PROMPT,
            "folders_prompt": DEFAULT_FOLDERS_ANALYZE_PROMPT,
            "chat_model": "qwen2.5:latest",
            "embed_model": "nomic-embed-text:latest",
            "search_top_k": 30,
            "search_full_k": 3,
            "search_full_chars": 9000,
            "search_frag_chars": 600,
        }
    )

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        """Load from an optional config file, falling back to defaults."""

        cfg_path = path or _user_data_dir() / DEFAULT_CONFIG_NAME
        s = cls()
        s.config_path = cfg_path
        if not cfg_path.exists():
            return s
        try:
            import yaml

            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 - config must never crash the tool
            raise ConfigError(f"Не могу прочитать конфиг {cfg_path}: {exc}") from exc

        if "source" in data:
            s.source = Path(str(data["source"]))
        if "target" in data:
            s.target = Path(str(data["target"]))
        if "enriched" in data:
            s.enriched = Path(str(data["enriched"]))
        if "llm_enabled" in data:
            s.llm_enabled = bool(data["llm_enabled"])
        if "prune" in data:
            s.prune = bool(data["prune"])
        if "prune_enriched" in data:
            s.prune_enriched = bool(data["prune_enriched"])
        if "dry_run" in data:
            s.dry_run = bool(data["dry_run"])
        if "obsidianize_dir" in data:
            s.obsidianize_dir = str(data["obsidianize_dir"])
        if "integration_vault" in data:
            s.integration_vault = str(data["integration_vault"])
        if "obsidianize_vault_root" in data:
            s.obsidianize_vault_root = str(data["obsidianize_vault_root"])
        if "obsidianize_gallery_prefix" in data:
            s.obsidianize_gallery_prefix = str(data["obsidianize_gallery_prefix"])
        if "obsidianize_template" in data:
            s.obsidianize_template = str(data["obsidianize_template"])
        if "language" in data:
            s.language = str(data["language"])
        if "ollama" in data and isinstance(data["ollama"], dict):
            s.ollama = {**s.ollama, **data["ollama"]}
        return s

    def save(self, path: Path | None = None) -> Path:
        """Write settings back to config.yml.

        Merges into any existing file (foreign keys a user edited by hand are
        kept), updates the known keys and writes atomically (.tmp -> replace).
        Returns the written path.
        """

        cfg_path = path or self.config_path or _user_data_dir() / DEFAULT_CONFIG_NAME
        data: dict = {}
        if cfg_path.exists():
            try:
                import yaml

                existing = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                if isinstance(existing, dict):
                    data = existing
            except Exception:  # noqa: BLE001 - a broken file must not block saving
                data = {}
        data["source"] = str(self.source)
        data["target"] = str(self.target)
        data["enriched"] = str(self.enriched)
        data["llm_enabled"] = bool(self.llm_enabled)
        data["prune"] = bool(self.prune)
        data["prune_enriched"] = bool(self.prune_enriched)
        data["dry_run"] = bool(self.dry_run)
        data["obsidianize_dir"] = str(self.obsidianize_dir)
        data["integration_vault"] = str(self.integration_vault)
        data["obsidianize_vault_root"] = str(self.obsidianize_vault_root)
        data["obsidianize_gallery_prefix"] = str(self.obsidianize_gallery_prefix)
        data["obsidianize_template"] = str(self.obsidianize_template)
        data["language"] = str(self.language)
        existing_ollama = data.get("ollama")
        merged_ollama = dict(existing_ollama) if isinstance(existing_ollama, dict) else {}
        merged_ollama.update(self.ollama)
        data["ollama"] = merged_ollama

        import yaml

        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg_path.with_name(cfg_path.name + ".tmp")
        tmp.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        os.replace(tmp, cfg_path)
        self.config_path = cfg_path
        return cfg_path

    def apply_cli(
        self,
        *,
        source: str | None,
        target: str | None,
        enriched: str | None = None,
    ) -> "Settings":
        if source:
            self.source = Path(source)
        if target:
            self.target = Path(target)
        if enriched:
            self.enriched = Path(enriched)
        return self

    def disable_llm(self) -> "Settings":
        self.llm_enabled = False
        return self


class ConfigError(Exception):
    pass