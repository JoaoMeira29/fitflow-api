"""Tests for the Anthropic provider. A fake SDK client returns scripted
replies, so no request is sent and no API key is needed."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.llm import (
    AnthropicProvider,
    LLMError,
    LLMRetryableError,
    OllamaProvider,
    Tool,
    ToolError,
    _build_provider,
    get_llm_client,
)

MESSAGES = [
    {"role": "system", "content": "You are a dietitian."},
    {"role": "user", "content": "Plan my day."},
]
SCHEMA = {
    "type": "object",
    "properties": {"grams": {"type": "number", "minimum": 5}},
    "required": ["grams"],
}

# --- A fake Anthropic SDK client -------------------------------------------


class FakeAnthropic:
    """Stands in for anthropic.AsyncAnthropic. `client.beta.messages.create`
    returns or raises the queued results in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.requests: list[dict] = []
        self.timeout = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, timeout):
        self.timeout = timeout
        return self

    async def _create(self, **request):
        # The provider keeps appending to its message list, so keep a copy of
        # what was sent each time.
        self.requests.append({**request, "messages": list(request["messages"])})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def text(value: str):
    return SimpleNamespace(type="text", text=value)


def tool_use(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


def reply(*blocks, stop_reason="end_turn", input_tokens=100, output_tokens=20):
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


async def chat(client, *, model="claude-opus-5-5", json_schema=None, tools=()):
    return await AnthropicProvider(client=client).chat(
        model=model,
        messages=MESSAGES,
        json_schema=json_schema,
        tools=list(tools),
        timeout_seconds=42,
    )


def add_tool(calls: list | None = None) -> Tool:
    def handler(arguments: dict) -> dict:
        if calls is not None:
            calls.append(arguments)
        if arguments["a"] < 0:
            raise ToolError("a must not be negative")
        return {"sum": arguments["a"] + arguments["b"]}

    return Tool(
        name="add",
        description="Add two numbers.",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
        handler=handler,
    )


# --- The request -----------------------------------------------------------


async def test_returns_the_text_and_token_counts():
    client = FakeAnthropic(reply(text("hello")))

    result = await chat(client)

    assert result.content == "hello"
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.tool_calls == 0


async def test_sends_the_system_prompt_separately_from_the_conversation():
    client = FakeAnthropic(reply(text("hello")))

    await chat(client)

    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["system"] == "You are a dietitian."
    assert request["messages"] == [{"role": "user", "content": "Plan my day."}]
    assert request["max_tokens"] == AnthropicProvider.MAX_OUTPUT_TOKENS
    assert request["output_config"] == {"effort": "medium"}
    assert "tools" not in request


async def test_uses_the_timeout_it_is_given():
    client = FakeAnthropic(reply(text("hello")))

    await chat(client)

    assert client.timeout == 42


async def test_asks_for_json_that_matches_the_schema():
    client = FakeAnthropic(reply(text('{"grams": 50}')))

    await chat(client, json_schema=SCHEMA)

    output_format = client.requests[0]["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    # The SDK rewrites the schema into the form the API accepts.
    assert output_format["schema"]["additionalProperties"] is False
    assert "minimum" not in output_format["schema"]["properties"]["grams"]


async def test_asks_for_a_server_side_fallback_on_models_that_have_one():
    client = FakeAnthropic(reply(text("hello")))

    await chat(client, model="claude-opus-5-5")

    assert client.requests[0]["fallbacks"] == "default"
    assert client.requests[0]["betas"] == ["server-side-fallback-2026-07-01"]


async def test_sends_no_fallback_on_other_models():
    client = FakeAnthropic(reply(text("hello")))

    await chat(client, model="claude-haiku-5-5")

    assert "fallbacks" not in client.requests[0]
    assert "betas" not in client.requests[0]


# --- The tool loop ---------------------------------------------------------


async def test_runs_a_tool_and_sends_the_result_back():
    calls = []
    first = reply(
        tool_use("call_1", "add", {"a": 2, "b": 3}),
        stop_reason="tool_use",
        input_tokens=100,
        output_tokens=30,
    )
    client = FakeAnthropic(first, reply(text("It is 5."), input_tokens=150))

    result = await chat(client, tools=[add_tool(calls)])

    assert calls == [{"a": 2, "b": 3}]
    assert result.content == "It is 5."
    assert result.tool_calls == 1
    # Usage is added up over both requests.
    assert result.input_tokens == 250
    assert result.output_tokens == 50

    second_request = client.requests[1]["messages"]
    # The model's own turn goes back unchanged, then the tool result.
    assert second_request[-2] == {"role": "assistant", "content": first.content}
    assert second_request[-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"sum": 5}'}
        ],
    }


async def test_describes_the_tools_to_the_model():
    client = FakeAnthropic(reply(text("hello")))

    await chat(client, tools=[add_tool()])

    assert client.requests[0]["tools"] == [
        {
            "name": "add",
            "description": "Add two numbers.",
            "input_schema": add_tool().input_schema,
            "strict": True,
        }
    ]


