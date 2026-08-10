"""Guard tests — data-destruction scenarios must refuse to run."""

import pytest

from obsidianizer.guard import GuardError, check


def test_equal_paths_refused(tmp_path):
    with pytest.raises(GuardError):
        check(tmp_path, tmp_path)


def test_target_inside_source_refused(tmp_path):
    source = tmp_path / "raw"
    target = source / "processed"
    source.mkdir()
    target.mkdir()
    with pytest.raises(GuardError):
        check(source, target)


def test_source_inside_target_refused(tmp_path):
    target = tmp_path / "vault"
    source = target / "raw"
    target.mkdir()
    source.mkdir()
    with pytest.raises(GuardError):
        check(source, target)


def test_safe_distinct_paths_pass(tmp_path):
    source = tmp_path / "raw"
    target = tmp_path / "processed"
    source.mkdir()
    target.mkdir()
    check(source, target)  # must not raise


def test_siblings_that_share_prefix_pass(tmp_path):
    source = tmp_path / "raw"
    target = tmp_path / "raw2"
    source.mkdir()
    target.mkdir()
    check(source, target)