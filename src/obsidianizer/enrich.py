"""Enrichment — compose YAML frontmatter, a human-readable card, and the
preserved original body into the final note. The card may carry an optional
AI-derived topic and conversation type alongside summary/tags.
"""

from __future__ import annotations

import re
from datetime import datetime

import yaml


def build_frontmatter(
    meta: dict, summary: str, tags: list[str], source_hash: str
) -> str:
    """YAML block with counts/tags as flat lists (Obsidian-friendly)."""

    data = dict(meta)
    # Keep nested structures readable for Obsidian frontmatter
    data["source_hash"] = source_hash
    data["date"] = date_from_meta(meta)
    data["tags"] = tags or []
    data["summary"] = summary or ""
    # The message index lives in the note body, not in the YAML block.
    data.pop("content_hash", None)
    data.pop("message_index", None)
    block = yaml.dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).strip()
    return f"---\n{block}\n---\n"


def build_card(
    meta: dict,
    summary: str,
    tags: list[str],
    topic: str = "",
    ai_type: str = "",
) -> str:
    lines: list[str] = []
    title = str(meta.get("title") or "")
    if title:
        lines.append(f"## 📇 {title}")
    else:
        lines.append("## 📇 Сводка")

    if summary:
        lines.append("")
        lines.append(f"**📌 Резюме:** {summary}")
    if topic:
        lines.append("")
        lines.append(f"**🎯 Тема:** {topic}")
    if ai_type:
        lines.append("")
        lines.append(f"**🧭 Тип:** {ai_type}")
    if tags:
        lines.append("")
        lines.append("**🏷 Теги:** " + " ".join(f"`{t}`" for t in tags))

    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    if meta.get("service"):
        lines.append(f"| **Источник** | {meta['service']} |")
    if meta.get("chat_id"):
        lines.append(f"| **Chat ID** | `{meta['chat_id']}` |")
    stamp = date_from_meta(meta)
    if stamp:
        lines.append(f"| **Дата** | {stamp} |")
    msgs = meta.get("messages") or {}
    if msgs.get("total"):
        lines.append(
            f"| **Сообщений** | {msgs['total']} (👤 {msgs.get('user', 0)} / 🤖 {msgs.get('assistant', 0)}) |"
        )
    if meta.get("branches"):
        lines.append(f"| **Веток** | {meta['branches']} |")
    if meta.get("attachments") is not None:
        lines.append(f"| **Вложений** | {meta['attachments']} |")
    if meta.get("source_url"):
        lines.append(f"| **Оригинал** | [ссылка]({meta['source_url']}) |")
    if meta.get("raw_file"):
        wiki = re.sub(r"\.md$", "", str(meta["raw_file"]))
        lines.append(f"| **RAW** | [[{wiki}]] |")
    lines.append("")
    return "\n".join(lines)


def build_navigation(meta: dict) -> str:
    """Compact navigation index: counts summary + message timeline.

    Only counts and roles — the full conversation stays below in the
    verbatim body, so nothing is duplicated.
    """

    lines: list[str] = ["## Навигация", ""]
    msgs = meta.get("messages") or {}
    parts: list[str] = []
    if isinstance(msgs, dict) and msgs.get("total"):
        parts.append(f"{msgs['total']} сообщений")
    if meta.get("code_blocks"):
        parts.append(f"{meta['code_blocks']} блоков кода")
    if meta.get("links"):
        parts.append(f"{meta['links']} ссылок")
    if meta.get("quotes"):
        parts.append(f"{meta['quotes']} цитат")
    if meta.get("attachments") is not None:
        parts.append(f"{meta['attachments']} вложений")
    if meta.get("branches") and meta["branches"] != 1:
        parts.append(f"{meta['branches']} ветвей")
    if parts:
        lines.append("- " + " · ".join(parts))

    index = meta.get("message_index") or []
    if index:
        lines += ["", "### Сообщения", ""]
        for i, item in enumerate(index, start=1):
            role = "user" if item.get("role") == "user" else "assistant"
            icon = "👤" if role == "user" else "🤖"
            name = "Вы" if role == "user" else "AI"
            ts = str(item.get("ts") or "").strip()
            suffix = f" — {ts}" if ts else ""
            lines.append(f"{i}. {icon} {name}{suffix}")

    return "\n".join(lines).rstrip()


def compose(frontmatter: str, card: str, body: str, navigation: str = "") -> str:
    parts = [frontmatter.rstrip("\n"), "", card.strip()]
    if navigation and navigation.strip():
        parts += ["", navigation.strip()]
    parts += ["", "---", "", body.strip(), ""]
    return "\n".join(parts)


def date_from_meta(meta: dict) -> str:
    """Best-effort human date: first message ts > export date > today."""

    stamp = str(meta.get("first_ts") or meta.get("export_date") or "").strip()
    if stamp:
        return stamp[:10]
    return datetime.now().strftime("%Y-%m-%d")