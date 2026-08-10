"""Local LLM client (Ollama) for summary + tags enrichment.

LLM is strictly optional: any failure (connection, timeout, parse) degrades to
empty summary/tags and never stops the pipeline. No API keys, no cloud.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger("obsidianizer.llm")


def parse_llm_response(text: str) -> tuple[str, list[str]]:
    """Parse the strict SUMMARY:/TAGS: response contract."""

    summary = ""
    m = re.search(r"SUMMARY:\s*(.+?)(?=\nTAGS:|\Z)", text, re.DOTALL)
    if m:
        summary = m.group(1).strip()

    tags: list[str] = []
    m = re.search(r"TAGS:\s*(.+?)$", text, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        tags = [t.strip(" -*•\t") for t in re.split(r"[,;\n]", raw) if t.strip()]
    return summary, tags


class LLMClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        timeout: float = 180,
        limit_chars: int = 6000,
        temperature: float = 0.2,
        num_ctx: int = 8192,
        prompt: str = "",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.limit_chars = limit_chars
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.prompt_template = prompt

    def summarize(self, content: str) -> tuple[str, list[str]]:
        """Return (summary, tags). Degrades to empty values on any failure."""

        if not content or not self.model:
            return "", []
        prompt = self.prompt_template.replace(
            "{content}", content[: self.limit_chars] or ""
        )
        try:
            resp = httpx.post(
                f"{self.endpoint}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_ctx": self.num_ctx,
                        "temperature": self.temperature,
                    },
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            summary, tags = parse_llm_response(text)
            if not summary and not tags:
                logger.warning("Ollama вернул пустой ответ — проверь модель/промпт")
            return summary, tags
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — пропускаю AI-обогащение", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama: %s", exc)
        return "", []