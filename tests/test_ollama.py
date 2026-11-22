import json

import httpx
import pytest

from reviewlane import summarizer
from reviewlane.models import ChangeKind, FileChange, PullRequest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("REVIEWLANE_LLM", "REVIEWLANE_OLLAMA_MODEL", "OLLAMA_HOST", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def pr():
    return PullRequest(title="t", files=[
        FileChange(path="fmt.py", patch="@@\n-a\n+b", kind=ChangeKind.FORMATTING),
        FileChange(path="core.py", patch="@@\n-a\n+SECRET_BEHAVIOR", kind=ChangeKind.BEHAVIORAL, additions=1),
    ])


class Resp:
    def __init__(self, data): self.data = data
    def json(self): return self.data
    def raise_for_status(self): pass


def fake_ollama(monkeypatch, models=("nomic-embed-text:latest", "llama3.2:latest"), reply="**What changed**\n- x", calls=None):
    def get(url, timeout=None):
        assert url.endswith("/api/tags")
        return Resp({"models": [{"name": n} for n in models]})

    def post(url, json=None, timeout=None):
        if calls is not None:
            calls.append((url, json))
        return Resp({"message": {"content": reply}})

    monkeypatch.setattr(summarizer.httpx, "get", get)
    monkeypatch.setattr(summarizer.httpx, "post", post)


def test_unreachable_ollama_means_no_model(monkeypatch):
    def boom(*a, **k): raise httpx.ConnectError("refused")
    monkeypatch.setattr(summarizer.httpx, "get", boom)
    assert summarizer.ollama_model() is None
    assert summarizer.llm_backend() is None
    text, src = summarizer.summarize(pr())
    assert src == "offline" and "core.py" in text


def test_picks_best_code_model_and_skips_embedding_models(monkeypatch):
    fake_ollama(monkeypatch, models=("nomic-embed-text:latest", "llama3.2:latest", "qwen2.5-coder:7b"))
    assert summarizer.ollama_model() == "qwen2.5-coder:7b"
    fake_ollama(monkeypatch, models=("nomic-embed-text:latest",))
    assert summarizer.ollama_model() is None


def test_explicit_model_is_respected_or_rejected(monkeypatch):
    fake_ollama(monkeypatch, models=("llama3.2:latest", "mistral:7b"))
    monkeypatch.setenv("REVIEWLANE_OLLAMA_MODEL", "mistral")
    assert summarizer.ollama_model() == "mistral:7b"
    monkeypatch.setenv("REVIEWLANE_OLLAMA_MODEL", "not-installed")
    assert summarizer.ollama_model() is None


def test_summarize_uses_ollama_and_only_sends_behavioral_hunks(monkeypatch):
    calls = []
    fake_ollama(monkeypatch, calls=calls)
    text, src = summarizer.summarize(pr())
    assert src == "ollama" and text.startswith("**What changed**")
    url, body = calls[0]
    prompt = body["messages"][1]["content"]
    assert url.endswith("/api/chat") and body["stream"] is False and body["model"] == "llama3.2:latest"
    assert "SECRET_BEHAVIOR" in prompt and "fmt.py" not in prompt
    assert body["options"]["num_ctx"] >= 8192 and len(prompt) <= 13_000
    assert "untrusted" in body["messages"][0]["content"]


def test_ollama_failure_falls_back_to_offline(monkeypatch):
    fake_ollama(monkeypatch)
    def bad_post(*a, **k): raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(summarizer.httpx, "post", bad_post)
    _, src = summarizer.summarize(pr())
    assert src == "offline"


def test_none_setting_disables_ai(monkeypatch):
    fake_ollama(monkeypatch)
    monkeypatch.setenv("REVIEWLANE_LLM", "none")
    assert summarizer.llm_backend() is None
    assert summarizer.summarize(pr())[1] == "offline"


def test_ollama_host_without_scheme(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9999")
    assert summarizer.ollama_base() == "http://127.0.0.1:9999"
