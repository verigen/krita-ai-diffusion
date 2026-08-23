from typing import Any

import pytest

from ai_diffusion.backend.llm_client import (
    LlmModel,
    LlmRequest,
    OllamaClient,
    OpenAiClient,
    normalize_url,
    reraise_llm_error,
    strip_reasoning,
)
from ai_diffusion.backend.network import NetworkError

from .conftest import qtapp
from .mock.http import MockRequests


def make_request(**overrides):
    defaults: dict[str, Any] = {
        "system_prompt": "You are helpful.",
        "user_prompt": "a cat",
        "model": "test-model",
    }
    defaults.update(overrides)
    return LlmRequest(**defaults)


# --- Ollama -------------------------------------------------------------


@qtapp
async def test_ollama_chat_request_shape():
    requests = MockRequests()
    requests.responses.append({"message": {"content": "a fluffy cat"}})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    result = await client.chat(make_request(), timeout=42)

    assert result == "a fluffy cat"
    call = requests.last_call
    assert call.method == "POST"
    assert call.url == "http://127.0.0.1:11434/api/chat"
    assert call.timeout == 42
    assert call.data is not None
    assert call.data["stream"] is False
    assert call.data["model"] == "test-model"
    assert call.data["messages"] == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "a cat"},
    ]
    assert "options" not in call.data


@qtapp
async def test_ollama_chat_temperature_only_when_set():
    requests = MockRequests()
    requests.responses.append({"message": {"content": "result"}})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    await client.chat(make_request(temperature=0.7, max_tokens=100))

    data = requests.last_call.data
    assert data is not None
    assert data["options"] == {"temperature": 0.7, "num_predict": 100}


@qtapp
async def test_ollama_chat_keep_alive_only_when_set():
    requests = MockRequests()
    requests.responses.append({"message": {"content": "result"}})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    await client.chat(make_request())

    data = requests.last_call.data
    assert data is not None
    assert "keep_alive" not in data


@qtapp
async def test_ollama_chat_keep_alive_forwarded():
    requests = MockRequests()
    requests.responses.append({"message": {"content": "result"}})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    await client.chat(make_request(keep_alive="0"))

    data = requests.last_call.data
    assert data is not None
    assert data["keep_alive"] == "0"


@qtapp
async def test_ollama_chat_strips_think_block():
    requests = MockRequests()
    requests.responses.append({"message": {"content": "<think>pondering...</think>a fluffy cat"}})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    result = await client.chat(make_request())

    assert result == "a fluffy cat"


@qtapp
async def test_ollama_chat_empty_content_raises():
    requests = MockRequests()
    requests.responses.append({"message": {"content": ""}})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    with pytest.raises(NetworkError):
        await client.chat(make_request())


@qtapp
async def test_ollama_list_models():
    requests = MockRequests()
    requests.responses.append({
        "models": [
            {"model": "qwen3:8b", "name": "qwen3:8b", "size": 123},
            {"model": "llama3.2:3b", "name": "llama3.2:3b", "size": 456},
        ]
    })
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    models = await client.list_models()

    assert models == [
        LlmModel("qwen3:8b", "qwen3:8b", 123),
        LlmModel("llama3.2:3b", "llama3.2:3b", 456),
    ]
    assert requests.last_call.url == "http://127.0.0.1:11434/api/tags"
    assert requests.last_call.method == "GET"


@qtapp
async def test_ollama_list_models_empty():
    requests = MockRequests()
    requests.responses.append({"models": []})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)

    assert await client.list_models() == []


@qtapp
async def test_ollama_bearer_only_when_key_set():
    requests = MockRequests()
    requests.responses.append({"models": []})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)
    await client.list_models()
    assert requests.last_call.bearer is None

    requests2 = MockRequests()
    requests2.responses.append({"models": []})
    client2 = OllamaClient("http://127.0.0.1:11434", api_key="secret", requests=requests2)
    await client2.list_models()
    assert requests2.last_call.bearer == "secret"


# --- OpenAI ---------------------------------------------------------------


@qtapp
async def test_openai_chat_request_shape():
    requests = MockRequests()
    requests.responses.append({"choices": [{"message": {"content": "a fluffy cat"}}]})
    client = OpenAiClient("https://api.openai.com/v1", api_key="sk-test", requests=requests)

    result = await client.chat(make_request(), timeout=99)

    assert result == "a fluffy cat"
    call = requests.last_call
    assert call.method == "POST"
    assert call.url == "https://api.openai.com/v1/chat/completions"
    assert call.bearer == "sk-test"
    assert call.timeout == 99
    assert call.data is not None
    assert "temperature" not in call.data
    assert "max_completion_tokens" not in call.data


