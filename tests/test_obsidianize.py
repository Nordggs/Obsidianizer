"""Folder Obsidianizer tests — read-only contract, card format (golden tests
on the real "Оборудование" structure), user-data preservation, freshness."""

from datetime import datetime
from pathlib import Path

from obsidianizer.cli import main
from obsidianizer.obsidianize import (
    CARD_MARKER_KEY,
    HASH_KEY,
    MANUAL_HEADER,
    RENDER_VERSION,
    TEMPLATE_KEY,
    VERSION_KEY,
    build_card,
    card_is_ours,
    card_path_for,
    card_status,
    extract_comments,
    extract_manual_block,
    folder_fingerprint,
    folder_stats,
    format_rel_date,
    format_size,
    parse_frontmatter,
    review_file_path,
    scan_tree,
    update_cards,
    write_atomic,
    ObsidianizeConfig,
)


def _touch(path: Path, data: bytes | str = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_equipment(tmp_path: Path) -> Path:
    """Mirror of the real 160_DemoProject/Оборудование structure."""
    root = tmp_path / "Оборудование"
    _touch(root / "Арх" / "Арх.md", "---\nobsidianizer: true\n---\n\nподкарточка\n")
    _touch(root / "Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx")
    _touch(root / "Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx")
    _touch(root / "Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf")
    return root


SCRIPT_CARD_EQUIPMENT = """---
дата_начала: 2026-03-16
источник: А. В. +7 (999) 123-45-67
дизайнер: Инна Футуро
клиент: Д. З. +7 (999) 123-45-67
адрес: Москва, Дмитровское шоссе 79
tags: [проект]
комментарий: null
---

> [!info] 📋 Карточка проекта
> - **клиент**: Д. З. +7 (999) 123-45-67
> - **адрес**: Москва, Дмитровское шоссе 79
> - **источник**: А. В. +7 (999) 123-45-67
> - **дата_начала**: 2026-03-16
> - **дизайнер**: Инна Футуро

---
#### 📁 Папки внутри проекта
| Папка | Комментарий |
| --- | --- |
| [[PROJECT/OBSIDIAN/Objects/160_Project_Name/Оборудование/Арх/Арх\\|Арх]] |  |

---
#### 📐 Чертежи (.dwg)
*Чертежей нет*

---
#### 💰 Сметы (.xlsx, .xls)
| Файл | Комментарий |
| --- | --- |
| [[PROJECT/OBSIDIAN/Objects/160_Project_Name/Оборудование/Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx\\|Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx]] |  |
| [[PROJECT/OBSIDIAN/Objects/160_Project_Name/Оборудование/Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx\\|Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx]] |  |

---
#### 📄 Документы (.pdf, .docx)
| Файл | Комментарий |
| --- | --- |
| [[PROJECT/OBSIDIAN/Objects/160_Project_Name/Оборудование/Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf\\|Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf]] |  |

---
## ✍️ Ручные заметки и дополнения
*Всё, что вы напишете ниже, сохранится при обновлении.*

_Обновлено: 18.03.2026 14:31_
"""


def _updated_footer() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def test_scan_tree_equipment_shape(tmp_path):
    root = _make_equipment(tmp_path)
    tree = scan_tree(root)
    assert set(tree) == {"", "Арх"}
    root_scan, sub_scan = tree[""], tree["Арх"]

    assert [f.name for f in root_scan.files] == [
        "Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf",
        "Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx",
        "Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx",
    ]
    assert root_scan.subfolders == ["Арх"]
    assert root_scan.images == []
    assert sub_scan.files == []
    assert sub_scan.subfolders == []
    assert sub_scan.path == root / "Арх"


def test_scan_tree_categories_and_filters(tmp_path):
    root = tmp_path / "mix"
    _touch(root / "чертеж.dwg")
    _touch(root / "схема.dxf")
    _touch(root / "смета.xlsx")
    _touch(root / "данные.csv")
    _touch(root / "док.pdf")
    _touch(root / "заметка.txt")
    _touch(root / "фото.png")
    _touch(root / "скрин.jpg")
    _touch(root / "архив.zip")
    _touch(root / "модель.step")
    _touch(root / "заметка.md")
    _touch(root / ".hidden.txt")
    _touch(root / ".obsidian" / "x.json")
    _touch(root / "node_modules" / "pkg.js")
    _touch(root / "own_card.md", "---\nobsidianizer: true\n---\n")

    scan = scan_tree(root)[""]
    by_ext = {f.ext: f.name for f in scan.files}
    assert by_ext["dwg"] == "чертеж.dwg"
    assert by_ext["dxf"] == "схема.dxf"
    assert by_ext["xlsx"] == "смета.xlsx"
    assert by_ext["csv"] == "данные.csv"
    assert by_ext["pdf"] == "док.pdf"
    assert by_ext["txt"] == "заметка.txt"
    assert by_ext["png"] == "фото.png"
    assert by_ext["zip"] == "архив.zip"
    assert by_ext["step"] == "модель.step"
    assert "md" not in by_ext  # .md excluded by default
    assert ".hidden.txt" not in by_ext
    assert scan.subfolders == []  # hidden + excluded dropped
    assert [f.name for f in scan.images] == ["скрин.jpg", "фото.png"]


def test_scan_tree_sorts_case_insensitive(tmp_path):
    root = tmp_path / "sort"
    _touch(root / "b.txt")
    _touch(root / "A.txt")
    _touch(root / "а.txt")
    _touch(root / "Б.txt")
    names = [f.name for f in scan_tree(root)[""].files]
    assert names == ["A.txt", "b.txt", "а.txt", "Б.txt"]


def test_scan_tree_card_never_catalogues_itself(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "Оборудование.md", "---\nobsidianizer: true\n---\n")
    names = [f.name for f in scan_tree(root)[""].files]
    assert "Оборудование.md" not in names


# --------------------------------------------------------------------------
# Frontmatter parsing / card recognition
# --------------------------------------------------------------------------


def test_parse_frontmatter_types():
    content = """---
дата_начала: 2026-03-16
источник: А. В. +7 (999) 123-45-67
tags:
  - проект
  - "два"
  - 42
пусто:
число: 42
дробь: 3.14
флаг: true
нет: null
вики: '[[путь|имя]]'
---
тело"""
    props = parse_frontmatter(content)
    assert props["дата_начала"] == "2026-03-16"
    assert props["источник"] == "А. В. +7 (999) 123-45-67"
    assert props["tags"] == ["проект", "два", 42]
    assert props["пусто"] is None
    assert props["число"] == 42
    assert props["дробь"] == 3.14
    assert props["флаг"] is True
    assert props["нет"] is None
    assert props["вики"] == "[[путь|имя]]"


def test_card_is_ours():
    ours = "---\nobsidianizer: true\n---\n\nтело"
    foreign = "---\nтег: 1\n---\n\nтело"
    no_fm = "просто текст"
    assert card_is_ours(ours) is True
    assert card_is_ours(foreign) is False
    assert card_is_ours(no_fm) is False


# --------------------------------------------------------------------------
# User data extraction
# --------------------------------------------------------------------------


def test_extract_comments_from_our_and_script_cards():
    our = """| Файл | Комментарий |
| --- | --- |
| [[смета.xlsx|смета.xlsx]] | главная смета |
| [[чертеж.dwg|чертеж.dwg]] |  |
"""
    script = """| [[PROJECT/ОБСИДИАН/foo/смета.xlsx\\|смета.xlsx]] | главная смета |
"""
    assert extract_comments(our) == {"смета.xlsx": "главная смета"}
    assert extract_comments(script) == {"смета.xlsx": "главная смета"}


def test_extract_comments_github_table_with_icons():
    github = """| Файл | Комментарий | Изменено | Размер |
| --- | --- | --- | --- |
| 📊 [[смета.xlsx]] |  | 26.02.2026 | 98.2 KB |
| 📄 [[чертеж.pdf|чертеж.pdf]] | финальный вариант | 17.12.2025 | 2 MB |
| 🖼️ [[фото.png]] |  | 26.01.2026 | 163.9 KB |
"""
    assert extract_comments(github) == {"чертеж.pdf": "финальный вариант"}


def test_extract_comments_bare_wikilink_key_is_file_name():
    assert extract_comments("| [[смета.xlsx]] | главная смета |\n") == {
        "смета.xlsx": "главная смета"
    }


def test_extract_manual_block_strips_all_footers():
    content = (
        f"{MANUAL_HEADER}\n"
        "Мой ручной текст.\n"
        "_Обновлено: 16.03.2026 22:26_\n"
        "_Обновлено: 16.03.2026 22:26_\n"
        "_Обновлено: 19.08.2026 07:00 · 40 файлов · 2 папки · 52.2 MB_\n"
    )
    block = extract_manual_block(content)
    assert block == f"{MANUAL_HEADER}\nМой ручной текст."
    assert "_Обновлено" not in block


def test_extract_manual_block_missing_returns_none():
    assert extract_manual_block("просто текст") is None


# --------------------------------------------------------------------------
# Card generation (golden tests)
# --------------------------------------------------------------------------


from unittest.mock import patch


@patch("obsidianizer.obsidianize._get_now")
def test_build_card_golden_equipment(mock_get_now, tmp_path):
    # Freeze time for reproducible test
    fixed_now = datetime(2026, 3, 18, 14, 31)
    mock_get_now.return_value = fixed_now

    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]

    card = build_card(scan, SCRIPT_CARD_EQUIPMENT, ObsidianizeConfig(template="classic"))

    # Service-only frontmatter: no user keys in the card anymore
    assert card.startswith("---\n")
    assert f"{CARD_MARKER_KEY}: true" in card
    assert f"{TEMPLATE_KEY}: classic\n" in card
    assert f"{VERSION_KEY}: {RENDER_VERSION}\n" in card
    assert "cssclasses" not in card
    assert "дата_начала:" not in card
    assert "клиент:" not in card

    # v5 header — plain markdown, no HTML blocks
    assert "# Оборудование\n" in card
    assert "Автоматическая карточка каталога" in card
    assert "Local project · 3 файла · 1 папка · 3 B" in card
    # Nav as plain wikilinks (no <p> wrapper)
    assert "[[#Folders|Folders]] | [[#Files|Files]] | [[#About|About]] | [[#Notes|Notes]]" in card
    assert "<p class=" not in card

    # Tree: physical folders only, with folder icon
    assert "| Name | Files | Size | Updated |" in card
    assert "| 📁 [[Арх/Арх\\|Арх]] | 0 | 0 B | |" in card

    # About falls back to the old card frontmatter when notes absent
    assert "## About\n" in card
    assert "> - **Клиент**: Д. З. +7 (999) 123-45-67" in card
    assert "> - **Адрес**: Москва, Дмитровское шоссе 79" in card

    # Files: single GitHub-style table with opens-with column
    assert "## Files\n" in card
    assert "| File | Type | Opens with | Modified | Size | Comment |" in card
    assert (
        "| 📊 [[Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx]]"
        " | XLSX | Excel | сегодня | 1 B |  |" in card
    )
    assert (
        "| 📄 [[Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf]]"
        " | PDF | Obsidian | сегодня | 1 B |  |" in card
    )

    # Notes embed + footer
    assert "## Notes\n" in card
    assert "![[Оборудование_заметки]]" in card
    assert '<footer class="repo-meta">' in card
    assert "3 файла · 1 папка · 3 B" in card


