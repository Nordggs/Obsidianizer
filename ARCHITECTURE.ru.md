# Архитектура — Obsidianizer v0.7.3

## Обзор

Obsidianizer — локальный инструмент, который подготавливает проектные материалы
для работы в [Obsidian](https://obsidian.md) и поддерживает их в порядке дальше.
Облачных зависимостей нет: всё работает на машине, дерево-источник никогда не
изменяется, а опциональный AI-слой обращается только к локальной Ollama.

Приложение объединяет **три инструмента** на одном ядре. Два фронтенда
(GUI на pywebview и CLI) вызывают одни и те же модули, поэтому поведение
идентично независимо от способа запуска.

```
   Obsidianizer.py / .bat            obsidianizer  (console script)
        (GUI-лаунчер)                 obsidianizer-cli.bat
              │                               │
              ▼                               ▼
        ui.launch()                      cli.main()
        (окно webview)                   (argparse)
              │                               │
              └───────────────┬───────────────┘
                              ▼
                          ядро модулей
   ┌──────────────┬──────────────┬───────────────┬──────────────┐
   ▼              ▼              ▼               ▼              ▼
obsidianize.py  pipeline.py   postprocess.py  topics.py    search.py / review.py
карточки папок  импорт        AI-enrich       темы         AI-анализ
```

## Три инструмента

| # | Инструмент | Точка входа | Что делает |
|---|-----------|-------------|------------|
| 1 | **Obsidianize** | вкладка 1 GUI, `obsidianizer folders` | Read-only скан обычной папки, карточка Markdown на каждую папку, пользовательские данные — в парном файле заметок |
| 2 | **Обработка (Enrichment)** | GUI «Обработка» (Источник/Обнаружено/Enrichment/AI-настройки/Обзоры папок/Инструменты), `obsidianizer` / `obsidianizer ai` | Импорт Markdown в `processed/`, затем AI-enrich в `enriched/` |
| 3 | **AI-инструменты** | Обзор папок (раздел «Обработки»), `search` / `review` / `topics`, отдельное окно чата | Локальный LLM: поиск, обзор папки, сборка тем, интерактивный чат |

## Карта модулей (`src/obsidianizer/`)

| Модуль | Ответственность |
|--------|-----------------|
| `cli.py` | Точка входа argparse (подкоманды `ui` / `ai` / `folders` + глобальные флаги) |
| `ui.py` | Мост pywebview `UIApp`: вкладки, события, настройки, окна (main/chat/help), проверка обновлений |
| `config.py` | `Settings` load/save/merge (`config.yml`), дефолтные промпты, `PROCESS_VERSION` |
| `events.py` | Единственный источник правды для `EventType` / `Event` |
| `obsidianize.py` | Движок карточек папок: скан, свежесть, рендер, сохранение заметок/комментариев |
| `pipeline.py` | Стадия 1 (импорт): scan → extract → emit → manifest/prune |
| `postprocess.py` | Стадия 2 (AI-enrich): `processed/ → enriched/` |
| `registry.py`, `base.py`, `models.py` | Реестр процессоров и записи данных |
| `md_processor.py` | Обработчик Markdown (регэксп-метаданные, тело verbatim, ссылки на media) |
| `enrich.py` | Сборка заметки/frontmatter/навигации для пайплайна |
| `emit.py`, `manifest.py`, `guard.py` | Атомарные записи, манифест владения + prune, защита от перекрытия путей |
| `search.py` | Персистентный индекс чатов (лексика + эмбеддинги), инкрементальное обновление |
| `review.py` | AI-обзор папки → `<папка>_обзор.md` |
| `topics.py` | Конструктор тем (`create_topic`) и авто-группировка (`group_all`) |
| `llm.py` | Клиент Ollama (`/api/generate`, `/api/chat`, `/api/embed`, `/api/tags`) |
| `integration.py` | Установка/починка шаблона Templater и его статус |
| `i18n.py` | Строки RU/EN и переключение языка |
| `nav_index.py` | Генерация `_index.md` для деревьев processed/enriched |

## Инструмент 1 — Folder Obsidianizer (`obsidianize.py`)

Read-only скан строит GitHub-подобную карточку на каждую папку; дерево-источник
никогда не пишется (используются только `os.scandir` / `os.stat`).

```
scan_tree ─► update_cards ─► _process_folder ─┬─ card_status        (ok/stale/missing/conflict)
                                              ├─ _ensure_notes_file (слой пользователя, пишется первым)
                                              ├─ _sync_comments_to_notes
                                              └─ build_card ─► _render_dashboard ─► write_atomic
```

- **Карточка и заметки.** Карточка (`<папка>.md`) несёт *только сервисный
  frontmatter* (`obsidianizer: true`, `obsidianizer_hash`, `obsidianizer_template`,
  `obsidianizer_version`, `cssclasses`) и полностью регенерируема. Все
  пользовательские данные — в `<папка>_заметки.md`, встраиваемом как
  `![[<папка>_заметки]]`. Файл заметок создаётся один раз и не перезаписывается;
  он пишется **до** карточки, чтобы сбой не потерял данные.
- **Сохранение frontmatter.** `parse_frontmatter` использует PyYAML и
  нормализует не-строковые ключи в строки, с терпимым построчным fallback при
  `YAMLError`. `_user_props` сохраняет все не-сервисные ключи; старые имена
  перемаппируются (`клиент → проект`, `дизайнер → контакт`).
- **Комментарии.** В таблице Files есть редактируемая `Comments` (плюс
  авто-колонка `Description (Auto)`); в таблице Folders — `Comments`. Правки
  таблицы синхронизируются во frontmatter заметок (`file_comments` /
  `folder_comments`) на каждом прогоне — даже когда карточка пропускается, —
  поэтому переживают перегенерацию.
- **Свежесть.** `folder_fingerprint` — sha1 по `(rel, size, content hash)` +
  подпапки + наличие review-файла; mtime сознательно исключён. Скрытый манифест
  внутри карточки хранит хэши файлов и хэш данных заметок, поэтому `card_status`
  возвращает `ok / stale / missing / conflict`, а `card_diff` объясняет, *что*
  изменилось.
- **Интеграция с Templater.** `integration.py` подставляет `{{CLI_PATH}}` в
  шаблон и устанавливает его в папку Templater; хоткей запускает
  `folders --no-recursive --adopt --vault-root --rel` для текущей заметки.

### Структура карточки

Шапка репозитория → `Folders` → `Files` → `About` → `Gallery` → `Images` →
`AI Review` → `Notes` → футер.

| Таблица | Колонки |
|---------|---------|
| Folders | Name, Files, Size, Updated, Comments |
| Files | File, Type, Opens with, Modified, Size, Description (Auto), Comments |

## Инструмент 2 — Пайплайн обработки чатов

Две фиксированные стадии с физически раздельными выходами:

```
raw (SOURCE, read-only) ──импорт──► processed/ ──AI──► enriched/ ──► Obsidian vault
```

```
guard ─► registry.scan ─► processor.parse/body/media_refs ─► emit.write_note/copy_media
      ─► manifest (.obsidianizer-manifest.json) ─► [--prune]
      ─► postprocess.enrich ─► [--prune-enriched] ─► _index.md
```

- **guard** (`guard.py`) отвергает перекрывающиеся пары путей: `same`, `второй
  внутри первого`, `первый внутри второго`; проверяются `(source, target)`,
  `(target, enriched)`, `(source, enriched)`. Отказ возвращает код 1. (У
  `folders` guard намеренно отсутствует — карточки пишутся в сканируемую папку
  по дизайну.)
- **Импорт** (`pipeline.run`) без LLM; пишет заметки и копирует media атомарно
  (`.tmp → os.replace`).
- **Manifest/prune**: `--prune` удаляет ровно `OLD − CURRENT` из числа
  собственных файлов; чужие файлы не трогаются. Манифест пишется последним,
  атомарно.
- **AI-enrich** (`postprocess.enrich`) — отдельная явная стадия (`obsidianizer ai`
  или кнопка в GUI). `processed/` здесь никогда не пишется.
- **Инкрементальность** на хэшах:
  - `source_hash` = sha1 сырого файла (frontmatter стадии 1);
  - `ai_hash` = `source_hash` разобранной заметки (маркер стадии 2, основание
    владения для `--prune-enriched`);
  - `PROCESS_VERSION` (`config.py`) форсирует перезапись при смене формата
    вывода, даже если `source_hash` совпадает.

## Инструмент 3 — AI-анализ

- **Поиск** (`search.py`): персистентный `ChatIndex` под
  `<enriched>/.obsidianizer/` (`index.json` + `embeddings.json`), обновляется
  инкрементально по `source_hash`. Итоговый скор смешивает лексический балл
  (IDF-подобный boost токенов) и косинусную близость эмбеддингов; сбой
  эмбеддингов молча деградирует до лексики.
- **Обзор** (`review.py`): собрать payload папки → один вызов LLM →
  `build_review_markdown` → `<папка>_обзор.md` (атомарно). Карточка встраивает
  его в секцию `AI Review`.
- **Темы** (`topics.py`): `create_topic` сливает несколько чатов в
  `enriched/topics/<Name>.md` (`topic_hash` = sha1 отсортированных выбранных
  `source_hash` → пропуск при совпадении). `group_all` кластеризует всю
  коллекцию map-проходом (блоки `TOPIC:` / `IDS:`, чанками) и переиспользует
  `create_topic` для каждого кластера из двух и более чатов.
- **LLM** (`llm.py`): все парсеры при сбое деградируют в пустые значения; если
  Ollama недоступна, остальное приложение продолжает работать.

## GUI-мост (`ui.py`)

JavaScript вызывает `window.pywebview.api.*`; бэкенд работает в отдельном потоке
и отдаёт прогресс событиями.

- **Окна**: `main` (`web/app.html` + `app.js`), отдельное окно чата
  (`web/chat.html` + `chat.js`) и окно справки (`web/help.html`).
- **Вкладки**: `Obsidianize`, `Обработка` (Источник / Обнаружено /
  Enrichment / AI-настройки / Обзоры папок / Инструменты), плюс кнопка
  справки и отдельное окно AI-чата (собственный редактор chat-модели).
  Переключатель языка (RU/EN) и индикатор обновления — в статус-баре.
- **Диспетчеризация событий**: `_on_event` добавляет в `self.events` и вызывает
  `window.pushEvent(payload)`; события чата идут только в окно чата.
- **Персистентность**: `Settings.load()` / `Settings.save()` сливаются в
  `config.yml` атомарно. Файл лежит в `_user_data_dir()` — `%LOCALAPPDATA%\Obsidianizer`
  во frozen-сборке, корень репозитория при запуске из исходников. Флаги CLI
  имеют приоритет.
- **Индикатор обновления**: `check_update()` запрашивает список релизов GitHub
  (drafts пропускаются, pre-release учитываются) и зажигает точку при наличии
  более нового тега; автообновления нет.

## Контракт событий (`events.py`)

```python
Event(type: EventType, path: str = "", index: int = 0,
      total: int = 0, message: str = "", data: dict = {})
```

`message` — локализованная человекочитаемая деталь; `data` несёт
машиночитаемый payload, чтобы фронтенду не приходилось парсить текст.

| Семейство | События |
|-----------|---------|
| Батч (стадия 1) | `SCAN_STARTED`, `FILE_STARTED`, `LLM_STARTED`, `FILE_DONE`, `FILE_SKIPPED`, `FILE_ERROR`, `FINISHED` |
| AI-enrich (стадия 2) | `AI_SCAN_STARTED`, `AI_FILE_STARTED`, `AI_FILE_DONE`, `AI_FILE_SKIPPED`, `AI_FILE_ERROR`, `AI_FINISHED` |
| Сборка тем | `TOPIC_SCAN_STARTED`, `TOPIC_MAP_STARTED`, `TOPIC_FILE_STARTED`, `TOPIC_FILE_DONE`, `TOPIC_FILE_ERROR`, `TOPIC_FINISHED` |
| Жизненный цикл тем | `TOPIC_UPDATED`, `TOPIC_RENAMED`, `TOPIC_DELETED` |
| AI-чат | `CHAT_REPLY`, `CHAT_ERROR`, `CHAT_FOUND` |
| Folder Obsidianizer | `OBS_SCAN_STARTED`, `OBS_FOLDER_DONE`, `OBS_FINISHED`, `OBS_ERROR` |
| Обзор папки | `REVIEW_STARTED`, `REVIEW_FOLDER_DONE`, `REVIEW_FINISHED`, `REVIEW_ERROR` |

`<...>_FINISHED` эмитится ровно один раз на любом пути (нормальное завершение,
пофайловые ошибки, отмена, фатальная ошибка).

## Инварианты безопасности

- **Источник никогда не изменяется** ни одним инструментом.
- **Guard перекрытия путей** для стадий пайплайна (см. выше).
- **Удаления — только по владению**: манифест стадии 1 для `--prune`, маркер
  `ai_hash` для `--prune-enriched`, `type: topic` + `topic_id` для удаления
  темы. Чужие заметки не перезаписываются без `--force` / `--adopt`.
- **Атомарные записи** везде (`.tmp → os.replace`): заметки, карточки,
  манифест, конфиг, индекс, файлы тем.
- **Заметки — территория пользователя**: `<папка>_заметки.md` создаётся один
  раз и не перезаписывается (меняется лишь frontmatter при синхронизации
  комментариев).

## Обработка ошибок и отмена

- `update_cards` изолирует каждую папку: сбой пишется в лог с traceback,
  попадает в `summary.failed`, эмитит `OBS_FOLDER_DONE` с action `failed`, и
  прогон продолжается на остальных папках. CLI печатает `failed`; payload
  `OBS_FINISHED` его содержит.
- Пайплайн оборачивает каждый файл в per-file обработчик (`FILE_ERROR` /
  `AI_FILE_ERROR` / `TOPIC_FILE_ERROR`).
- `cancel_check` опрашивается **только между файлами** (никогда внутри файла
  или вызова LLM). При отмене владение уже записанными файлами сохраняется,
  `--prune` пропускается, `_index.md` не публикуется.

## Сборка и релиз

- **PyInstaller** onedir через `build-release.bat` (`Obsidianizer.spec` →
  `dist/Obsidianizer/`), web-ассеты включаются как datas, рядом с exe
  копируются `Obsidianizer.exe.config` и `integration/`.
- **Inno Setup** инсталлятор через `Obsidianizer.iss`
  (`release/Obsidianizer-Setup-<ver>.exe`).
- Портативный архив — `release/Obsidianizer-<ver>.zip` (содержимое
  `dist/Obsidianizer/`).

## Структура файлов

```
Obsidianizer.py / .bat / obsidianizer-cli.bat   точки входа
src/obsidianizer/                                пакет
  web/                                           HTML/JS/CSS для GUI
tests/                                           набор pytest
docs/                                            пользовательские и архитектурные доки
integration/                                     payload интеграции Templater/shell
obsidian/                                        заметки по ручной настройке
Obsidianizer.spec / .iss / build-release.bat     сборка и упаковка
config.example.yml                               документированные дефолты
```

## Тесты

Набор (`tests/`, `pytest`) в первую очередь покрывает критичные для
безопасности пути: guard, manifest/prune, инкрементальность пайплайна,
postprocess, golden-тесты карточек папок, round-trip комментариев, симметрию
i18n, установку интеграции и UI-мост в headless-режиме.

## Зависимости

- Python 3.10+
- `pyyaml>=6.0` — парсинг frontmatter/конфига
- `httpx>=0.27` — HTTP-клиент Ollama
- `pywebview>=5` (extra `ui`) — настольный GUI
