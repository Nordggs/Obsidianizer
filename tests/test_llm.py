"""LLMClient.chat tests — the interactive assistant path via ``/api/chat``.

The chat endpoint differs from the rest of the client (``/api/generate``): it
takes a ``messages`` array and returns the reply under ``message.content``.
"""

import httpx

from obsidianizer.llm import LLMClient


class _FakeChatResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _CapturingPost:
    def __init__(self, reply="ответ модели"):
        self.calls = []
        self.reply = reply

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeChatResponse({"message": {"content": self.reply}})


class _EmbedPost:
    def __init__(self, payload):
        self.calls = []
        self.payload = payload

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeChatResponse(self.payload)


def _client() -> LLMClient:
    return LLMClient(endpoint="http://localhost:11434", model="test-model")


def test_chat_builds_api_chat_payload(monkeypatch):
    post = _CapturingPost("привет, пользователь!")
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    reply = _client().chat(
        [{"role": "user", "content": "вопрос"}],
        system="Ты — помощник.",
    )

    assert reply == "привет, пользователь!"
    (url,), kwargs = post.calls[0]
    assert url.endswith("/api/chat")
    body = kwargs["json"]
    assert body["model"] == "test-model"
    assert body["stream"] is False
    assert body["options"] == {"num_ctx": 8192, "temperature": 0.2}
    assert body["messages"] == [
        {"role": "system", "content": "Ты — помощник."},
        {"role": "user", "content": "вопрос"},
    ]


def test_chat_drops_empty_messages_and_no_system(monkeypatch):
    post = _CapturingPost()
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    _client().chat(
        [
            {"role": "user", "content": ""},
            {"role": "user", "content": "  "},
            {"role": "assistant", "content": "x"},
        ]
    )

    _, kwargs = post.calls[0]
    assert kwargs["json"]["messages"] == [{"role": "assistant", "content": "x"}]


def test_chat_returns_empty_without_messages(monkeypatch):
    post = _CapturingPost()
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    assert _client().chat([]) == ""
    assert post.calls == [], "no request must be sent for an empty dialog"


def test_chat_returns_empty_without_model():
    client = LLMClient(endpoint="http://localhost:11434", model="")
    assert client.chat([{"role": "user", "content": "x"}]) == ""


def test_chat_degrades_on_error(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("obsidianizer.llm.httpx.post", boom)

    assert _client().chat([{"role": "user", "content": "x"}]) == ""


def test_chat_returns_raw_reply_whitespace_stripped(monkeypatch):
    post = _CapturingPost("  ответ с пробелами  ")
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    assert _client().chat([{"role": "user", "content": "x"}]) == "ответ с пробелами"


# ── embed (semantic search layer) ───────────────────────────────────────────


def test_embed_builds_api_embed_payload(monkeypatch):
    post = _EmbedPost({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    client = LLMClient(
        endpoint="http://localhost:11434",
        model="test-model",
        embed_model="nomic-embed-text:latest",
    )
    vecs = client.embed(["первый текст", "второй"])

    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
    (url,), kwargs = post.calls[0]
    assert url.endswith("/api/embed")
    body = kwargs["json"]
    assert body["model"] == "nomic-embed-text:latest"
    assert body["input"] == ["первый текст", "второй"]


def test_embed_returns_none_without_embed_model(monkeypatch):
    post = _EmbedPost({"embeddings": [[0.1]]})
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    assert _client().embed(["x"]) is None
    assert post.calls == []


def test_embed_degrades_on_error(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("obsidianizer.llm.httpx.post", boom)

    client = LLMClient(
        endpoint="http://localhost:11434",
        model="test-model",
        embed_model="nomic-embed-text:latest",
    )
    assert client.embed(["x"]) is None


def test_embed_returns_none_on_bad_payload(monkeypatch):
    post = _EmbedPost({"not_embeddings": 1})
    monkeypatch.setattr("obsidianizer.llm.httpx.post", post)

    client = LLMClient(
        endpoint="http://localhost:11434",
        model="test-model",
        embed_model="nomic-embed-text:latest",
    )
    assert client.embed(["x"]) is None