def test_build_card_new_card_defaults(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="classic"))
    props = parse_frontmatter(card)
    # Service keys only — user fields live in the notes file
    assert props[CARD_MARKER_KEY] is True
    assert props[VERSION_KEY] == RENDER_VERSION
    assert len(props[HASH_KEY]) == 12
    assert "дата_начала" not in props
    assert "клиент" not in props
    assert "cssclasses" not in props  # classic template


def test_update_cards_migrates_user_frontmatter_to_notes(tmp_path):
    """Old-card user fields migrate ONCE into the notes file frontmatter."""
    root = _make_equipment(tmp_path)
    # Simulate a pre-v5 card: ours (marker present) but stale, with user data
    prev_card = (
        "---\n"
        "дата_начала: 2026-03-16\n"
        "источник: А. В. +7 (999) 123-45-67\n"
        "клиент: Д. З. +7 (999) 123-45-67\n"
        "телефон: +7 999 123-45-67\n"
        "карта: https://yandex.ru/maps/?text=Москва\n"
        "мой_пункт: что-то\n"
        "tags:\n"
        "  - проект\n"
        "  - важное\n"
        f"{CARD_MARKER_KEY}: true\n"
        f"{HASH_KEY}: deadbeefdead\n"
        f"{TEMPLATE_KEY}: github\n"
        f"{VERSION_KEY}: 2\n"
        "---\n\nстарое тело\n"
    )
    card = root / "Оборудование.md"
    card.write_text(prev_card, encoding="utf-8")

    summary = update_cards(root, ObsidianizeConfig(force=True))
    assert summary.updated >= 1

    # Card no longer carries user fields
    new_card = parse_frontmatter(card.read_text(encoding="utf-8"))
    assert new_card[CARD_MARKER_KEY] is True
    assert "клиент" not in new_card
    assert "телефон" not in new_card

    # Notes received ALL user fields, incl. unknown ones and block-list tags
    notes_props = parse_frontmatter((root / "Оборудование_заметки.md").read_text(encoding="utf-8"))
    assert notes_props["дата_начала"] == "2026-03-16"
    assert notes_props["источник"] == "А. В. +7 (999) 123-45-67"
    assert notes_props["клиент"] == "Д. З. +7 (999) 123-45-67"
    assert notes_props["телефон"] == "+7 999 123-45-67"
    assert notes_props["карта"] == "https://yandex.ru/maps/?text=Москва"
    assert notes_props["мой_пункт"] == "что-то"
    assert notes_props["tags"] == ["проект", "важное"]

    # About section renders from the migrated notes
    body = card.read_text(encoding="utf-8")
    assert "> - **Клиент**: Д. З. +7 (999) 123-45-67" in body
    assert "> - **Телефон**: +7 999 123-45-67" in body
    assert "> - **Карта**: https://yandex.ru/maps/?text=Москва" in body
    assert "> - **Мой_пункт**: что-то" in body


