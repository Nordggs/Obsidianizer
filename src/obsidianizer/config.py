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
from dataclasses import asdict, dataclass, field
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
class AIInfrastructureSettings:
    """Shared AI infrastructure (TICKET-010 ``ai.*``).

    Providers/endpoint/credentials plus the generation defaults both runtime
    contracts inherit. No model/prompt/state lives here — those belong to the
    specific contract (``ai_enrichment``/``ai_chat``).
    """

    endpoint: str = "http://localhost:11434"
    timeout: int = 180
    temperature: float = 0.2
    num_ctx: int = 8192
    limit_chars: int = 6000


@dataclass
class AIEnrichmentSettings:
    """AI Enrichment contract (TICKET-010 ``ai_enrichment.*``).

    Automatic processing: model, prompts (incl. folder review) and optional
    per-group generation overrides (``None`` → inherit ``ai.*``).
    """

    model: str = "deepseek-r1:14b"
    prompt: str = DEFAULT_PROMPT
    ai_prompt: str = DEFAULT_AI_PROMPT
    topic_prompt: str = DEFAULT_TOPIC_PROMPT
    map_prompt: str = DEFAULT_TOPIC_MAP_PROMPT
    folders_prompt: str = DEFAULT_FOLDERS_ANALYZE_PROMPT
    timeout: int | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    limit_chars: int | None = None