async def test_runs_several_tool_calls_from_one_reply():
    client = FakeAnthropic(
        reply(
            tool_use("call_1", "add", {"a": 1, "b": 1}),
            tool_use("call_2", "add", {"a": 2, "b": 2}),
            stop_reason="tool_use",
        ),
        reply(text("done")),
    )

    result = await chat(client, tools=[add_tool()])

    assert result.tool_calls == 2
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["call_1", "call_2"]
    assert [json.loads(r["content"])["sum"] for r in results] == [2, 4]


async def test_a_tool_error_goes_back_to_the_model():
    client = FakeAnthropic(
        reply(tool_use("call_1", "add", {"a": -1, "b": 3}), stop_reason="tool_use"),
        reply(text("sorry")),
    )

    await chat(client, tools=[add_tool()])

    assert client.requests[1]["messages"][-1]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Error: a must not be negative",
            "is_error": True,
        }
    ]


async def test_a_call_to_an_unknown_tool_is_an_error_result():
    client = FakeAnthropic(
        reply(tool_use("call_1", "launch_rocket", {}), stop_reason="tool_use"),
        reply(text("ok")),
    )

    await chat(client, tools=[add_tool()])

    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert result["content"] == "Error: Unknown tool: launch_rocket"


async def test_stops_a_model_that_never_stops_calling_tools():
    endless = [
        reply(tool_use(f"call_{n}", "add", {"a": 1, "b": 1}), stop_reason="tool_use")
        for n in range(20)
    ]
    client = FakeAnthropic(*endless)

    with pytest.raises(LLMError, match="still calling tools"):
        await chat(client, tools=[add_tool()])

    assert len(client.requests) == AnthropicProvider.MAX_TOOL_ROUNDS + 1


# --- Replies that cannot be used -------------------------------------------


async def test_a_refusal_is_an_error():
    client = FakeAnthropic(reply(stop_reason="refusal"))

    with pytest.raises(LLMError, match="declined"):
        await chat(client)


async def test_a_reply_cut_off_at_the_token_limit_is_an_error():
    client = FakeAnthropic(reply(text('{"grams":'), stop_reason="max_tokens"))

    with pytest.raises(LLMError, match="cut off"):
        await chat(client)


async def test_a_reply_without_text_is_an_error():
    client = FakeAnthropic(reply(SimpleNamespace(type="thinking")))

    with pytest.raises(LLMError, match="no text"):
        await chat(client)


# --- SDK errors ------------------------------------------------------------

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(error_class, status_code: int):
    response = httpx2.Response(status_code, request=REQUEST)
    return error_class("something went wrong", response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APITimeoutError(request=REQUEST),
        anthropic.APIConnectionError(request=REQUEST),
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.InternalServerError, 500),
    ],
    ids=["timeout", "connection", "rate limit", "server error"],
)
async def test_temporary_failures_are_retryable(error):
    with pytest.raises(LLMRetryableError):
        await chat(FakeAnthropic(error))


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.AuthenticationError, 401),
        status_error(anthropic.NotFoundError, 404),
        status_error(anthropic.BadRequestError, 400),
    ],
    ids=["bad api key", "unknown model", "bad request"],
)
async def test_permanent_failures_are_not_retryable(error):
    with pytest.raises(LLMError) as raised:
        await chat(FakeAnthropic(error))

    assert not isinstance(raised.value, LLMRetryableError)
    assert f"HTTP {error.status_code}" in str(raised.value)


# --- Choosing the provider from the settings -------------------------------


@pytest.fixture
def fresh_llm_client():
    """get_llm_client caches its result, so clear it around each test."""
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


def test_build_provider_returns_the_anthropic_provider(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", SecretStr("test-key"))

    provider = _build_provider("anthropic")

    assert isinstance(provider, AnthropicProvider)
    assert provider.supports_tools is True


def test_ollama_is_the_default_provider_with_its_default_model(
    monkeypatch, fresh_llm_client
):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", None)

    client = get_llm_client()

    assert isinstance(client._provider, OllamaProvider)
    assert client.model == "qwen2.5:7b"


def test_anthropic_gets_its_own_default_model(monkeypatch, fresh_llm_client):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "LLM_MODEL", None)

    assert get_llm_client().model == "claude-opus-5-5"


@pytest.mark.parametrize("provider", ["ollama", "anthropic"])
def test_tools_are_off_unless_the_setting_turns_them_on(
    monkeypatch, fresh_llm_client, provider
):
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "LLM_USE_TOOLS", False)
    assert get_llm_client().supports_tools is False

    get_llm_client.cache_clear()
    monkeypatch.setattr(settings, "LLM_USE_TOOLS", True)
    assert get_llm_client().supports_tools is True


def test_llm_model_overrides_the_default(monkeypatch, fresh_llm_client):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "LLM_MODEL", "claude-haiku-5-5")

    assert get_llm_client().model == "claude-haiku-5-5"
