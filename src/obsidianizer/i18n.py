"""Minimal RU/EN string table for user-facing backend messages.

Principles (Ticket #6):

- **UI language ≠ data language.** Markdown cards, LLM prompts and the
  Templater template are *data* — they are never localized here.
- Only user-facing backend strings (errors returned to the GUI, pushed
  statuses, UI-visible log lines) go through ``tr()``.
- Technical details (paths, file names, exception texts) are interpolated
  as-is; developer/debug logger lines stay untouched.

The language is resolved once at startup (``ui.launch``): an explicit
``Settings.language`` wins; an empty value auto-detects from the OS locale.
"""

from __future__ import annotations

import locale

_current = "ru"

_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        # ── ui.py: validation / busy errors ─────────────────────────────
        "err.unknown_template": "неизвестный шаблон: {template}",
        "err.no_folder": "не выбрана папка",
        "err.folder_missing": "Папка не существует: {path}",
        "err.busy": "запуск уже выполняется",
        "err.no_window": "нет окна",
        "err.no_folder_picked": "Папка не выбрана",
        "err.pick_folder": "выберите хотя бы одну папку",
        "err.no_files": "не выбраны файлы",
        "err.no_topic": "не указана тема",
        "err.no_chats": "нет выбранных чатов",
        "err.assistant_busy": "ассистент уже отвечает",
        "err.empty_message": "пустое сообщение",
        "err.unknown_prompt": "Неизвестный промпт",
        "err.topic_missing": "Тема не найдена",
        "err.ollama_unavailable": "Ollama недоступен",
        "err.model_no_reply": "модель не ответила",
        "err.model_no_reply_check": "Модель не ответила — проверьте Ollama и модель",
        "err.llm_disabled": "LLM отключён — включите «AI-постобработку»",
        # ── ui.py: extract_templates ────────────────────────────────────
        "err.integration_missing": "Папка integration не найдена — переустановите программу",
        "err.no_template_files": "В {src} нет файлов шаблонов",
        "err.files_exist": "Файлы уже существуют. Повторный клик перезапишет их.",
        # ── ui.py: scan table fallbacks + summary ───────────────────────
        "scan.card_stale": "карточка устарела",
        "scan.content_changed": "изменилось содержимое проекта",
        "scan.summary": "Проверено: {checked} · без изменений: {unchanged} · требуют обновления: {changed}",
        # ── ui.py: pushed statuses / chat ───────────────────────────────
        "status.stopping": "Остановка… текущий файл будет завершён",
        "status.text_unavailable": "(текст недоступен)",
        # ── ui.py: user-visible warnings ────────────────────────────────
        "log.settings_save_failed": "Не удалось сохранить настройки: {exc}",
        # ── integration.py ──────────────────────────────────────────────
        "int.not_vault": "Это не Obsidian vault: не найдена папка .obsidian",
        "int.templater_missing": (
            "Плагин Templater не найден (нет .obsidian/plugins/{plugin}). "
            "Установите Templater в Obsidian и повторите установку интеграции."
        ),
        "int.exists": "Шаблон уже установлен. Повторный запуск перезапишет его (Repair).",
        "int.hint": (
            "В Obsidian: Настройки → Горячие клавиши → «Obsidianizer Update» "
            "→ назначьте Alt+3."
        ),
        # ── pipeline.py (stage 2: structured finish + localized log) ────
        "fin.processed": "обработано={n}",
        "fin.skipped": "пропущено={n}",
        "fin.errors": "ошибок={n}",
        "fin.cancelled": "отменено с сохранением уже записанных файлов",
        "fin.critical": "критическая ошибка: {err}",
        "pl.no_processor": "нет процессора для расширения {ext}",
        "pl.found_files": "Найдено {n} файлов для обработки",
        "pl.file_error": "Ошибка обработки {path}: {err}",
        "pl.critical_log": "Критическая ошибка запуска: {err}",
        # ── postprocess.py ──────────────────────────────────────────────
        "ai.processed": "AI-обработано={n}",
        "ai.cancelled": "AI-постобработка отменена",
        "ai.critical": "AI-критическая ошибка: {err}",
        "ai.pruned": "удалено сирот={n}",
        "ai.empty_reply": "пустой ответ модели",
        "ai.file_error": "Ошибка AI-постобработки {path}: {err}",
        "ai.critical_log": "Критическая ошибка AI-постобработки: {err}",
        "ai.orphan_removed": "Удалён сирота AI: {path}",
        # ── topics.py (UI strings only — Markdown content stays RU) ─────
        "top.path_outside": "путь за пределами папки processed",
        "top.file_missing": "файл не найден",
        "top.not_ours": "не файл Obsidianizer (нет frontmatter)",
        "top.no_hash": "нет маркера владения source_hash",
        "top.collect_log": "Ошибка сбора чата {path}: {err}",
        "top.no_files": "Не выбраны файлы",
        "top.no_readable": "Нет читаемых чатов для объединения",
        "top.empty_reply": "пустой ответ модели",
        "top.not_found_id": "Тема {id} не найдена",
        "top.not_found": "Тема не найдена",
        "top.empty_name": "Пустое имя темы",
        "top.name_exists": "Тема с таким именем уже существует",
        "top.read_failed": "Не удалось прочитать тему",
        "top.no_processed": "Нет обработанных чатов для авто-группировки",
        "top.one_chat": "один чат — тема не создана",
        "top.uptodate_short": "актуальна",
        "top.not_created": "тема не создана",
        "top.group_cancelled": "Авто-группировка отменена",
        "top.critical": "Критическая ошибка: {err}",
        "top.group_summary": (
            "Авто-группировка: создано={created}, актуально={skipped}, "
            "пропущено={one_chat}, ошибок={errors}"
        ),
        "top.merge_cancelled": "Объединение в тему отменено",
        "top.uptodate": "Тема актуальна: {name} (чаты не менялись)",
        "top.updated": "Тема обновлена: {name}",
        "top.created": "Тема создана: {name}",
        "top.none": "Тема не создана",
        "top.log.merge_failed": "Критическая ошибка объединения в тему: {err}",
        "top.log.update_failed": "Критическая ошибка обновления темы: {err}",
        "top.log.group_failed": "Критическая ошибка авто-группировки: {err}",
        # ── llm.py (UI-visible warnings) ────────────────────────────────
        "llm.empty_reply": "Ollama вернул пустой ответ — проверь модель/промпт",
        "llm.unavailable_skip": "Ollama недоступен ({exc}) — пропускаю AI-обогащение",
        "llm.error": "Ошибка Ollama: {exc}",
        "llm.empty_ai_reply": "Ollama вернул пустой AI-ответ — проверь модель/промпт",
        "llm.unavailable_skip_ai": "Ollama недоступен ({exc}) — пропуск AI-постобработки",
        "llm.error_ai": "Ошибка Ollama: {exc}",
        "llm.empty_topic_reply": "Ollama вернул пустой topic-ответ — проверь модель/промпт",
        "llm.unavailable_topic": "Ollama недоступен ({exc}) — пропуск анализа темы",
        "llm.error_topic": "Ошибка Ollama при анализе темы: {exc}",
        "llm.empty_map_reply": "Ollama вернул пустую карту тем — проверь модель/промпт",
        "llm.unavailable_map": "Ollama недоступен ({exc}) — пропуск карты тем",
        "llm.error_map": "Ошибка Ollama при построении карты тем: {exc}",
        "llm.unavailable_chat": "Ollama недоступен ({exc}) — чат не выполнен",
        "llm.error_chat": "Ошибка Ollama в чате: {exc}",
        "llm.unavailable_embed": "Ollama недоступен ({exc}) — эмбеддинги не построены",
        "llm.error_embed": "Ошибка Ollama при построении эмбеддингов: {exc}",
        "llm.unavailable_models": "Ollama недоступен ({exc}) — не удалось получить модели",
        "llm.error_models": "Ошибка Ollama при получении моделей: {exc}",
        # ── guard.py (user-facing path-safety errors) ───────────────────
        "guard.same_path": (
            "Первый и второй путь совпадают: {first}\n"
            "Запуск запрещён — этап уничтожил бы собственный вход."
        ),
        "guard.second_inside_first": (
            "Второй путь находится внутри первого:\n"
            "  первый: {first}\n"
            "  второй: {second}\n"
            "Запуск запрещён — предусмотренные результаты были бы видны как новые входные данные."
        ),
        "guard.first_inside_second": (
            "Первый путь находится внутри второго:\n"
            "  первый: {first}\n"
            "  второй: {second}\n"
            "Запуск запрещён — этап стал бы сканировать собственные результаты."
        ),
    },
    "en": {
        "err.unknown_template": "unknown template: {template}",
        "err.no_folder": "no folder selected",
        "err.folder_missing": "Folder does not exist: {path}",
        "err.busy": "a run is already in progress",
        "err.no_window": "no window",
        "err.no_folder_picked": "No folder selected",
        "err.pick_folder": "select at least one folder",
        "err.no_files": "no files selected",
        "err.no_topic": "no topic specified",
        "err.no_chats": "no chats selected",
        "err.assistant_busy": "assistant is already replying",
        "err.empty_message": "empty message",
        "err.unknown_prompt": "Unknown prompt",
        "err.topic_missing": "Topic not found",
        "err.ollama_unavailable": "Ollama is unavailable",
        "err.model_no_reply": "model gave no reply",
        "err.model_no_reply_check": "Model gave no reply — check Ollama and the model",
        "err.llm_disabled": "LLM is disabled — enable «AI post-processing»",
        "err.integration_missing": "integration folder not found — reinstall the app",
        "err.no_template_files": "No template files in {src}",
        "err.files_exist": "Files already exist. Click again to overwrite them.",
        "scan.card_stale": "card is stale",
        "scan.content_changed": "project content changed",
        "scan.summary": "Checked: {checked} · unchanged: {unchanged} · need update: {changed}",
        "status.stopping": "Stopping… the current file will finish",
        "status.text_unavailable": "(text unavailable)",
        "log.settings_save_failed": "Failed to save settings: {exc}",
        "int.not_vault": "This is not an Obsidian vault: .obsidian folder not found",
        "int.templater_missing": (
            "Templater plugin not found (.obsidian/plugins/{plugin} missing). "
            "Install Templater in Obsidian and retry the integration."
        ),
        "int.exists": "Template is already installed. Running again will overwrite it (Repair).",
        "int.hint": (
            "In Obsidian: Settings → Hotkeys → «Obsidianizer Update» "
            "→ assign Alt+3."
        ),
        # ── pipeline.py ─────────────────────────────────────────────────
        "fin.processed": "processed={n}",
        "fin.skipped": "skipped={n}",
        "fin.errors": "errors={n}",
        "fin.cancelled": "cancelled, already-written files kept",
        "fin.critical": "critical error: {err}",
        "pl.no_processor": "no processor for extension {ext}",
        "pl.found_files": "Found {n} files to process",
        "pl.file_error": "Error processing {path}: {err}",
        "pl.critical_log": "Critical run error: {err}",
        # ── postprocess.py ──────────────────────────────────────────────
        "ai.processed": "AI-processed={n}",
        "ai.cancelled": "AI post-processing cancelled",
        "ai.critical": "AI critical error: {err}",
        "ai.pruned": "orphans removed={n}",
        "ai.empty_reply": "empty model reply",
        "ai.file_error": "AI post-processing error {path}: {err}",
        "ai.critical_log": "Critical AI post-processing error: {err}",
        "ai.orphan_removed": "Removed AI orphan: {path}",
        # ── topics.py ───────────────────────────────────────────────────
        "top.path_outside": "path outside the processed folder",
        "top.file_missing": "file not found",
        "top.not_ours": "not an Obsidianizer file (no frontmatter)",
        "top.no_hash": "no source_hash ownership marker",
        "top.collect_log": "Chat collection error {path}: {err}",
        "top.no_files": "No files selected",
        "top.no_readable": "No readable chats to merge",
        "top.empty_reply": "empty model reply",
        "top.not_found_id": "Topic {id} not found",
        "top.not_found": "Topic not found",
        "top.empty_name": "Empty topic name",
        "top.name_exists": "A topic with this name already exists",
        "top.read_failed": "Failed to read the topic",
        "top.no_processed": "No processed chats for auto-grouping",
        "top.one_chat": "single chat — no topic created",
        "top.uptodate_short": "up to date",
        "top.not_created": "topic not created",
        "top.group_cancelled": "Auto-grouping cancelled",
        "top.critical": "Critical error: {err}",
        "top.group_summary": (
            "Auto-grouping: created={created}, up-to-date={skipped}, "
            "single-chat={one_chat}, errors={errors}"
        ),
        "top.merge_cancelled": "Topic merge cancelled",
        "top.uptodate": "Topic up to date: {name} (chats unchanged)",
        "top.updated": "Topic updated: {name}",
        "top.created": "Topic created: {name}",
        "top.none": "No topic created",
        "top.log.merge_failed": "Critical topic-merge error: {err}",
        "top.log.update_failed": "Critical topic-update error: {err}",
        "top.log.group_failed": "Critical auto-grouping error: {err}",
        # ── llm.py ──────────────────────────────────────────────────────
        "llm.empty_reply": "Ollama returned an empty reply — check the model/prompt",
        "llm.unavailable_skip": "Ollama unavailable ({exc}) — skipping AI enrichment",
        "llm.error": "Ollama error: {exc}",
        "llm.empty_ai_reply": "Ollama returned an empty AI reply — check the model/prompt",
        "llm.unavailable_skip_ai": "Ollama unavailable ({exc}) — skipping AI post-processing",
        "llm.error_ai": "Ollama error: {exc}",
        "llm.empty_topic_reply": "Ollama returned an empty topic reply — check the model/prompt",
        "llm.unavailable_topic": "Ollama unavailable ({exc}) — skipping topic analysis",
        "llm.error_topic": "Ollama error during topic analysis: {exc}",
        "llm.empty_map_reply": "Ollama returned an empty topic map — check the model/prompt",
        "llm.unavailable_map": "Ollama unavailable ({exc}) — skipping topic map",
        "llm.error_map": "Ollama error while building the topic map: {exc}",
        "llm.unavailable_chat": "Ollama unavailable ({exc}) — chat not executed",
        "llm.error_chat": "Ollama error in chat: {exc}",
        "llm.unavailable_embed": "Ollama unavailable ({exc}) — embeddings not built",
        "llm.error_embed": "Ollama error while building embeddings: {exc}",
        "llm.unavailable_models": "Ollama unavailable ({exc}) — could not list models",
        "llm.error_models": "Ollama error while listing models: {exc}",
        # ── guard.py ────────────────────────────────────────────────────
        "guard.same_path": (
            "The first and second paths are the same: {first}\n"
            "Run forbidden — the stage would destroy its own input."
        ),
        "guard.second_inside_first": (
            "The second path is inside the first one:\n"
            "  first: {first}\n"
            "  second: {second}\n"
            "Run forbidden — the produced results would be seen as new input."
        ),
        "guard.first_inside_second": (
            "The first path is inside the second one:\n"
            "  first: {first}\n"
            "  second: {second}\n"
            "Run forbidden — the stage would scan its own results."
        ),
    },
}


def resolve_language(raw: str | None) -> str:
    """Return a supported language code for ``raw`` ("" → OS locale)."""

    raw = (raw or "").strip().lower()
    if raw in ("ru", "en"):
        return raw
    try:
        loc = locale.getlocale()[0] or ""
    except (ValueError, OSError):
        loc = ""
    return "ru" if loc.lower().startswith("ru") else "en"


def set_language(lang: str) -> None:
    """Switch the active UI language ("ru" | "en" | "" → auto)."""

    global _current
    _current = resolve_language(lang)


def get_language() -> str:
    return _current


def tr(key: str, **kwargs: object) -> str:
    """Translate ``key`` in the active language; unknown keys pass through."""

    text = _STRINGS.get(_current, {}).get(key)
    if text is None:
        other = "en" if _current != "en" else "ru"
        text = _STRINGS[other].get(key, key)
    return text.format(**kwargs) if kwargs else text