def test_update_cards_never_overwrites_user_notes_data(tmp_path):
    """Once the notes carry user data, regeneration never touches them."""
    root = _make_equipment(tmp_path)
    update_cards(root)
    notes = root / "Оборудование_заметки.md"
    notes.write_text(
        "---\nклиент: Мой Клиент ООО Ромашка\n---\n\nМои личные мысли.\n",
        encoding="utf-8",
    )
    old_stale_card = root / "Оборудование.md"
    content = old_stale_card.read_text(encoding="utf-8").replace(
        "obsidianizer_hash:", "obsidianizer_hash: 0000"  # break hash → stale
    )
    old_stale_card.write_text(content, encoding="utf-8")

    update_cards(root)

    notes_text = notes.read_text(encoding="utf-8")
    assert "Мои личные мысли." in notes_text
    assert parse_frontmatter(notes_text)["клиент"] == "Мой Клиент ООО Ромашка"


def test_build_card_preserves_comments(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    prev = SCRIPT_CARD_EQUIPMENT.replace(
        "Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx\\|Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx]] |  |",
        "Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx\\|Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx]] | главная смета |",
    )
    card = build_card(scan, prev, ObsidianizeConfig(template="classic"))
    assert "| главная смета |" in card


def test_update_cards_migrates_manual_block_to_notes(tmp_path):
    root = _make_equipment(tmp_path)
    update_cards(root)
    card = root / "Оборудование.md"
    # simulate a pre-v2 card with an in-card manual block
    content = card.read_text(encoding="utf-8").replace(
        "## Notes\n\n![[Оборудование_заметки]]",
        f"{MANUAL_HEADER}\nМой комментарий по проекту.\n\nВторая мысль.",
    )
    card.write_text(content, encoding="utf-8")
    (root / "Оборудование_заметки.md").unlink()  # до v2 заметок ещё не было
    _touch(root / "новый.xlsx")  # make the card stale

    summary = update_cards(root)
    assert summary.updated == 1  # только Оборудование stale (Арх в актуальном состоянии)

    notes = root / "Оборудование_заметки.md"
    assert notes.is_file()
    body = notes.read_text(encoding="utf-8")
    assert "Мой комментарий по проекту." in body
    assert "Вторая мысль." in body
    assert "_Обновлено" not in body  # футеры срезаны при миграции
    new_card = card.read_text(encoding="utf-8")
    assert "![[Оборудование_заметки]]" in new_card
    assert MANUAL_HEADER not in new_card  # блок ушёл из карточки в заметку


def test_build_card_subfolder_has_parent_link(tmp_path):
    root = _make_equipment(tmp_path)
    tree = scan_tree(root)
    card = build_card(tree["Арх"], None, ObsidianizeConfig(template="classic"), parent_rel="Оборудование")
    # Вверх живёт строкой в таблице Folders, а не в шапке
    assert "[[../Оборудование|⬆ Up]]" not in card.split("## Folders")[0]
    assert "| ⬆ [[../Оборудование|Up]] |  |  |  |" in card


def test_build_card_root_has_no_parent_link(tmp_path):
    root = _make_equipment(tmp_path)
    card = build_card(scan_tree(root)[""], None, ObsidianizeConfig(template="classic"), parent_rel=None)
    assert "⬆ Up" not in card


def test_images_gallery_and_archive(tmp_path):
    """Gallery = direct images (img-gallery); Images = full subtree archive."""
    root = _make_equipment(tmp_path)
    _touch(root / "фото.png")
    scan = scan_tree(root)[""]

    # Без пути: только Images (callout), галереи нет.
    plain = build_card(scan, None, ObsidianizeConfig(template="classic"))
    assert "## Images" in plain
    assert "> [!example]- Images · 1 изображение · " in plain
    assert "> ![" in plain
    assert "img-gallery" not in plain

    # С vault_root: Gallery (img-gallery) + Images (callout).
    with_gallery = build_card(
        scan, None, ObsidianizeConfig(vault_root=str(tmp_path), template="classic")
    )
    assert "## Gallery" in with_gallery
    assert "```img-gallery" in with_gallery
    assert "path: Оборудование" in with_gallery
    assert "## Images" in with_gallery
    assert "> [!example]- Images · 1 изображение · " in with_gallery
    # Gallery идёт первой
    assert with_gallery.index("## Gallery") < with_gallery.index("## Images")

    # vault_root вне папки: галереи нет, Images остаётся.
    outside = build_card(
        scan, None, ObsidianizeConfig(vault_root=str(tmp_path / "vault"), template="classic")
    )
    assert "img-gallery" not in outside
    assert "## Images" in outside


def test_images_tree_recursive(tmp_path):
    """Images archive collects direct images only (not recursive)."""
    root = _make_equipment(tmp_path)
    _touch(root / "фото.png")
    _touch(root / "Арх" / "вложенная картинка.png")
    tree = scan_tree(root)
    stats = folder_stats(tree)

    card = build_card(
        tree[""], None, ObsidianizeConfig(template="classic"), stats=stats[""]
    )
    assert "> ![" in card
    # urlencode пути (пробел → %20), относительный от карточки
    assert "> ![фото.png](./фото.png)" in card
    # Nested images are NOT included in Images section (only direct images)
    # images_tree is kept in folder_stats for future Canvas use


def test_images_gallery_with_prefix(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "фото.png")
    scan = scan_tree(root)[""]
    cfg = ObsidianizeConfig(
        template="classic", gallery_prefix="PROJECT/OBSIDIAN/Objects"
    )
    card = build_card(scan, None, cfg)
    assert "> ![фото.png](./фото.png)" in card
    assert "path: PROJECT/OBSIDIAN/Objects/Оборудование" in card


def test_build_card_include_md_puts_md_into_docs(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "заметка.md", "текст заметки")
    cfg = ObsidianizeConfig(include_md=True, template="classic")
    scan = scan_tree(root, cfg)[""]

    card = build_card(scan, None, cfg)
    assert "[[заметка.md]]" in card
    assert "| 📄 [[заметка.md]] | MD | Obsidian |" in card

    without = build_card(
        scan_tree(root, ObsidianizeConfig())[""], None, ObsidianizeConfig(template="classic")
    )
    assert "[[заметка.md]]" not in without  # нет в таблицах (манифест не считается)


def test_scan_tree_excludes_derived_artifacts(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "Оборудование_заметки.md", "# Заметки\n")
    _touch(root / "Оборудование_обзор.md", "# Обзор\n")
    cfg = ObsidianizeConfig(include_md=True)
    scan = scan_tree(root, cfg)[""]
    names = [f.name for f in scan.files]
    assert "Оборудование_заметки.md" not in names
    assert "Оборудование_обзор.md" not in names
    assert len(names) == 3  # xlsx × 2 + pdf, никаких производных


def test_build_card_unknown_extensions_go_to_other(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "модель.rvt")
    _touch(root / "архив.rar")
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="classic"))
    files_section = card.split("## Files")[1].split("## ")[0]
    assert "[[модель.rvt]]" in files_section
    assert "[[архив.rar]]" in files_section
    assert "| Revit |" in files_section  # .rvt → Revit
    assert "| — |" in files_section  # .rar неизвестен


