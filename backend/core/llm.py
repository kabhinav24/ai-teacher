"""Provider-agnostic LLM client.

The teaching agents only ever call `complete()` / `complete_json()`. Swapping
Anthropic for a local Ollama model is a one-line env change, which is what makes
the offline demo path possible.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.config import settings
from backend.core.json_utils import extract_json

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class Message:
    role: str
    content: str


class BaseLLM:
    def complete(self, system: str, messages: list[Message], **kw: Any) -> str:  # pragma: no cover
        raise NotImplementedError


class AnthropicLLM(BaseLLM):
    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.llm.api_key or None)

    def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
        resp = self._client.messages.create(
            model=kw.get("model", settings.llm.model),
            max_tokens=kw.get("max_tokens", settings.llm.max_tokens),
            temperature=kw.get("temperature", settings.llm.temperature),
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


class OpenAILLM(BaseLLM):
    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=settings.llm.api_key or None, base_url=settings.llm.base_url or None)

    def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
        resp = self._client.chat.completions.create(
            model=kw.get("model", settings.llm.model),
            max_tokens=kw.get("max_tokens", settings.llm.max_tokens),
            temperature=kw.get("temperature", settings.llm.temperature),
            messages=[{"role": "system", "content": system}] + [{"role": m.role, "content": m.content} for m in messages],
        )
        return resp.choices[0].message.content or ""


class OllamaLLM(BaseLLM):
    """Local models (llama3.1, qwen2.5, gemma2...) for the offline demo."""

    def __init__(self) -> None:
        import httpx

        self._http = httpx
        self._base = settings.llm.base_url or "http://localhost:11434"

    def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
        payload = {
            "model": kw.get("model", settings.llm.model),
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": kw.get("temperature", settings.llm.temperature)},
        }
        r = self._http.post(f"{self._base}/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")


class EchoLLM(BaseLLM):
    """No-network stand-in used by the test suite and by `make demo-offline`.

    It returns deterministic, schema-valid stubs so the full pipeline (planning →
    video → assessment) can be exercised in CI without any API key.
    """

    def complete(self, system: str, messages: list[Message], **kw: Any) -> str:
        from backend.core.stubs import stub_for

        return stub_for(system, messages[-1].content if messages else "")


_PROVIDERS = {
    "anthropic": AnthropicLLM,
    "openai": OpenAILLM,
    "ollama": OllamaLLM,
    "echo": EchoLLM,
}

_client: BaseLLM | None = None


def get_llm() -> BaseLLM:
    global _client
    if _client is None:
        provider = settings.llm.provider.lower()
        if provider not in _PROVIDERS:
            raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Choose from {sorted(_PROVIDERS)}.")
        try:
            _client = _PROVIDERS[provider]()
        except ImportError as exc:
            raise LLMError(f"Install the SDK for LLM_PROVIDER={provider}: {exc}") from exc
    return _client


def complete(system: str, user: str, **kw: Any) -> str:
    return get_llm().complete(system, [Message("user", user)], **kw)


def complete_json(system: str, user: str, *, retries: int = 2, **kw: Any) -> Any:
    """Ask for JSON, tolerate prose/fences around it, and repair once on failure."""
    guard = "\n\nReturn ONLY valid JSON. No markdown fences, no commentary."
    last_err: Exception | None = None
    raw = ""
    for attempt in range(retries + 1):
        try:
            raw = get_llm().complete(system + guard, [Message("user", user)], **kw)
            return extract_json(raw)
        except Exception as exc:  # noqa: BLE001 - repair loop is intentional
            last_err = exc
            log.warning("JSON parse failed (attempt %s/%s): %s", attempt + 1, retries + 1, exc)
            user = (
                "Your previous reply could not be parsed as JSON.\n"
                f"Previous reply:\n{raw[:2000]}\n\n"
                "Return the same content as strictly valid JSON only."
            )
    raise LLMError(f"Model did not return parsable JSON: {last_err}")