@qtapp
async def test_openai_chat_temperature_and_max_tokens():
    requests = MockRequests()
    requests.responses.append({"choices": [{"message": {"content": "result"}}]})
    client = OpenAiClient("https://api.openai.com/v1", api_key="sk-test", requests=requests)

    await client.chat(make_request(temperature=0.5, max_tokens=200))

    data = requests.last_call.data
    assert data is not None
    assert data["temperature"] == 0.5
    assert data["max_completion_tokens"] == 200


@qtapp
async def test_openai_chat_content_parts_form():
    requests = MockRequests()
    requests.responses.append({
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "a fluffy "},
                        {"type": "text", "text": "cat"},
                    ]
                }
            }
        ]
    })
    client = OpenAiClient("https://api.openai.com/v1", api_key="sk-test", requests=requests)

    result = await client.chat(make_request())

    assert result == "a fluffy cat"


@qtapp
async def test_openai_chat_requires_api_key():
    requests = MockRequests()
    client = OpenAiClient("https://api.openai.com/v1", requests=requests)

    with pytest.raises(NetworkError):
        await client.chat(make_request())
    assert requests.calls == []


@qtapp
async def test_openai_list_models_id_normalization():
    requests = MockRequests()
    requests.responses.append({"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})
    client = OpenAiClient("https://api.openai.com/v1", api_key="sk-test", requests=requests)

    models = await client.list_models()

    assert models == [LlmModel("gpt-4o-mini"), LlmModel("gpt-4o")]
    assert requests.last_call.url == "https://api.openai.com/v1/models"


@qtapp
async def test_openai_list_models_empty():
    requests = MockRequests()
    requests.responses.append({"data": []})
    client = OpenAiClient("https://api.openai.com/v1", api_key="sk-test", requests=requests)

    assert await client.list_models() == []


# --- reraise_llm_error ------------------------------------------------


def test_reraise_openai_nested_error():
    e = NetworkError(1, "boom", "http://x", status=400, data={"error": {"message": "bad request"}})
    with pytest.raises(NetworkError) as exc_info:
        reraise_llm_error(e)
    assert "bad request" in str(exc_info.value)


def test_reraise_ollama_flat_error():
    e = NetworkError(1, "boom", "http://x", status=404, data={"error": "model not found"})
    with pytest.raises(NetworkError) as exc_info:
        reraise_llm_error(e)
    assert "model not found" in str(exc_info.value)


def test_reraise_401_hint():
    e = NetworkError(1, "unauthorized", "http://x", status=401, data=None)
    with pytest.raises(NetworkError) as exc_info:
        reraise_llm_error(e)
    assert "Authorization failed" in str(exc_info.value)


def test_reraise_404_hint():
    e = NetworkError(1, "not found", "http://x", status=404, data=None)
    with pytest.raises(NetworkError) as exc_info:
        reraise_llm_error(e)
    assert "Not found" in str(exc_info.value)


def test_reraise_429_hint():
    e = NetworkError(1, "too many", "http://x", status=429, data=None)
    with pytest.raises(NetworkError) as exc_info:
        reraise_llm_error(e)
    assert "Rate limited" in str(exc_info.value)


# --- normalize_url ------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1"),
        ("0.0.0.0:11434", "http://127.0.0.1:11434"),
        ("http://0.0.0.0:11434/", "http://127.0.0.1:11434"),
    ],
)
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected


# --- strip_reasoning ------------------------------------------------------


def test_strip_reasoning_no_think_block():
    assert strip_reasoning("just a plain answer") == "just a plain answer"


def test_strip_reasoning_multiline():
    text = "<think>\nline one\nline two\n</think>\nfinal answer"
    assert strip_reasoning(text) == "final answer"


# --- timeout forwarding ---------------------------------------------------


@qtapp
async def test_timeout_is_forwarded_ollama():
    requests = MockRequests()
    requests.responses.append({"models": []})
    client = OllamaClient("http://127.0.0.1:11434", requests=requests)
    await client.list_models(timeout=7)
    assert requests.last_call.timeout == 7


@qtapp
async def test_timeout_is_forwarded_openai():
    requests = MockRequests()
    requests.responses.append({"data": []})
    client = OpenAiClient("https://api.openai.com/v1", api_key="sk-test", requests=requests)
    await client.list_models(timeout=13)
    assert requests.last_call.timeout == 13
