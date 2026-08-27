"""Markdown processor — primary file type.

Parses raw AI-chat export files (the header style produced by the exporter:
`**Источник:**`, `# title`, `**Оригинал:**` URL, `**Stable ID:**`, message
counts, `<!-- hash: ... -->`, `#### 👤/🤖 (timestamp)`). The body is preserved
verbatim; media references are captured for copying.
"""

from __future__ import annotations

import re

from .base import Processor
from .models import SourceFile


class MdProcessor(Processor):
    extensions = frozenset({".md"})

    _RE_TITLE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
    _RE_SOURCE = re.compile(r"^\*\*Источник:\*\*\s*(.+)$", re.MULTILINE)
    _RE_URL = re.compile(r"^\*\*Оригинал:\*\*[\r\n]+\s*(https?://\S+)", re.MULTILINE)
    _RE_EXPORT_DATE = re.compile(r"^\*\*Дата экспорта:\*\*[\r\n]+\s*(.+)$", re.MULTILINE)
    _RE_STABLE_ID = re.compile(r"^\*\*Stable ID:\*\*[\r\n]+\s*(\S+)", re.MULTILINE)
    _RE_COUNTS = re.compile(
        r"^\*\*Экспортировано сообщений(?: \(основная ветка\))?:\*\*[\r\n]+\s*"
        r"(\d+)\s*\(Пользователь:\s*(\d+),\s*AI:\s*(\d+)\)",
        re.MULTILINE,
    )
    _RE_ATTACHMENTS = re.compile(
        r"^\*\*Вложений:\*\*[\r\n]+\s*(\d+)", re.MULTILINE
    )
    _RE_HASH = re.compile(
        r"<!--\s*hash:\s*(\w+)\s+title:\s*([\w-]+)\s+chat_order:\s*(\d+)\s*-->"
    )
    _RE_BRANCH = re.compile(r"🌿\s*Альтернативная ветвь")
    _RE_SCHEMA = re.compile(r"^\s*<!--\s*schema_version:\s*(\d+)\s*-->", re.MULTILINE)
    # Strict message headers only: "#### 👤 Вы (ts)", "#### 🤖 AI", etc. Other
    # "#### "-prefixed lines inside copied text are never treated as messages.
    _RE_MESSAGE = re.compile(
        r"^####\s+(?P<who>👤|🤖)\s*(?P<name>Вы|AI)?\s*(?:\((?P<ts>[^()]*)\))?\s*$",
        re.MULTILINE,
    )
    _RE_MEDIA = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
    _RE_CODE_BLOCK = re.compile(r"^\s*```", re.MULTILINE)
    _RE_LINK = re.compile(r"https?://[^\s\)\]\}>\"']+")
    _RE_QUOTE = re.compile(r"^\s*>", re.MULTILINE)
    _RE_FILE_MARKER = re.compile(r"\[Файл:\s*[^\]]*\]|📎")

    def parse(self, src: SourceFile) -> dict:
        text = src.abs_path.read_text(encoding="utf-8")
        title = self._first(self._RE_TITLE, text) or ""
        meta = {
            "title": title[:200] or src.name,
            "service": (self._first(self._RE_SOURCE, text) or "").lower(),
            "source_url": self._first(self._RE_URL, text) or "",
            "export_date": self._first(self._RE_EXPORT_DATE, text) or "",
            "chat_id": self._first(self._RE_STABLE_ID, text) or "",
            "messages": {},
            "branches": len(self._RE_BRANCH.findall(text)) + 1,
            "raw_file": src.name,
        }

        counts = self._RE_COUNTS.search(text)
        if counts:
            total, user_c, assistant_c = counts.groups()
            meta["messages"] = {
                "total": int(total),
                "user": int(user_c),
                "assistant": int(assistant_c),
            }

        att = self._RE_ATTACHMENTS.search(text)
        if att:
            meta["attachments"] = int(att.group(1))
        else:
            counted = len(self._RE_MEDIA.findall(text)) + len(
                self._RE_FILE_MARKER.findall(text)
            )
            if counted:
                meta["attachments"] = counted

        h = self._RE_HASH.search(text)
        if h:
            meta["content_hash"], meta["title_slug"], meta["chat_order"] = (
                h.group(1),
                h.group(2),
                int(h.group(3)),
            )

        sv = self._first(self._RE_SCHEMA, text)
        if sv:
            meta["schema_version"] = int(sv)

        roles: list[str] = []
        index: list[dict] = []
        for m in self._RE_MESSAGE.finditer(text):
            role = "user" if m.group("who") == "👤" else "assistant"
            ts = (m.group("ts") or "").strip()
            roles.append(role)
            index.append({"role": role, "ts": ts})
        if index:
            meta["roles"] = sorted(set(roles))
            meta["message_index"] = index
            if index[0]["ts"]:
                meta["first_ts"] = index[0]["ts"]
            if index[-1]["ts"]:
                meta["last_ts"] = index[-1]["ts"]

        blocks = len(self._RE_CODE_BLOCK.findall(text)) // 2
        if blocks:
            meta["code_blocks"] = blocks
        links = self._RE_LINK.findall(text)
        if links:
            meta["links"] = len(set(links))
        quotes = len(self._RE_QUOTE.findall(text))
        if quotes:
            meta["quotes"] = quotes

        if not meta["service"] and src.rel_dir:
            meta["service"] = src.rel_dir.split("/", 1)[0].lower()
        return meta

    def body(self, src: SourceFile) -> str:
        return src.abs_path.read_text(encoding="utf-8")

    def media_refs(self, src: SourceFile) -> list[str]:
        try:
            text = src.abs_path.read_text(encoding="utf-8")
        except OSError:
            return []
        refs = []
        for m in self._RE_MEDIA.finditer(text):
            ref = m.group(1)
            lowered = ref.lower()
            if lowered.startswith(("http://", "https://", "//", "data:", "#")):
                continue
            refs.append(ref)
        return refs

    @staticmethod
    def _first(pattern: re.Pattern, text: str) -> str | None:
        m = pattern.search(text)
        return m.group(1).strip() if m else None