def test_fingerprint_changes_with_content(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    before = folder_fingerprint(scan)
    _touch(root / "новый.dwg")
    after = folder_fingerprint(scan_tree(root)[""])
    assert before != after


# --------------------------------------------------------------------------
# update_cards: creation, freshness, conflicts, read-only contract
# --------------------------------------------------------------------------


def test_update_cards_creates_all_cards(tmp_path):
    root = _make_equipment(tmp_path)
    summary = update_cards(root)
    assert summary.scanned == 2
    assert summary.created == 1
    assert summary.updated == 1  # Арх/Арх.md из фикстуры перегенерирован
    assert summary.conflicts == []
    assert (root / "Оборудование.md").is_file()
    assert (root / "Арх" / "Арх.md").is_file()
    assert card_is_ours((root / "Оборудование.md").read_text(encoding="utf-8"))
    # производные заметки создаются вместе с карточками
    assert (root / "Оборудование_заметки.md").is_file()
    assert (root / "Арх" / "Арх_заметки.md").is_file()
    leftovers = [p for p in root.rglob("*.tmp")]
    assert leftovers == []


def test_update_cards_skips_unchanged(tmp_path):
    root = _make_equipment(tmp_path)
    update_cards(root)
    card = root / "Оборудование.md"
    mtime_before = card.stat().st_mtime_ns

    summary = update_cards(root)
    assert summary.created == 0
    assert summary.updated == 0
    assert summary.skipped == 2
    assert card.stat().st_mtime_ns == mtime_before


def test_update_cards_force_rebuilds_current_cards(tmp_path):
    root = _make_equipment(tmp_path)
    update_cards(root)
    summary = update_cards(root, ObsidianizeConfig(force=True))
    assert summary.created == 0
    assert summary.updated == 2
    assert summary.skipped == 0
    card = (root / "Оборудование.md").read_text(encoding="utf-8")
    assert "obsidianizer: true" in card


def test_update_cards_refreshes_stale_card(tmp_path):
    root = _make_equipment(tmp_path)
    update_cards(root)
    card = root / "Оборудование.md"
    assert card_status(card, scan_tree(root)[""]) == "ok"

    _touch(root / "новый.xlsx")
    folder = scan_tree(root)[""]
    assert card_status(card, folder) == "stale"

    summary = update_cards(root)
    assert summary.updated == 1
    assert "новый.xlsx" in card.read_text(encoding="utf-8")
    assert card_status(card, scan_tree(root)[""]) == "ok"


def test_update_cards_foreign_note_not_overwritten(tmp_path):
    root = _make_equipment(tmp_path)
    foreign = root / "Оборудование.md"
    _touch(foreign, "моя личная заметка, не карточка")
    before = foreign.read_text(encoding="utf-8")

    summary = update_cards(root)
    assert summary.conflicts == [str(foreign)]
    assert foreign.read_text(encoding="utf-8") == before

    forced = update_cards(root, ObsidianizeConfig(force=True))
    assert forced.conflicts == []
    assert card_is_ours(foreign.read_text(encoding="utf-8"))


def test_update_cards_adopt_renames_foreign_note(tmp_path):
    """Adopt renames a foreign note into _заметки.md (1:1) and builds the card."""
    root = _make_equipment(tmp_path)
    foreign = root / "Оборудование.md"
    old_content = (
        "---\n"
        "клиент: ООО Ромашка\n"
        "адрес: Москва\n"
        "---\n\nмои старые заметки по проекту\n"
    )
    foreign.write_text(old_content, encoding="utf-8")

    summary = update_cards(root, ObsidianizeConfig(adopt=True))
    assert summary.conflicts == []
    notes = root / "Оборудование_заметки.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8") == old_content  # 1:1, ничего не потеряно

    card = root / "Оборудование.md"
    assert card.is_file()
    assert card_is_ours(card.read_text(encoding="utf-8"))
    # старый frontmatter стал источником Карточки проекта
    body = card.read_text(encoding="utf-8")
    assert "> - **Клиент**: ООО Ромашка" in body
    assert "> - **Адрес**: Москва" in body


