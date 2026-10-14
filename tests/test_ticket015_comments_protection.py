"""TICKET-015 — критическая защита Comments от потери при перегенерации.

Все тесты работают на tmp_path и проверяют реальные файлы на диске после
фактических прогонов update_cards (Этап C плана v6 + уточнения E1/E3/E4).
Любая подтверждённая потеря пользовательского комментария = FAIL.
"""

from pathlib import Path

import pytest

from obsidianizer.obsidianize import (
    ObsidianizeConfig,
    extract_user_comments,
    parse_frontmatter,
    parse_frontmatter_strict,
    update_cards,
)


def _make_tree(root: Path) -> None:
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "sub" / "file.xlsx").write_text("x", encoding="utf-8")
    (root / "a.txt").write_text("hi", encoding="utf-8")


def _card_for(folder: Path) -> Path:
    cards = [c for c in folder.glob("*.md") if not c.name.endswith(("_заметки.md", "_обзор.md"))]
    assert len(cards) == 1, f"expected single card in {folder}, got {cards}"
    return cards[0]


def _notes_for(folder: Path) -> Path:
    return folder / (folder.name + "_заметки.md")


def _type_comment(card: Path, fname: str, marker: str) -> None:
    lines = card.read_text(encoding="utf-8").splitlines()
    out = []
    hit = False
    for line in lines:
        if f"[[{fname}]]" in line and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            assert len(cells) >= 7, f"unexpected Files row: {line!r}"
            cells[-1] = marker
            line = "| " + " | ".join(cells) + " |"
            hit = True
        out.append(line)
    assert hit, f"row [[{fname}]] not found in {card}"
    card.write_text("\n".join(out) + "\n", encoding="utf-8")


def _table_comment(card: Path, fname: str) -> str:
    return extract_user_comments(card.read_text(encoding="utf-8")).get(fname, "")


# --- T015-01: полный прогон вложенной карточки -------------------------------

