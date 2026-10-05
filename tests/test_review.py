"""Review module tests: payload collection, request building, markdown, saving."""

from datetime import datetime
from pathlib import Path

from obsidianizer.obsidianize import ObsidianizeConfig, scan_tree
from obsidianizer.review import (
    REVIEW_MAX_TEXT_CHARS,
    build_review_markdown,
    build_request,
    collect_payload,
    review_file_for,
    save_review,
)


def _tree(tmp_path: Path, name: str = "Проект") -> tuple[Path, dict]:
    root = tmp_path / name
    (root / "Чертежи").mkdir(parents=True)
    (root / "Чертежи" / "План.dwg").write_text("x", encoding="utf-8")
    (root / "Спецификация.txt").write_text("сталь С245, болты М16", encoding="utf-8")
    (root / "Заметки.md").write_text("# Заметки\nПокрасить раму", encoding="utf-8")
    (root / "Смета.xlsx").write_text("x", encoding="utf-8")
    tree = scan_tree(root, ObsidianizeConfig())
    return root, tree


def test_review_file_for_is_sibling_of_card(tmp_path):
    root, tree = _tree(tmp_path)
    assert review_file_for(tree[""]).name == "Проект_обзор.md"
    assert review_file_for(tree[""]).parent == root
    assert review_file_for(tree["Чертежи"]).name == "Чертежи_обзор.md"


def test_collect_payload_metadata_and_texts(tmp_path):
    _, tree = _tree(tmp_path)
    payload = collect_payload(tree[""])
    names = {f["name"] for f in payload["files"]}
    assert names == {"Спецификация.txt", "Смета.xlsx"}  # .md never catalogued
    cats = {f["name"]: f["category"] for f in payload["files"]}
    assert cats["Спецификация.txt"] == "Документы"
    assert cats["Смета.xlsx"] == "Таблицы"
    texts = {t["name"]: t["text"] for t in payload["texts"]}
    assert "сталь С245" in texts["Спецификация.txt"]
    assert "Покрасить раму" in texts["Заметки.md"]
    assert payload["subfolders"] == ["Чертежи"]
    assert payload["images"] == 0
    assert payload["card"] == ""


def test_collect_payload_texts_skipped_when_disabled(tmp_path):
    _, tree = _tree(tmp_path)
    payload = collect_payload(tree[""], include_text=False)
    assert payload["texts"] == []
    assert payload["files"]


def test_collect_payload_text_truncated(tmp_path):
    root, tree = _tree(tmp_path)
    (root / "Длинный.txt").write_text("а" * (REVIEW_MAX_TEXT_CHARS * 2), encoding="utf-8")
    payload = collect_payload(tree[""])
    long = next(t for t in payload["texts"] if t["name"] == "Длинный.txt")
    assert len(long["text"]) <= REVIEW_MAX_TEXT_CHARS


def test_collect_payload_includes_card_when_present(tmp_path):
    root, tree = _tree(tmp_path)
    (root / "Проект.md").write_text(
        "---\nobsidianizer: true\n---\n\nКарточка проекта", encoding="utf-8"
    )
    payload = collect_payload(tree[""])
    assert "Карточка проекта" in payload["card"]


def test_build_request_lists_folders_and_files(tmp_path):
    _, tree = _tree(tmp_path)
    request = build_request([collect_payload(tree[""])], include_text=True)
    assert "## Папка: Проект" in request
    assert "Спецификация.txt" in request
    assert "сталь С245" in request
    assert "### Карточка проекта" not in request  # no card in this fixture
    disabled = build_request([collect_payload(tree[""], include_text=False)], include_text=False)
    assert "не запрашивалось" in disabled
    assert "сталь С245" not in disabled


def test_build_review_markdown_frontmatter_and_body():
    md = build_review_markdown(
        "## Назначение\nЧто-то.", model="qwen2.5:latest", now=datetime(2026, 8, 19, 10, 5)
    )
    assert md.startswith("---\ntype: обзор\n")
    assert "generated: 2026-08-19 10:05" in md
    assert "model: qwen2.5:latest" in md
    assert "## Назначение" in md
    assert md.endswith("\n")


def test_save_review_writes_atomically(tmp_path):
    root, tree = _tree(tmp_path)
    target = save_review(tree[""], "текст обзора")
    assert target == review_file_for(tree[""])
    assert target.read_text(encoding="utf-8") == "текст обзора"
    assert not target.with_name(target.name + ".tmp").exists()