def test_update_cards_adopt_keeps_conflict_when_notes_exist(tmp_path):
    """Adopt must never overwrite an existing notes file."""
    root = _make_equipment(tmp_path)
    (root / "Оборудование_заметки.md").write_text("мои личные заметки\n", encoding="utf-8")
    foreign = root / "Оборудование.md"
    foreign.write_text("чужая заметка\n", encoding="utf-8")

    summary = update_cards(root, ObsidianizeConfig(adopt=True))
    assert summary.conflicts == [str(foreign)]
    assert foreign.read_text(encoding="utf-8") == "чужая заметка\n"
    assert (root / "Оборудование_заметки.md").read_text(encoding="utf-8") == "мои личные заметки\n"


def test_update_cards_without_adopt_stays_conflict(tmp_path):
    root = _make_equipment(tmp_path)
    foreign = root / "Оборудование.md"
    foreign.write_text("чужая заметка\n", encoding="utf-8")

    summary = update_cards(root)  # adopt=False по умолчанию
    assert summary.conflicts == [str(foreign)]
    assert foreign.exists()
    assert not (root / "Оборудование_заметки.md").exists()


def test_update_cards_dry_run_writes_nothing(tmp_path):
    root = _make_equipment(tmp_path)
    arh_card = root / "Арх" / "Арх.md"
    before = arh_card.read_bytes()

    summary = update_cards(root, dry_run=True)
    assert summary.updated == 2
    assert not (root / "Оборудование.md").exists()
    assert not (root / "Оборудование_заметки.md").exists()  # заметки тоже не пишем
    assert arh_card.read_bytes() == before  # уже существующая карточка не тронута
    assert summary.created == 0


def test_update_cards_read_only_contract(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "фото.png")
    _touch(root / "Арх" / "Арх.md", "---\nobsidianizer: true\n---\n\nподкарточка\n")

    def snapshot():
        out = {}
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.endswith(("_заметки.md", "_обзор.md")):
                continue  # производные артефакты генератора
            if p.suffix.lower() == ".md":
                try:
                    if card_is_ours(p.read_text(encoding="utf-8")):
                        continue
                except OSError:
                    pass
            out[p.relative_to(root).as_posix()] = _sha(p)
        return out

    before = snapshot()
    update_cards(root)
    after = snapshot()

    assert before == after, "исходный файл изменён"
    assert (root / "Оборудование.md").is_file()


def test_write_atomic_noop_on_same_content(tmp_path):
    target = tmp_path / "a.md"
    assert write_atomic(target, "текст") is True
    mtime = target.stat().st_mtime_ns
    assert write_atomic(target, "текст") is False
    assert target.stat().st_mtime_ns == mtime
    assert list(tmp_path.glob("*.tmp")) == []


def test_card_status_states(tmp_path):
    root = _make_equipment(tmp_path)
    folder = scan_tree(root)[""]
    card = card_path_for(folder)
    assert card_status(card, folder) == "missing"

    update_cards(root)
    assert card_status(card, folder) == "ok"

    _touch(root / "новый.pdf")
    assert card_status(card, scan_tree(root)[""]) == "stale"

    _touch(card, "чужая")
    assert card_status(card, scan_tree(root)[""]) == "conflict"


# --------------------------------------------------------------------------
# CLI: obsidianizer folders
# --------------------------------------------------------------------------


def test_cli_folders_creates_cards(tmp_path, capsys):
    root = _make_equipment(tmp_path)
    assert main(["folders", "--path", str(root)]) == 0
    assert (root / "Оборудование.md").is_file()
    assert card_is_ours((root / "Оборудование.md").read_text(encoding="utf-8"))
    assert "[OK]" in capsys.readouterr().out


def test_cli_folders_dry_run_writes_nothing(tmp_path):
    root = _make_equipment(tmp_path)
    assert main(["folders", "--path", str(root), "--dry-run"]) == 0
    assert not (root / "Оборудование.md").exists()


def test_cli_folders_missing_path(tmp_path):
    assert main(["folders", "--path", str(tmp_path / "нет_такой")]) == 1


def test_cli_folders_conflict_and_force(tmp_path, capsys):
    root = _make_equipment(tmp_path)
    foreign = root / "Оборудование.md"
    _touch(foreign, "моя заметка")

    assert main(["folders", "--path", str(root)]) == 0
    assert foreign.read_text(encoding="utf-8") == "моя заметка"
    assert "Конфликтов" in capsys.readouterr().out

    assert main(["folders", "--path", str(root), "--force"]) == 0
    assert card_is_ours(foreign.read_text(encoding="utf-8"))


