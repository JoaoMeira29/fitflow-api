import json
import logging

import httpx
import pytest

from app.core.llm import (
    LLMClient,
    LLMError,
    LLMRetryableError,
    OllamaProvider,
    ProviderReply,
    _build_provider,
)

MESSAGES = [{"role": "user", "content": "Hello"}]
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}

OLLAMA_REPLY = {
    "model": "test-model",
    "message": {"role": "assistant", "content": '{"answer": "hi"}'},
    "prompt_eval_count": 12,
    "eval_count": 7,
}


def ollama(handler) -> OllamaProvider:
    """An OllamaProvider whose HTTP requests go to `handler`, not the network."""
    return OllamaProvider("http://ollama.test", transport=httpx.MockTransport(handler))


async def chat(provider, json_schema=SCHEMA):
    return await provider.chat(
        model="test-model",
        messages=MESSAGES,
        json_schema=json_schema,
        timeout_seconds=5,
    )


# --- OllamaProvider --------------------------------------------------------


async def test_ollama_returns_the_content_and_token_counts():
    provider = ollama(lambda request: httpx.Response(200, json=OLLAMA_REPLY))

    reply = await chat(provider)

    assert reply == ProviderReply(
        content='{"answer": "hi"}', input_tokens=12, output_tokens=7
    )


async def test_ollama_sends_the_model_messages_and_schema():
    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json=OLLAMA_REPLY)

    await chat(ollama(handler))

    assert sent["url"] == "http://ollama.test/api/chat"
    assert sent["body"]["model"] == "test-model"
    assert sent["body"]["messages"] == MESSAGES
    assert sent["body"]["format"] == SCHEMA
    assert sent["body"]["stream"] is False


async def test_ollama_omits_the_format_without_a_schema():
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json=OLLAMA_REPLY)

    await chat(ollama(handler), json_schema=None)

    assert "format" not in sent["body"]


async def test_ollama_timeout_is_retryable():
    def handler(request):
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(LLMRetryableError):
        await chat(ollama(handler))


async def test_ollama_connection_failure_is_retryable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMRetryableError):
        await chat(ollama(handler))


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_ollama_server_errors_are_retryable(status_code):
    provider = ollama(lambda request: httpx.Response(status_code, text="busy"))

    with pytest.raises(LLMRetryableError):
        await chat(provider)


async def test_ollama_client_errors_are_not_retryable():
    provider = ollama(lambda request: httpx.Response(404, text="model not found"))

    with pytest.raises(LLMError) as error:
        await chat(provider)

    assert not isinstance(error.value, LLMRetryableError)
    assert "model not found" in str(error.value)


async def test_ollama_rejects_an_unexpected_response():
    provider = ollama(lambda request: httpx.Response(200, json={"oops": True}))

    with pytest.raises(LLMError):
        await chat(provider)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        _build_provider("crystal-ball")


# --- LLMClient -------------------------------------------------------------


class ScriptedProvider:
    """A provider that returns or raises the queued results in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


REPLY = ProviderReply(content="hello", input_tokens=12, output_tokens=7)


def client_for(provider, max_retries=2) -> LLMClient:
    return LLMClient(
        provider,
        model="test-model",
        timeout_seconds=30,
        max_retries=max_retries,
        retry_delay_seconds=0,
    )


async def test_generate_returns_the_reply():
    provider = ScriptedProvider(REPLY)

    response = await client_for(provider).generate(MESSAGES, json_schema=SCHEMA)

    assert response.content == "hello"
    assert response.model == "test-model"
    assert response.input_tokens == 12
    assert response.output_tokens == 7
    assert response.latency_ms >= 0
    assert provider.calls == [
        {
            "model": "test-model",
            "messages": MESSAGES,
            "json_schema": SCHEMA,
            "timeout_seconds": 30,
        }
    ]


async def test_generate_retries_after_a_retryable_error():
    provider = ScriptedProvider(LLMRetryableError("timeout"), REPLY)

    response = await client_for(provider).generate(MESSAGES)

    assert response.content == "hello"
    assert len(provider.calls) == 2


async def test_generate_gives_up_after_the_last_retry():
    provider = ScriptedProvider(
        LLMRetryableError("timeout 1"),
        LLMRetryableError("timeout 2"),
        LLMRetryableError("timeout 3"),
    )

    with pytest.raises(LLMError, match="timeout 3"):
        await client_for(provider, max_retries=2).generate(MESSAGES)

    assert len(provider.calls) == 3


async def test_generate_does_not_retry_a_permanent_error():
    provider = ScriptedProvider(LLMError("model not found"), REPLY)

    with pytest.raises(LLMError, match="model not found"):
        await client_for(provider).generate(MESSAGES)

    assert len(provider.calls) == 1


async def test_generate_logs_latency_and_token_usage(caplog):
    provider = ScriptedProvider(REPLY)

    with caplog.at_level(logging.INFO, logger="app.core.llm"):
        await client_for(provider).generate(MESSAGES)

    message = caplog.messages[-1]
    assert "model=test-model" in message
    assert "latency_ms=" in message
    assert "input_tokens=12" in message
    assert "output_tokens=7" in message


async def test_generate_logs_each_failed_attempt(caplog):
    provider = ScriptedProvider(LLMRetryableError("timeout"), REPLY)

    with caplog.at_level(logging.WARNING, logger="app.core.llm"):
        await client_for(provider).generate(MESSAGES)

    assert "attempt=1/3" in caplog.messages[0]
    assert "error=timeout" in caplog.messages[0]