def test_t015_01_full_run_preserves_nested_comment(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    _type_comment(card, "file.xlsx", "USER_COMMENT_T015_7f31")
    update_cards(root, cfg)
    update_cards(root, cfg)
    text = card.read_text(encoding="utf-8")
    assert "USER_COMMENT_T015_7f31" in text
    assert _table_comment(card, "file.xlsx") == "USER_COMMENT_T015_7f31"
    notes = parse_frontmatter(_notes_for(sub).read_text(encoding="utf-8"))
    assert notes.get("file_comments", {}).get("file.xlsx") == "USER_COMMENT_T015_7f31"


# --- T015-02: Templater-эквивалент --------------------------------------------

def test_t015_02_templater_single_card_preserves(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    _type_comment(card, "file.xlsx", "USER_COMMENT_T015_TMPL")
    # Templater сканирует саму папку карточки: recursive=False, adopt.
    tcfg = ObsidianizeConfig(adopt=True)
    s = update_cards(sub, tcfg, recursive=False)
    assert s.failed == []
    assert _table_comment(card, "file.xlsx") == "USER_COMMENT_T015_TMPL"
    # Полный прогон после Templater — комментарий цел (межбазисная стабильность).
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == "USER_COMMENT_T015_TMPL"


# --- T015-03: несколько карточек, одинаковые имена -----------------------------

def test_t015_03_same_filenames_stay_with_own_cards(tmp_path):
    root = tmp_path / "proj"
    for sub in ("a", "b"):
        (root / sub).mkdir(parents=True)
        (root / sub / "same.xlsx").write_text("x", encoding="utf-8")
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    ca, cb = _card_for(root / "a"), _card_for(root / "b")
    _type_comment(ca, "same.xlsx", "COMMENT_A_015")
    _type_comment(cb, "same.xlsx", "COMMENT_B_015")
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(ca, "same.xlsx") == "COMMENT_A_015"
    assert _table_comment(cb, "same.xlsx") == "COMMENT_B_015"


# --- T015-04: повторные прогоны -------------------------------------------------

def test_t015_04_three_runs_no_degradation(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    card = _card_for(root / "sub")
    _type_comment(card, "file.xlsx", "USER_COMMENT_T015_REPEAT")
    for _ in range(3):
        update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == "USER_COMMENT_T015_REPEAT"


# --- T015-05: force / rebuild ----------------------------------------------------

def test_t015_05_force_rebuild_preserve_own_comments(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    update_cards(root, ObsidianizeConfig())
    card = _card_for(root / "sub")
    _type_comment(card, "file.xlsx", "USER_COMMENT_T015_FORCE")
    update_cards(root, ObsidianizeConfig(rebuild=True))
    assert _table_comment(card, "file.xlsx") == "USER_COMMENT_T015_FORCE"
    update_cards(root, ObsidianizeConfig(force=True))
    assert _table_comment(card, "file.xlsx") == "USER_COMMENT_T015_FORCE"


def test_t015_05_foreign_notes_still_conflict(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    sub = root / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    # Чужая заметка без маркера + чужая карточка без маркера.
    (sub / "sub_заметки.md").write_text("# чужое\n", encoding="utf-8")
    (sub / "sub.md").write_text("# чужая карточка\n", encoding="utf-8")
    s = update_cards(root, ObsidianizeConfig())
    assert s.conflicts, "foreign note must stay conflict, not overwritten"
    assert (sub / "sub_заметки.md").read_text(encoding="utf-8") == "# чужое\n"


# --- T015-06: пустой комментарий --------------------------------------------------

def test_t015_06_empty_comment_is_fine(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "one.xlsx").write_text("x", encoding="utf-8")
    (root / "sub" / "two.xlsx").write_text("y", encoding="utf-8")
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    card = _card_for(root / "sub")
    _type_comment(card, "one.xlsx", "NEIGHBOUR_015")
    # two.xlsx остаётся пустым намеренно.
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(card, "one.xlsx") == "NEIGHBOUR_015"
    assert _table_comment(card, "two.xlsx") == ""


# --- T015-07: безопасный отказ ----------------------------------------------------

def test_t015_07_corrupt_notes_refuses_without_overwrite(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    _type_comment(card, "file.xlsx", "SAFE_015")
    update_cards(root, cfg)
    notes = _notes_for(sub)
    before = notes.read_bytes()
    # Портим frontmatter заметок: незакрытый блок.
    notes.write_text("---\nfile_comments: [oops\nконтакт: Иван\n", encoding="utf-8")
    before_card = card.read_bytes()
    s = update_cards(root, cfg)
    assert "sub" in s.failed
    assert notes.read_bytes() != b""  # файл существует
    assert card.read_bytes() == before_card  # карточка не перезаписана
    # Остальные поля не потеряны молча — файл вообще не тронут.
    raw = notes.read_text(encoding="utf-8")
    assert "контакт" in raw


def test_t015_07_corrupt_card_refuses_without_overwrite(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    card = _card_for(root / "sub")
    _type_comment(card, "file.xlsx", "SAFE_CARD_015")
    update_cards(root, cfg)
    card.write_text("---\nobsidianizer: true\nbroken: [x\n", encoding="utf-8")
    corrupted = card.read_bytes()
    s = update_cards(root, cfg)
    assert "sub" in s.failed
    after = card.read_bytes()
    assert after == corrupted  # прогон отказался перезаписывать повреждённую карточку
    assert b"broken" in after


def test_t015_07_unreadable_notes_marks_failed(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    _type_comment(card, "file.xlsx", "SAFE_OS_015")
    update_cards(root, cfg)
    before = card.read_bytes()
    notes = _notes_for(sub)
    real_read = Path.read_text

    def boom(self, *a, **k):
        if self == notes:
            raise OSError("EACCES")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom)
    s = update_cards(root, cfg)
    monkeypatch.undo()
    assert "sub" in s.failed
    assert card.read_bytes() == before


# --- T015-08: авто-поля меняются, Comments цел -------------------------------------

def test_t015_08_auto_change_keeps_comments(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    card = _card_for(root / "sub")
    _type_comment(card, "file.xlsx", "AUTO_KEEP_015")
    update_cards(root, cfg)
    (root / "sub" / "new.pdf").write_text("pdf", encoding="utf-8")
    s = update_cards(root, cfg)
    assert s.failed == []
    text = card.read_text(encoding="utf-8")
    assert "new.pdf" in text  # авто-часть обновилась
    assert _table_comment(card, "file.xlsx") == "AUTO_KEEP_015"


# --- T015-09: старые карточки + legacy-ключи ---------------------------------------

def test_t015_09_real_victim_vault_recovers_not_erases(tmp_path):
    """Реальный пострадавший vault (F1): FM хранит КОРОТКИЙ ключ f.name
    (именно так писал багованный sync после 0008532), карточка показывает
    пустую ячейку из-за промаха рендера. Один прогон обязан ВОССТАНОВИТЬ
    отображение, а не стереть FM в ""."""

    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    notes = _notes_for(sub)
    _type_comment(card, "file.xlsx", "VICTIM_REAL_015")
    update_cards(root, cfg)  # sync: FM file.xlsx = marker (stale card, skipped)
    props = parse_frontmatter(notes.read_text(encoding="utf-8"))
    assert props.get("file_comments", {}).get("file.xlsx") == "VICTIM_REAL_015"
    # Карточка перерисована пустой (промах рендера до фикса), FM нетронут.
    lines = card.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        if "[[file.xlsx]]" in line and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells[-1] = ""
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    card.write_text("\n".join(out) + "\n", encoding="utf-8")
    assert _table_comment(card, "file.xlsx") == ""
    fm_before = parse_frontmatter(notes.read_text(encoding="utf-8"))
    assert fm_before.get("file_comments", {}).get("file.xlsx") == "VICTIM_REAL_015"
    s = update_cards(root, cfg)  # один прогон — восстановление
    assert "sub" not in s.failed
    fm_after = parse_frontmatter(notes.read_text(encoding="utf-8"))
    assert fm_after.get("file_comments", {}).get("file.xlsx") == "VICTIM_REAL_015", (
        "F1: тихая очистка FM запрещена — пустая ячейка stale-карточки не стирает"
    )
    assert _table_comment(card, "file.xlsx") == "VICTIM_REAL_015"
    # Второй прогон — стабильность, идемпотентность (D3).
    snap_card = card.read_bytes()
    snap_notes = notes.read_bytes()
    update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == "VICTIM_REAL_015"
    assert card.read_bytes() == snap_card
    assert notes.read_bytes() == snap_notes


def test_t015_09_fresh_render_miss_restores(tmp_path):
    """Аутентичный пострадавший vault (аудит №2): карточка ПЕРЕПИСАНА
    багованным рендером — manifest свежий (notes-hash совпадает), но снапшот
    ``fc`` доказывает, что карта показывала ПУСТО, а не значение из FM.
    Один прогон обязан восстановить отображение, а не стереть FM."""

    from obsidianizer.obsidianize import build_card, scan_tree, _parse_manifest

    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    notes = _notes_for(sub)
    _type_comment(card, "file.xlsx", "FRESH_MISS_015")
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == "FRESH_MISS_015"
    # Имитация багованного рендера: пересобираем карточку с пустой ячейкой,
    # но со СВЕЖИМ manifest (notes-hash текущего FM). Снапшот fc при этом
    # честно фиксирует h("") — карта значение НЕ показывала.
    notes_text = notes.read_text(encoding="utf-8")
    scan = scan_tree(root)["sub"]
    buggy = build_card(
        scan, None, cfg, None, None, notes_text, None, {"file.xlsx": ""}, {},
    )
    card.write_text(buggy, encoding="utf-8")
    assert _table_comment(card, "file.xlsx") == ""
    man = _parse_manifest(buggy)
    assert man is not None and "fc" in man, "manifest обязан нести fc-снапшот"
    from obsidianizer.obsidianize import _notes_user_hash

    assert man.get("notes") == _notes_user_hash(notes_text), "manifest свежий"
    s = update_cards(root, cfg)  # один прогон — восстановление, не стирание
    assert "sub" not in s.failed
    fm_after = parse_frontmatter(notes.read_text(encoding="utf-8"))
    assert fm_after.get("file_comments", {}).get("file.xlsx") == "FRESH_MISS_015", (
        "F1: fresh-промах рендера обязан восстанавливаться, а не стираться"
    )
    assert _table_comment(card, "file.xlsx") == "FRESH_MISS_015"


def test_t015_09b_scanbasis_legacy_key_migrates(tmp_path):
    """Старые (pre-0008532) FM с scan-basis ключом sub/file.xlsx мигрируют
    в canonical f.name без потери текста."""

    from obsidianizer.obsidianize import _notes_frontmatter, _strip_frontmatter

    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    notes = _notes_for(sub)
    ntext = notes.read_text(encoding="utf-8")
    props = parse_frontmatter(ntext)
    props.setdefault("file_comments", {})["sub/file.xlsx"] = "LEGACY_LONG_015"
    body = _strip_frontmatter(ntext).lstrip("\n")
    notes.write_text(_notes_frontmatter(props) + "\n\n" + body, encoding="utf-8")
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == "LEGACY_LONG_015"
    props2 = parse_frontmatter(notes.read_text(encoding="utf-8"))
    assert props2.get("file_comments", {}).get("file.xlsx") == "LEGACY_LONG_015"


# --- Кейс B1: осознанная очистка -----------------------------------------------------

def test_t015_b1_deliberate_clear_is_not_restored(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    _type_comment(card, "file.xlsx", "TO_CLEAR_015")
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == "TO_CLEAR_015"
    # Пользователь осознанно очищает ячейку (canonical уже был в FM).
    _type_comment(card, "file.xlsx", "")
    update_cards(root, cfg)
    update_cards(root, cfg)
    assert _table_comment(card, "file.xlsx") == ""
    notes = parse_frontmatter(_notes_for(sub).read_text(encoding="utf-8"))
    assert notes.get("file_comments", {}).get("file.xlsx", "MISSING") == ""


# --- Кейс B3: legacy без файла ---------------------------------------------------------

def test_t015_b3_legacy_without_file_preserved(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    _type_comment(card, "file.xlsx", "KEEPER_015")
    update_cards(root, cfg)
    from obsidianizer.obsidianize import _notes_frontmatter, _strip_frontmatter

    notes = _notes_for(sub)
    text = notes.read_text(encoding="utf-8")
    # Подкладываем legacy-ключ удалённого файла напрямую в FM (через YAML).
    props = parse_frontmatter(text)
    props.setdefault("file_comments", {})["ghost.xlsx"] = "GHOST_VAL_015"
    body = _strip_frontmatter(text).lstrip("\n")
    notes.write_text(_notes_frontmatter(props) + "\n\n" + body, encoding="utf-8")
    update_cards(root, cfg)
    update_cards(root, cfg)
    props = parse_frontmatter(notes.read_text(encoding="utf-8"))
    assert props.get("file_comments", {}).get("ghost.xlsx") == "GHOST_VAL_015"


# --- Контракт parse_frontmatter_strict (5 кейсов §15 + E1/E3/E4) --------------------------

def test_strict_absent_notes_is_not_corrupt():
    props, st = parse_frontmatter_strict("# просто заметки без FM\n")
    assert st == "absent" and props == {}


def test_strict_valid_block():
    props, st = parse_frontmatter_strict("---\nконтакт: Иван\n---\nbody\n")
    assert st == "valid" and props.get("контакт") == "Иван"


def test_strict_corrupt_unclosed():
    _, st = parse_frontmatter_strict("---\nконтакт: Иван\nbody без закрытия\n")
    assert st == "corrupt"


def test_strict_corrupt_bad_yaml():
    _, st = parse_frontmatter_strict("---\nfile_comments: [oops\n---\nbody\n")
    assert st == "corrupt"


def test_strict_legacy_masking_is_corrupt():
    # Битый YAML, который legacy-парсер частично разобрал бы, — строго corrupt.
    _, st = parse_frontmatter_strict("---\nfile_comments: [oops\nконтакт: Иван\n---\nbody\n")
    assert st == "corrupt"


def test_strict_empty_block_is_valid():
    props, st = parse_frontmatter_strict("---\n---\nbody\n")
    assert st == "valid" and props == {}


def test_strict_nondict_is_corrupt():
    _, st = parse_frontmatter_strict("---\n- a\n- b\n---\nbody\n")
    assert st == "corrupt"


def test_strict_crlf_is_valid():
    _, st = parse_frontmatter_strict("---\r\nконтакт: Иван\r\n---\r\nbody\r\n")
    assert st == "valid"


def test_strict_leading_blank_line_is_not_absent():
    _, st = parse_frontmatter_strict("\n---\nконтакт: Иван\n---\nbody\n")
    assert st in ("valid", "corrupt")  # главное — не absent с тихой перезаписью
    assert st == "valid"


def test_strict_bom_is_valid():
    _, st = parse_frontmatter_strict("﻿---\nконтакт: Иван\n---\nbody\n")
    assert st == "valid"


def test_strict_notes_without_fm_processes_normally(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    notes = _notes_for(sub)
    # Сносим FM полностью — штатный случай, failed пуст (нет ложных отказов).
    body = "# Рабочие заметки\n\nтекст\n"
    notes.write_text(body, encoding="utf-8")
    s = update_cards(root, cfg)
    assert "sub" not in s.failed


# --- F4: осознанное ограничение строгого гейта (тест-наблюдение) -----------------
# Значение с ": " внутри (напр. "Встреча: 10:00") — валидный tolerant-YAML
# (см. tests/test_obsidianize.py:2111), но strict считает его corrupt, и папка
# уходит в failed БЕЗ перезаписи. Это безопасный отказ, а не потеря данных.

def test_f4_colon_value_strict_corrupt_but_tolerant_reads():
    from obsidianizer.obsidianize import parse_frontmatter as _pf

    content = "---\nкомментарий: Встреча: 10:00\n---\n\nbody\n"
    assert _pf(content).get("комментарий") == "Встреча: 10:00"
    _, st = parse_frontmatter_strict(content)
    assert st == "corrupt"


def test_f4_colon_value_notes_fails_safe_without_overwrite(tmp_path):
    root = tmp_path / "proj"
    _make_tree(root)
    cfg = ObsidianizeConfig()
    update_cards(root, cfg)
    sub = root / "sub"
    card = _card_for(sub)
    notes = _notes_for(sub)
    before_card = card.read_bytes()
    ntext = notes.read_text(encoding="utf-8")
    ntext = ntext.replace("комментарий: null", "комментарий: Встреча: 10:00")
    assert "Встреча: 10:00" in ntext
    notes.write_text(ntext, encoding="utf-8")
    before_notes = notes.read_bytes()
    s = update_cards(root, cfg)
    assert "sub" in s.failed
    assert notes.read_bytes() == before_notes
    assert card.read_bytes() == before_card
