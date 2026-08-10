# Процессоры

> **Русский** | [English](processors.md)

Процессоры — единственный специфичный для типов файлов код в Obsidianizer.
Ядро конвейера (`scan → extract → enrich → emit`) никогда не содержит логики
типов файлов; оно работает только с интерфейсом `Processor`.

## Интерфейс (`base.py`)

```python
class Processor(ABC):
    extensions: frozenset[str]          # например frozenset({".md"})

    def parse(self, path: pathlib.Path, rel_path: str) -> dict:
        """Извлекает плоские метаданные из файла.

        Ключи возвращаемого словаря попадают в YAML-frontmatter.
        raise ValueError при кривом содержимом.
        """

    def body(self, path: pathlib.Path) -> str:
        """Возвращает оригинальное тело как есть (никогда не преобразованное)."""

    def media_refs(self, path: pathlib.Path) -> list[str]:
        """Локальные ссылки на медиа, используемые этим файлом.

        Пути относительны каталога заметки. Удалённые URL ничего не возвращают.
        """
```

## Реестр (`registry.py`)

Регистрация сопоставляет расширение файла с классом процессора:

```python
registry = ProcessorRegistry()
registry.register(".md", MdProcessor)
```

`registry.walk(source_root)` возвращает кандидатов — записи `SourceFile`,
которые конвейер будет обрабатывать. Всё незарегистрированное сканированием
игнорируется.

## Добавление нового типа файла (например, SVG)

1. Создайте `svg_processor.py`:

```python
class SvgProcessor(Processor):
    extensions = frozenset({".svg"})

    def parse(self, path, rel_path) -> dict:
        # width/height через чтение заголовка, title из <title> или имени файла
        return {"title": ..., "format": "svg"}

    def body(self, path) -> str:
        return path.read_text(encoding="utf-8")

    def media_refs(self, path) -> list[str]:
        return []   # или ссылки на растровые ассеты
```

2. Зарегистрируйте его однажды в CLI / корне композиции:

```python
registry.register(".svg", SvgProcessor)
```

3. Ядро, эмиттер, манифест, индекс и механизм prune работают без изменений.

## Правила для процессоров

- **Только чтение.** Никогда не изменяй `path`.
- **Детерминированность.** Один и тот же файл → одни и те же метаданные.
- **Сохраняй тело.** Обогащение добавляет *вокруг* содержимого, никогда не
  переписывает его.
- **Ошибка локально.** Кривой файл поднимает `ValueError`; конвейер сообщает
  о нём и продолжает с остальными.
- Возвращай ссылки на медиа *относительными* путями; эмиттер резолвит их
  относительно каталога заметки, затем корня источника.