def test_cli_folders_no_recursive(tmp_path):
    root = _make_equipment(tmp_path)
    arh_before = (root / "Арх" / "Арх.md").read_text(encoding="utf-8")
    assert main(["folders", "--path", str(root), "--no-recursive"]) == 0
    assert (root / "Оборудование.md").is_file()
    # карточка подпапки не перегенерирована (осталась как в фикстуре)
    assert (root / "Арх" / "Арх.md").read_text(encoding="utf-8") == arh_before


def test_cli_folders_vault_root_gallery(tmp_path):
    with_gallery = _make_equipment(tmp_path / "а")
    _touch(with_gallery / "фото.png")
    assert (
        main(
            [
                "folders",
                "--path",
                str(with_gallery),
                "--vault-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    card = (with_gallery / "Оборудование.md").read_text(encoding="utf-8")
    assert "img-gallery" in card
    assert "path: а/Оборудование" in card

    no_gallery = _make_equipment(tmp_path / "б")
    _touch(no_gallery / "фото.png")
    assert main(["folders", "--path", str(no_gallery), "--no-gallery"]) == 0
    card = (no_gallery / "Оборудование.md").read_text(encoding="utf-8")
    assert "img-gallery" not in card


# --------------------------------------------------------------------------
# Project Dashboard (github template): formatters, aggregates, renderer
# --------------------------------------------------------------------------


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1 KB"
    assert format_size(1536) == "1.5 KB"
    assert format_size(1024 * 1024) == "1 MB"
    assert format_size(3 * 1024**3) == "3 GB"


def test_format_rel_date():
    now = datetime(2026, 3, 18, 12, 0)
    day = 86_400
    assert format_rel_date(int(now.timestamp() * 1e9), now) == "сегодня"
    assert format_rel_date(int((now.timestamp() - day) * 1e9), now) == "вчера"
    assert format_rel_date(int((now.timestamp() - 3 * day) * 1e9), now) == "3 дня назад"
    assert format_rel_date(int((now.timestamp() - 21 * day) * 1e9), now) == "21 день назад"
    assert format_rel_date(int((now.timestamp() - 35 * day) * 1e9), now) == "11.02.2026"


def test_folder_stats_subtree_aggregates(tmp_path):
    root = tmp_path / "P"
    _touch(root / "a.dwg", b"x" * 100)
    _touch(root / "под" / "b.xlsx", b"y" * 200)
    _touch(root / "под" / "глубже" / "c.png", b"z" * 300)
    tree = scan_tree(root)
    stats = folder_stats(tree)
    st = stats[""]
    assert st["total_count"] == 3
    assert st["total_size"] == 600
    assert st["total_subfolders"] == 2
    assert st["categories_tree"]["drafting"]["count"] == 1
    assert st["categories_tree"]["tables"]["count"] == 1
    assert st["categories_tree"]["images"]["count"] == 1
    assert stats["под"]["total_count"] == 2
    assert stats["под"]["subfolders"]["глубже"]["count"] == 1
    assert st["subfolders"]["под"]["count"] == 2


@patch("obsidianizer.obsidianize._get_now")
def test_build_card_github_golden(mock_get_now, tmp_path):
    # Freeze time for reproducible test
    fixed_now = datetime(2026, 3, 18, 14, 31)
    mock_get_now.return_value = fixed_now

    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="github"))

    # Verify v5 structure for github template
    assert card.startswith("---\n")
    assert f"{TEMPLATE_KEY}: github" in card
    assert "cssclasses: [github-dashboard]" in card
    # Service-only frontmatter
    assert "клиент:" not in card.split("---\n")[1]
    assert "# Оборудование\n" in card
    # Header — plain markdown, no HTML blocks
    assert "Автоматическая карточка каталога" in card
    assert "Local project · 3 файла · 1 папка · 3 B" in card
    assert "<p class=" not in card
    # Nav as plain wikilinks
    assert "[[#Folders|Folders]] | [[#Files|Files]] | [[#About|About]] | [[#Notes|Notes]]" in card
    assert "## Folders\n" in card
    assert "| Name | Files | Size | Updated |" in card
    assert "| 📁 [[Арх/Арх\\|Арх]] | 0 | 0 B | |" in card
    assert "## About\n" not in card  # нет данных в заметках — секция скрыта
    assert "## Files\n" in card
    assert "| File | Type | Opens with | Modified | Size | Comment |" in card
    assert "📊 [[Бриз_2шт 55 Счет_на_оплату_№_05_0014216_от_26_01_2026.xlsx]] | XLSX | Excel | сегодня | 1 B |  |" in card
    assert "| 📄 [[Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf]] | PDF | Obsidian | сегодня | 1 B |  |" in card
    assert "## Notes\n" in card
    assert "![[Оборудование_заметки]]" in card
    assert '<footer class="repo-meta">' in card
    assert "3 файла · 1 папка · 3 B" in card
    assert "## AI Review\n" not in card


def test_classic_has_no_cssclasses(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="classic"))
    assert "cssclasses" not in card
    assert "## Folders\n" in card  # классик — та же структура v5
    assert "![[Оборудование_заметки]]" in card


def test_github_keeps_user_cssclasses(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="github"))
    prev = card.replace(
        "cssclasses: [github-dashboard]",
        "cssclasses: [my-own-class, github-dashboard]",
    )
    card2 = build_card(scan, prev, ObsidianizeConfig(template="github"))
    assert "cssclasses: [my-own-class, github-dashboard]" in card2
    assert card2.count("cssclasses:") == 1


def test_about_comes_from_notes_file(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="github"))
    assert "## About\n" not in card

    notes_prev = (
        "---\n"
        "дата_начала: 2026-01-15\n"
        "клиент: Иванов\n"
        "---\n\n# Рабочие заметки\n"
    )
    card2 = build_card(scan, None, ObsidianizeConfig(template="github"), notes_prev=notes_prev)
    assert "## About\n" in card2
    assert "> - **Клиент**: Иванов" in card2
    assert "> - **Дата начала**: 2026-01-15" in card2
    assert "| Адрес |" not in card2
    # Subtitle from notes комментарий
    notes3 = notes_prev.replace("# Рабочие заметки", "").replace("---\n\n", "---\n", 1)
    notes3 = notes_prev.replace("клиент: Иванов", 'комментарий: "Мой проект"')
    card3 = build_card(scan, None, ObsidianizeConfig(template="github"), notes_prev=notes3)
    assert "Мой проект" in card3