@dataclass
class AIChatSettings:
    """AI Chat contract (TICKET-010 ``ai_chat.*``).

    Interactive session: its own model + system prompt, the embedding/search
    layer, and optional per-group generation overrides (``None`` → inherit
    ``ai.*``). ``ai_chat.model`` is intentionally separate from
    ``ai_enrichment.model``.
    """

    model: str = "qwen2.5:latest"
    chat_prompt: str = DEFAULT_CHAT_PROMPT
    embed_model: str = "nomic-embed-text:latest"
    search_top_k: int = 30
    search_full_k: int = 3
    search_full_chars: int = 9000
    search_frag_chars: int = 600
    timeout: int | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    limit_chars: int | None = None


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _load_ai_groups(s: "Settings", data: dict) -> None:
    """Load the three AI groups, migrating the legacy flat ``ollama`` block.

    TICKET-010: a pre-010 config has a single flat ``ollama`` mapping. It is
    read into the groups (old keys keep working); the flat block is never
    written back by ``save()``. Group keys win over legacy keys.
    """

    legacy = _as_dict(data.get("ollama"))
    groups = {
        "ai": _as_dict(data.get("ai")),
        "ai_enrichment": _as_dict(data.get("ai_enrichment")),
        "ai_chat": _as_dict(data.get("ai_chat")),
    }

    def pick(group: str, key: str, legacy_key: str | None = None):
        g = groups[group]
        if key in g:
            return g[key]
        return legacy.get(legacy_key if legacy_key is not None else key)

    for key in ("endpoint", "timeout", "temperature", "num_ctx", "limit_chars"):
        val = pick("ai", key)
        if val is not None:
            setattr(s.ai, key, val)

    for key in (
        "model", "prompt", "ai_prompt", "topic_prompt", "map_prompt",
        "folders_prompt", "timeout", "temperature", "num_ctx", "limit_chars",
    ):
        val = pick("ai_enrichment", key)
        if val is not None:
            setattr(s.ai_enrichment, key, val)

    # Legacy ``chat_model`` becomes the chat contract's model.
    val = pick("ai_chat", "model", "chat_model")
    if val is not None:
        s.ai_chat.model = val
    for key in (
        "chat_prompt", "embed_model", "search_top_k", "search_full_k",
        "search_full_chars", "search_frag_chars", "timeout", "temperature",
        "num_ctx", "limit_chars",
    ):
        val = pick("ai_chat", key)
        if val is not None:
            setattr(s.ai_chat, key, val)


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
    chat_only: bool = True
    prune: bool = False
    prune_enriched: bool = False
    dry_run: bool = False
    # Source boundary exclusions (TICKET-006): "do not consider" layer for
    # tabs 2-3. Directory names match at any depth; masks are fnmatch
    # patterns against file names. GUI has no fields until TICKET-011 —
    # behaviour comes from config.yml (+ CLI overrides).
    exclude_dirs: list[str] = field(
        default_factory=lambda: [
            ".git",
            "node_modules",
            "venv",
            "__pycache__",
            "build",
            "dist",
            ".obsidian",
        ]
    )
    exclude_masks: list[str] = field(
        default_factory=lambda: ["*.tmp", "*.bak", "*.log"]
    )
    # Source record identity (TICKET-007): ``source_namespace`` is the
    # persisted identity of the Source record; ``source`` (root path) is its
    # mutable field. Moving an existing Source only changes the path and
    # keeps the namespace; a new Source gets a new UUID — even for the same
    # physical directory. "" = not assigned yet (assigned on first use).
    source_namespace: str = ""
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
    # AI configuration (TICKET-010): one shared infrastructure level plus two
    # independent runtime contracts. ``ai`` = providers/endpoint/credentials +
    # generation defaults; ``ai_enrichment`` = automatic processing; ``ai_chat``
    # = interactive session. The legacy flat ``ollama`` mapping is migrated on
    # load and never written back.
    ai: AIInfrastructureSettings = field(default_factory=AIInfrastructureSettings)
    ai_enrichment: AIEnrichmentSettings = field(default_factory=AIEnrichmentSettings)
    ai_chat: AIChatSettings = field(default_factory=AIChatSettings)

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
        if "chat_only" in data:
            s.chat_only = bool(data["chat_only"])
        if "prune" in data:
            s.prune = bool(data["prune"])
        if "prune_enriched" in data:
            s.prune_enriched = bool(data["prune_enriched"])
        if "dry_run" in data:
            s.dry_run = bool(data["dry_run"])
        if "exclude_dirs" in data and isinstance(data["exclude_dirs"], list):
            s.exclude_dirs = [str(x) for x in data["exclude_dirs"]]
        if "exclude_masks" in data and isinstance(data["exclude_masks"], list):
            s.exclude_masks = [str(x) for x in data["exclude_masks"]]
        if "source_namespace" in data:
            s.source_namespace = str(data["source_namespace"] or "")
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
        _load_ai_groups(s, data)
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
        data["chat_only"] = bool(self.chat_only)
        data["prune"] = bool(self.prune)
        data["prune_enriched"] = bool(self.prune_enriched)
        data["dry_run"] = bool(self.dry_run)
        data["exclude_dirs"] = list(self.exclude_dirs)
        data["exclude_masks"] = list(self.exclude_masks)
        # Persisted Source-record identity (TICKET-007); "" survives only
        # until the first ``ensure_source_namespace()`` call.
        data["source_namespace"] = str(self.source_namespace or "")
        data["obsidianize_dir"] = str(self.obsidianize_dir)
        data["integration_vault"] = str(self.integration_vault)
        data["obsidianize_vault_root"] = str(self.obsidianize_vault_root)
        data["obsidianize_gallery_prefix"] = str(self.obsidianize_gallery_prefix)
        data["obsidianize_template"] = str(self.obsidianize_template)
        data["language"] = str(self.language)
        # AI groups (TICKET-010). The legacy flat ``ollama`` block is retired:
        # drop it from the file on save.
        data["ai"] = asdict(self.ai)
        data["ai_enrichment"] = asdict(self.ai_enrichment)
        data["ai_chat"] = asdict(self.ai_chat)
        data.pop("ollama", None)

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


def ensure_source_namespace(
    settings: "Settings", *, persist: bool = True
) -> tuple[str, bool]:
    """Return the persisted Source-record namespace, creating it once if needed.

    Returns ``(namespace, created_now)``. A regular first creation is silent;
    a warning is logged only when the namespace is unexpectedly missing (an
    existing config file without the key — e.g. recovery after config loss).
    Silent re-generation on every launch is forbidden: the value is assigned
    once and then reused. Deleting ``config.yml`` means losing identity — a
    new namespace is minted with a warning.
    """

    import logging
    import uuid

    logger = logging.getLogger("obsidianizer.config")
    ns = str(getattr(settings, "source_namespace", "") or "")
    if ns:
        return ns, False
    existed = bool(
        getattr(settings, "config_path", None)
        and Path(str(settings.config_path)).exists()
    )
    ns = str(uuid.uuid4())
    settings.source_namespace = ns
    if persist:
        try:
            settings.save()
        except Exception:  # noqa: BLE001 - identity must survive a save failure
            logger.debug("Не удалось сохранить source_namespace", exc_info=True)
    if existed:
        logger.warning(
            "source_namespace отсутствовал в %s — создан новый %s; "
            "identity объектов Source изменилась",
            settings.config_path,
            ns,
        )
    return ns, True


class ConfigError(Exception):
    pass