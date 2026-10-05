"""TICKET-012 AC2 — the legacy ``--all-md`` flag is gone (negative contract).

``chat_only`` stays alive (filter, prune-guard, config); only the CLI
override surface is removed. Passing the flag must fail as an unknown
argument, not silently change behaviour.
"""

import pytest

from obsidianizer.cli import build_parser


def test_all_md_absent_from_help():
    assert "--all-md" not in build_parser().format_help()


def test_all_md_rejected_as_unknown_argument():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--all-md"])


def test_chat_only_flag_survives():
    args = build_parser().parse_args(["--chat-only"])
    assert args.chat_only is True