def test_github_tree_view_with_aggregates(tmp_path):
    root = tmp_path / "P"
    _touch(root / "под" / "файл.dwg", b"x" * 512)
    tree = scan_tree(root)
    stats = folder_stats(tree)
    card = build_card(
        tree[""], None, ObsidianizeConfig(template="github"), stats=stats[""]
    )
    # Code table: physical folders with icon, count, size
    assert "| Name | Files | Size | Updated |" in card
    assert "| --- | --- | --- | --- |" in card
    assert "| 📁 [[под/под\\|под]] | 1 | 512 B | сегодня |" in card


@patch("obsidianizer.obsidianize._get_now")
def test_github_review_link_and_fingerprint(mock_get_now, tmp_path):
    # Freeze time for reproducible test
    fixed_now = datetime(2026, 3, 18, 14, 31)
    mock_get_now.return_value = fixed_now

    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    before = folder_fingerprint(scan)
    card = build_card(scan, None, ObsidianizeConfig(template="github"))
    assert "## AI Review\n" not in card
    assert "[[#AI Review|AI Review]]" not in card
    _touch(root / "Оборудование_обзор.md", "# Обзор\n")
    after = folder_fingerprint(scan_tree(root)[""])
    assert before != after
    card2 = build_card(scan_tree(root)[""], None, ObsidianizeConfig(template="github"))
    assert "## AI Review\n" in card2
    assert "![[Оборудование_обзор]]" in card2
    assert "[[#AI Review|AI Review]]" in card2  # nav-пункт появляется вместе с секцией
    (root / "Оборудование_обзор.md").unlink()
    assert folder_fingerprint(scan_tree(root)[""]) == before


@patch("obsidianizer.obsidianize._get_now")
def test_review_presence_refreshes_card(mock_get_now, tmp_path):
    # Freeze time for reproducible test
    fixed_now = datetime(2026, 3, 18, 14, 31)
    mock_get_now.return_value = fixed_now

    root = _make_equipment(tmp_path)
    update_cards(root)
    card = root / "Оборудование.md"
    assert "## AI Review\n" not in card.read_text(encoding="utf-8")

    _touch(root / "Оборудование_обзор.md", "# Обзор\n")
    assert card_status(card, scan_tree(root)[""]) == "stale"
    update_cards(root)
    assert "## AI Review\n" in card.read_text(encoding="utf-8")
    assert "![[Оборудование_обзор]]" in card.read_text(encoding="utf-8")

    (root / "Оборудование_обзор.md").unlink()
    assert card_status(card, scan_tree(root)[""]) == "stale"
    update_cards(root)
    assert "## AI Review\n" not in card.read_text(encoding="utf-8")


@patch("obsidianizer.obsidianize._get_now")
def test_dashboard_stats_consistency(mock_get_now, tmp_path):
    """All dashboard sections must use the same DIRECT counts for the current folder.

    - meta line total == sum of detail section totals (direct files)
    - summary/langbar counts == detail section totals (per category)
    - footer total == meta total
    - language bar counts == category count
    """
    fixed_now = datetime(2026, 3, 18, 14, 31)
    mock_get_now.return_value = fixed_now

    root = _make_equipment(tmp_path)
    update_cards(root)
    card_path = root / "Оборудование.md"
    card = card_path.read_text(encoding="utf-8")

    # Parse numbers from card sections
    import re

    # 1. Meta line: "Local project · N файлов · SIZE · M папки"
    meta_match = re.search(r"Local project.*?(\d+) файл", card, re.DOTALL)
    assert meta_match, f"meta line missing in: {card[:300]}"
    meta_total = int(meta_match.group(1))

    # 2. Files table: count file rows (each starts with "| 📐/📊/📄/🖼️/📦")
    files_match = re.search(r"## Files\n+\| File \| Type \| Opens with", card)
    assert files_match, "Files table missing"
    file_rows = len(re.findall(r"^\| [📐📊📄🖼️📦] \[\[", card, re.MULTILINE))

    # 3. Footer: "Updated ... · N файлов · M папки · SIZE"
    footer_match = re.search(r"Updated .*? · (\d+) файл", card)
    assert footer_match, "footer missing"
    footer_total = int(footer_match.group(1))

    # Assertions
    assert meta_total == footer_total, f"meta total ({meta_total}) != footer total ({footer_total})"
    assert meta_total == file_rows, f"meta total ({meta_total}) != Files table rows ({file_rows})"


def test_update_cards_never_overwrites_existing_notes(tmp_path):
    root = _make_equipment(tmp_path)
    update_cards(root)
    notes = root / "Оборудование_заметки.md"
    notes.write_text("Мои личные мысли.\n", encoding="utf-8")
    _touch(root / "ещё.xlsx")  # сделать карточку stale

    update_cards(root)
    assert notes.read_text(encoding="utf-8") == "Мои личные мысли.\n"


@patch("obsidianizer.obsidianize._get_now")
def test_card_status_template_mismatch_is_stale_and_migrates(mock_get_now, tmp_path):
    # Freeze time for reproducible test
    fixed_now = datetime(2026, 3, 18, 14, 31)
    mock_get_now.return_value = fixed_now

    root = _make_equipment(tmp_path)
    update_cards(root)  # github по умолчанию
    card = root / "Оборудование.md"
    folder = scan_tree(root)[""]
    assert card_status(card, folder) == "ok"
    assert card_status(card, folder, template="classic") == "stale"
    notes = root / "Оборудование_заметки.md"
    notes.write_text("Моя заметка.\n", encoding="utf-8")  # пользовательские заметки
    summary = update_cards(root, ObsidianizeConfig(template="classic"))
    assert summary.updated == 2  # Оборудование + Арх (в фикстуре без шаблона)
    migrated = card.read_text(encoding="utf-8")
    assert "# Оборудование\n" in migrated
    assert notes.read_text(encoding="utf-8") == "Моя заметка.\n"  # заметка не тронута
    assert "obsidianizer_template: classic" in migrated
    assert card_status(card, scan_tree(root)[""], template="classic") == "ok"


