from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn, Protocol

from ..localization import translate as _
from .network import NetworkError, RequestManager


class SupportsHttp(Protocol):
    """Structural interface for the subset of RequestManager used here.

    Lets tests substitute a duck-typed fake without inheriting from RequestManager.
    """

    def http(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        timeout: float | None = None,
        bearer: str | None = None,
    ) -> Any: ...


class LlmBackend(Enum):
    ollama = _("Ollama")
    openai = _("OpenAI compatible")


@dataclass
class LlmModel:
    id: str
    name: str = ""  # display name, falls back to id
    size: int = 0  # bytes, ollama only

    def __post_init__(self):
        if not self.name:
            self.name = self.id


@dataclass
class LlmRequest:
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float | None = None  # None -> omit, use server default
    max_tokens: int = 0  # 0 -> omit
    keep_alive: str | None = None  # Ollama only; None -> server default (usually 5m)


class LlmClient(ABC):
    default_url: str = ""

    def __init__(self, url: str, api_key: str = "", requests: SupportsHttp | None = None):
        self.url = normalize_url(url or self.default_url)
        self._key = api_key
        self._requests: SupportsHttp = requests or RequestManager()

    @abstractmethod
    async def chat(self, request: LlmRequest, timeout: float = 120) -> str: ...

    @abstractmethod
    async def list_models(self, timeout: float = 15) -> list[LlmModel]: ...

    async def check_connection(self, timeout: float = 15) -> list[LlmModel]:
        return await self.list_models(timeout)  # cheapest round-trip that also validates auth


class OllamaClient(LlmClient):
    default_url = "http://127.0.0.1:11434"

    async def chat(self, request: LlmRequest, timeout: float = 120) -> str:
        data: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
            "think": False,
        }
        if request.keep_alive is not None:
            data["keep_alive"] = request.keep_alive
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens:
            options["num_predict"] = request.max_tokens
        if options:
            data["options"] = options

        try:
            result = await self._requests.http(
                "POST", f"{self.url}/api/chat", data, timeout=timeout, bearer=self._key or None
            )
        except NetworkError as e:
            reraise_llm_error(e)
        assert isinstance(result, dict)
        return parse_chat_response_ollama(result)

    async def list_models(self, timeout: float = 15) -> list[LlmModel]:
        try:
            result = await self._requests.http(
                "GET", f"{self.url}/api/tags", timeout=timeout, bearer=self._key or None
            )
        except NetworkError as e:
            reraise_llm_error(e)
        assert isinstance(result, dict)
        return parse_model_list_ollama(result)


class OpenAiClient(LlmClient):
    default_url = "https://api.openai.com/v1"

    async def chat(self, request: LlmRequest, timeout: float = 120) -> str:
        self._require_key()
        data: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }
        if request.temperature is not None:
            data["temperature"] = request.temperature
        if request.max_tokens:
            data["max_completion_tokens"] = request.max_tokens

        try:
            result = await self._requests.http(
                "POST", f"{self.url}/chat/completions", data, timeout=timeout, bearer=self._key
            )
        except NetworkError as e:
            reraise_llm_error(e)
        assert isinstance(result, dict)
        return parse_chat_response_openai(result)

    async def list_models(self, timeout: float = 15) -> list[LlmModel]:
        self._require_key()
        try:
            result = await self._requests.http(
                "GET", f"{self.url}/models", timeout=timeout, bearer=self._key
            )
        except NetworkError as e:
            reraise_llm_error(e)
        assert isinstance(result, dict)
        return parse_model_list_openai(result)

    def _require_key(self):
        if not self._key:
            raise NetworkError(0, _("An API key is required to use this endpoint"), self.url)


def create_llm_client(
    backend: LlmBackend, url: str, api_key: str = "", requests: SupportsHttp | None = None
) -> LlmClient:
    if backend is LlmBackend.ollama:
        return OllamaClient(url, api_key, requests)
    elif backend is LlmBackend.openai:
        return OpenAiClient(url, api_key, requests)
    raise ValueError(f"Unsupported LLM backend: {backend}")


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.replace("0.0.0.0", "127.0.0.1")


def reraise_llm_error(e: NetworkError) -> NoReturn:
    message = e.message
    if isinstance(e.data, dict):
        error = e.data.get("error")
        if isinstance(error, dict):
            message = error.get("message", message)
        elif isinstance(error, str):
            message = error

    if e.status == 401:
        message = _("Authorization failed - check the API key") + f" ({message})"
    elif e.status == 404:
        message = _("Not found - check the URL") + f" ({message})"
    elif e.status == 429:
        message = _("Rate limited - too many requests") + f" ({message})"

    raise NetworkError(e.code, message, e.url, e.status, e.data) from e


_think_pattern = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    return _think_pattern.sub("", text).strip()


def parse_chat_response_ollama(data: dict) -> str:
    content = (data.get("message") or {}).get("content", "")
    if not content:
        raise NetworkError(0, _("The model returned an empty response"), "")
    return strip_reasoning(content)


def parse_model_list_ollama(data: dict) -> list[LlmModel]:
    models = data.get("models", [])
    return [
        LlmModel(id=m.get("model", ""), name=m.get("name", ""), size=m.get("size", 0))
        for m in models
        if m.get("model")
    ]


def parse_chat_response_openai(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise NetworkError(0, _("The model returned an empty response"), "")
    content = (choices[0].get("message") or {}).get("content", "")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise NetworkError(0, _("The model returned an empty response"), "")
    return strip_reasoning(content)


def parse_model_list_openai(data: dict) -> list[LlmModel]:
    return [LlmModel(id=m["id"]) for m in data.get("data", []) if m.get("id")]
