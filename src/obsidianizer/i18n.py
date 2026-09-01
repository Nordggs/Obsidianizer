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