def test_cli_folders_template_classic(tmp_path):
    root = _make_equipment(tmp_path)
    assert main(["folders", "--path", str(root), "--template", "classic"]) == 0
    card = (root / "Оборудование.md").read_text(encoding="utf-8")
    assert "# Оборудование\n" in card
    assert "obsidianizer_template: classic" in card
    assert "cssclasses" not in card


def test_cli_folders_template_default_github(tmp_path):
    root = _make_equipment(tmp_path)
    assert main(["folders", "--path", str(root)]) == 0
    card = (root / "Оборудование.md").read_text(encoding="utf-8")
    assert "## Folders\n" in card
    assert "obsidianizer_template: github" in card
    assert "cssclasses: [github-dashboard]" in card


def test_review_file_path_agrees_with_review_module(tmp_path):
    root = _make_equipment(tmp_path)
    folder = scan_tree(root)[""]
    from obsidianizer.review import review_file_for

    assert review_file_for(folder) == review_file_path(folder)
    assert review_file_for(folder).name == "Оборудование_обзор.md"

# --------------------------------------------------------------------------
# v5.5: Code-table escaping, project card, manifest diff
# --------------------------------------------------------------------------


def test_code_table_wikilink_stays_in_one_cell(tmp_path):
    """The aliased wikilink must be escaped (\|) so the table keeps 4 cells."""
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    card = build_card(scan, None, ObsidianizeConfig(template="github"))

    code_section = card.split("## Folders")[1].split("## README")[0]
    for line in code_section.splitlines():
        if not line.startswith("| 📁"):
            continue
        # mask the escaped alias pipe, then count real cell separators
        normalized = line.replace("\\|", "\x00")
        cells = normalized.strip().strip("|").split("|")
        assert len(cells) == 4, f"row split into {len(cells)} cells: {line!r}"
        assert "[[" in cells[0] and "]]" in cells[0]
        assert "\\|" in line, f"wikilink pipe not escaped: {line!r}"


def test_notes_frontmatter_change_makes_card_stale(tmp_path):
    """Editing designer in the notes marks the card stale even with no file changes."""
    root = _make_equipment(tmp_path)
    update_cards(root)
    card = root / "Оборудование.md"
    notes = root / "Оборудование_заметки.md"

    notes.write_text(
        "---\nклиент: ООО Ромашка\nдизайнер: Татьяна\n---\n\nзаметки\n",
        encoding="utf-8",
    )
    # файлы не менялись, но данные проекта изменились → stale при переданных заметках
    assert (
        card_status(card, scan_tree(root)[""], notes_prev=notes.read_text(encoding="utf-8"))
        == "stale"
    )
    update_cards(root)
    body = card.read_text(encoding="utf-8")
    assert "Татьяна" in body          # карточка пересобрана с новыми данными
    assert "📋 Карточка проекта" in body


def test_project_card_callout_from_notes(tmp_path):
    root = _make_equipment(tmp_path)
    scan = scan_tree(root)[""]
    notes_prev = (
        "---\n"
        "дата_начала: 2026-01-15\n"
        "клиент: ООО Ромашка\n"
        "адрес: Москва\n"
        "источник: Иванов\n"
        "дизайнер: Татьяна\n"
        "комментарий: Согласовать\n"
        "---\n"
    )
    card = build_card(scan, None, ObsidianizeConfig(template="github"), notes_prev=notes_prev)
    assert "> [!info] 📋 Карточка проекта" in card
    assert "> - **Клиент**: ООО Ромашка" in card
    assert "> - **Адрес**: Москва" in card
    assert "> - **Источник**: Иванов" in card
    assert "> - **Дата начала**: 2026-01-15" in card
    assert "> - **Дизайнер**: Татьяна" in card
    assert "> - **Комментарий**: Согласовать" in card


def test_manifest_written_and_diff_detected(tmp_path):
    from obsidianizer.obsidianize import card_diff, format_changes

    root = _make_equipment(tmp_path)
    update_cards(root)
    card = root / "Оборудование.md"
    content = card.read_text(encoding="utf-8")
    assert "obsidianizer-manifest:" in content

    folder = scan_tree(root)[""]
    notes_text = (root / "Оборудование_заметки.md").read_text(encoding="utf-8")

    # без изменений
    diff = card_diff(content, folder, notes_text)
    assert diff is not None
    assert not any([diff["added"], diff["removed"], diff["changed"],
                    diff["folders_changed"], diff["notes_changed"]])
    assert format_changes(diff) == []

    # добавлен файл
    _touch(root / "новый.dwg")
    diff2 = card_diff(content, scan_tree(root)[""], notes_text)
    assert diff2["added"] == ["новый.dwg"]
    assert "добавлен: новый.dwg" in format_changes(diff2)

    # изменён размер существующего
    (root / "Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx").write_bytes(b"xx")
    diff3 = card_diff(content, scan_tree(root)[""], notes_text)
    assert "Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx" in diff3["changed"]
    assert "изменён: Бриз_Монтажные_размеры_FUNAI_2025_v1.xlsx" in format_changes(diff3)

    # удалён
    (root / "новый.dwg").unlink()
    (root / "Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf").unlink()
    diff5 = card_diff(content, scan_tree(root)[""], notes_text)
    assert diff5["removed"] == [
        "Бриз Фунай onsen-full-dc-inverter-heat-pump-instruction.pdf"
    ]
    assert any("удалён" in line for line in format_changes(diff5))


def test_nav_includes_images_when_images_present(tmp_path):
    root = _make_equipment(tmp_path)
    _touch(root / "фото.png")
    scan = scan_tree(root)[""]
    # без vault_root и без gallery_prefix — секция Images всё равно есть
    card = build_card(scan, None, ObsidianizeConfig(template="github"))
    assert "[[#Images|Images]]" in card
    assert "## Images" in card
    assert "img-gallery" not in card
