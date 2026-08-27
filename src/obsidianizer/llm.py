"""Local LLM client (Ollama) for summary + tags enrichment.

LLM is strictly optional: any failure (connection, timeout, parse) degrades to
empty values and never stops the pipeline. No API keys, no cloud.

Two independent contracts:
- ``summarize`` — legacy in-pipeline enrichment: (summary, tags) via SUMMARY/TAGS.
- ``analyze`` — AI post-processing stage (``postprocess.enrich``): summary,
  tags, topic and type via SUMMARY/TAGS/TOPIC/TYPE.
- ``analyze_topic`` — group analysis (``topics.create_topic``): name, summary,
  decisions, key facts and artifacts via NAME/SUMMARY/DECISIONS/KEY_FACTS/ARTIFACTS.
- ``analyze_topic_map`` — auto-grouping (``topics.group_all``): clusters the
  whole processed collection into topics by title, via TOPIC:/IDS: blocks.
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


def parse_ai_response(text: str) -> dict:
    """Parse the extended AI post-processing contract.

    Expected blocks (all optional): SUMMARY:, TAGS:, TOPIC:, TYPE:.
    Missing blocks degrade to empty values — a broken model must never
    stop the pipeline.
    """

    result = {"summary": "", "tags": [], "topic": "", "type": ""}

    m = re.search(r"SUMMARY:\s*(.+?)(?=\nTAGS:|\nTOPIC:|\nTYPE:|\Z)", text, re.DOTALL)
    if m:
        result["summary"] = m.group(1).strip()

    m = re.search(r"TAGS:\s*(.+?)(?=\nTOPIC:|\nTYPE:|\Z)", text, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        result["tags"] = [t.strip(" -*•\t") for t in re.split(r"[,;\n]", raw) if t.strip()]

    m = re.search(r"TOPIC:\s*(.+?)(?=\nTYPE:|\Z)", text, re.DOTALL)
    if m:
        result["topic"] = m.group(1).strip()

    m = re.search(r"TYPE:\s*(.+?)$", text, re.DOTALL)
    if m:
        result["type"] = m.group(1).strip()

    return result


_TOPIC_BLOCKS = {
    "DECISIONS": ("KEY_FACTS", "ARTIFACTS"),
    "KEY_FACTS": ("ARTIFACTS",),
    "ARTIFACTS": (),
}


def parse_topic_response(text: str) -> dict:
    """Parse the group-analysis (topic) contract.

    Expected blocks (all optional): NAME:, SUMMARY:, DECISIONS:, KEY_FACTS:,
    ARTIFACTS:. Missing blocks degrade to empty values — a broken model must
    never stop the topic builder.
    """

    result = {
        "name": "",
        "summary": "",
        "decisions": [],
        "key_facts": [],
        "artifacts": [],
    }

    m = re.search(
        r"NAME:\s*(.+?)(?=\nSUMMARY:|\nDECISIONS:|\nKEY_FACTS:|\nARTIFACTS:|\Z)",
        text,
        re.DOTALL,
    )
    if m:
        result["name"] = m.group(1).strip()

    m = re.search(
        r"SUMMARY:\s*(.+?)(?=\nDECISIONS:|\nKEY_FACTS:|\nARTIFACTS:|\Z)",
        text,
        re.DOTALL,
    )
    if m:
        result["summary"] = m.group(1).strip()

    for key, nexts in _TOPIC_BLOCKS.items():
        if nexts:
            m = re.search(
                rf"{key}:\s*(.+?)(?=\n{'|'.join(n + ':' for n in nexts)}|\Z)",
                text,
                re.DOTALL,
            )
        else:
            m = re.search(rf"{key}:\s*(.+?)\Z", text, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            result[key.lower()] = [
                line.strip(" -*•\t").strip()
                for line in raw.splitlines()
                if line.strip(" -*•\t").strip()
            ]
    return result


def parse_topic_map(text: str) -> list[dict]:
    """Parse the auto-grouping (topic map) contract.

    Expected blocks (repeated): ``TOPIC: <name>`` followed by ``IDS: <list>``.
    IDs are 1-based indices of the chats inside the collection. A broken/missing
    ``IDS`` degrades the whole group away — clustering must never crash the
    auto-grouping run.
    """

    groups: list[dict] = []
    for m in re.finditer(r"TOPIC:\s*([^\n]+)", text or ""):
        name = m.group(1).strip()
        if not name:
            continue
        rest = text[m.end():]
        ids_m = re.search(r"IDS:\s*([^\n]*)", rest)
        if not ids_m:
            continue
        ids_line = re.split(r"\n\s*TOPIC:", ids_m.group(1))[0]
        ids = [t.strip() for t in re.split(r"[,;\s]+", ids_line) if t.strip()]
        if not ids:
            continue
        groups.append({"name": name, "ids": ids})
    return groups


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
        ai_prompt: str = "",
        topic_prompt: str = "",
        map_prompt: str = "",
        chat_prompt: str = "",
        embed_model: str = "",
        chat_model: str = "",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.limit_chars = limit_chars
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.prompt_template = prompt
        self.ai_prompt_template = ai_prompt
        self.topic_prompt_template = topic_prompt
        self.map_prompt_template = map_prompt
        self.chat_prompt_template = chat_prompt
        self.embed_model = embed_model
        self.chat_model = chat_model

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

    def analyze(self, content: str) -> dict:
        """AI post-processing pass: summary + tags + topic + type.

        Uses the ``ai_prompt`` template when available (falls back to the
        regular prompt template). Degrades to empty fields on any failure —
        a broken model must never block the import results.
        """

        if not content or not self.model:
            return {"summary": "", "tags": [], "topic": "", "type": ""}
        template = getattr(self, "ai_prompt_template", "") or self.prompt_template
        prompt = template.replace("{content}", content[: self.limit_chars] or "")
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
            parsed = parse_ai_response(text)
            if not parsed["summary"] and not parsed["tags"]:
                logger.warning("Ollama вернул пустой AI-ответ — проверь модель/промпт")
            return parsed
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — пропуск AI-постобработки", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama: %s", exc)
        return {"summary": "", "tags": [], "topic": "", "type": ""}

    def analyze_topic(self, payload: str) -> dict:
        """Group analysis: one LLM call over several chats (payload already
        assembled by the topic builder).

        Uses the ``topic_prompt`` template (falls back to ``ai_prompt``, then
        the regular prompt template). Degrades to empty fields on any failure —
        a broken model must never block the topic result.
        """

        empty = {"name": "", "summary": "", "decisions": [], "key_facts": [], "artifacts": []}
        if not payload or not self.model:
            return empty
        template = (
            getattr(self, "topic_prompt_template", "")
            or getattr(self, "ai_prompt_template", "")
            or self.prompt_template
        )
        prompt = template.replace("{content}", payload)
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
            parsed = parse_topic_response(text)
            if not parsed["summary"] and not parsed["name"]:
                logger.warning("Ollama вернул пустой topic-ответ — проверь модель/промпт")
            return parsed
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — пропуск анализа темы", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama при анализе темы: %s", exc)
        return empty

    def analyze_topic_map(self, payload: str) -> list[dict]:
        """Auto-grouping: cluster the whole processed collection into topics.

        The payload is the topic-map card list (titles, sources, dates and
        short snippets with 1-based indices). Uses the ``map_prompt`` template
        (falls back to ``topic_prompt``, then ``ai_prompt``, then the regular
        prompt template). Degrades to an empty cluster list on any failure —
        a broken model must never crash the auto-grouping run.
        """

        if not payload or not self.model:
            return []
        template = (
            getattr(self, "map_prompt_template", "")
            or getattr(self, "topic_prompt_template", "")
            or getattr(self, "ai_prompt_template", "")
            or self.prompt_template
        )
        prompt = template.replace("{content}", payload)
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
            groups = parse_topic_map(text)
            if not groups:
                logger.warning("Ollama вернул пустую карту тем — проверь модель/промпт")
            return groups
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — пропуск карты тем", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama при построении карты тем: %s", exc)
        return []

    def chat(self, messages: list[dict], system: str = "", *, timeout: float | None = None) -> str:
        """Free-form chat via ``/api/chat`` (no streaming).

        ``messages``: ``[{role, content}]`` with role ``user`` or ``assistant``.
        ``system``: optional system prompt prepended once. Empty messages are
        dropped. Returns the assistant reply text, or ``""`` on failure or an
        empty reply — the caller decides how to surface it.
        """

        if not messages or not self.model:
            return ""
        api_messages: list[dict] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(
            {
                "role": str(m.get("role") or "user"),
                "content": str(m.get("content") or ""),
            }
            for m in messages
            if str(m.get("content") or "").strip()
        )
        try:
            resp = httpx.post(
                f"{self.endpoint}/api/chat",
                json={
                    "model": self.chat_model or self.model,
                    "messages": api_messages,
                    "stream": False,
                    "options": {
                        "num_ctx": self.num_ctx,
                        "temperature": self.temperature,
                    },
                },
                timeout=timeout or self.timeout,
            )
            resp.raise_for_status()
            return str(resp.json().get("message", {}).get("content", "")).strip()
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — чат не выполнен", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama в чате: %s", exc)
        return ""

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Embed a batch of texts via ``/api/embed`` (batched by the caller).

        Used by the semantic search layer (``search.ChatIndex``). Texts are
        truncated to 2048 chars (the nomic-embed-text context limit). Returns
        a list of vectors, or ``None`` on any failure — semantic search must
        degrade silently to the deterministic lexical layer.
        """

        if not texts or not self.embed_model:
            return None
        try:
            resp = httpx.post(
                f"{self.endpoint}/api/embed",
                json={
                    "model": self.embed_model,
                    "input": [str(t)[:2048] for t in texts],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            embs = data.get("embeddings")
            if not isinstance(embs, list) or not embs:
                return None
            return [list(map(float, v)) for v in embs]
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — эмбеддинги не построены", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama при построении эмбеддингов: %s", exc)
        return None

    def list_models(self, timeout: float = 10.0) -> list[str] | None:
        """Return installed model names from Ollama (``GET /api/tags``), sorted.

        Lists names only — never sends any chat content. Returns ``None`` when
        Ollama is unreachable (the caller keeps the current model) and ``[]``
        when it responds but has no models installed.
        """

        try:
            resp = httpx.get(f"{self.endpoint}/api/tags", timeout=timeout)
            resp.raise_for_status()
            models = [
                m.get("name", "")
                for m in resp.json().get("models", [])
                if m.get("name")
            ]
            return sorted(models)
        except httpx.HTTPError as exc:
            logger.warning("Ollama недоступен (%s) — не удалось получить модели", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка Ollama при получении моделей: %s", exc)
        return None