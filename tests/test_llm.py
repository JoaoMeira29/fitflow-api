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
    Tool,
    ToolError,
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
        tools=[],
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


# --- OllamaProvider: tools -------------------------------------------------


def add_tool() -> Tool:
    def handler(arguments: dict) -> dict:
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
        },
        handler=handler,
    )


def ollama_message(content="", tool_calls=None, prompt_tokens=10, output_tokens=5):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = [
            {"function": {"name": name, "arguments": arguments}}
            for name, arguments in tool_calls
        ]
    return {
        "message": message,
        "prompt_eval_count": prompt_tokens,
        "eval_count": output_tokens,
    }


def scripted_ollama(*replies):
    """An OllamaProvider that answers with `replies` in order, plus the list
    of request bodies it received."""
    queue = list(replies)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0))

    return ollama(handler), requests


async def chat_with_tools(provider, tools, json_schema=SCHEMA):
    return await provider.chat(
        model="test-model",
        messages=MESSAGES,
        json_schema=json_schema,
        tools=tools,
        timeout_seconds=5,
    )


async def test_ollama_runs_a_tool_then_asks_for_the_final_answer():
    provider, requests = scripted_ollama(
        ollama_message(tool_calls=[("add", {"a": 2, "b": 3})]),
        ollama_message("It is 5."),
        ollama_message('{"answer": "5"}'),
    )

    reply = await chat_with_tools(provider, [add_tool()])

    assert reply.content == '{"answer": "5"}'
    assert reply.tool_calls == 1
    # Usage is added up over the three requests.
    assert reply.input_tokens == 30
    assert reply.output_tokens == 15

    # Tool rounds offer the tools and no schema, with a limit on the output.
    assert requests[0]["options"]["num_predict"] == OllamaProvider.TOOL_ROUND_MAX_TOKENS
    assert "num_predict" not in requests[2]["options"]
    assert requests[0]["tools"][0]["function"]["name"] == "add"
    assert requests[0]["tools"][0]["function"]["parameters"] == add_tool().input_schema
    assert "format" not in requests[0]
    # The second round sees the model's call and the tool's result.
    assert requests[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "add"
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_name": "add",
        "content": '{"sum": 5}',
    }
    # The final request asks for the schema and offers no tools.
    assert requests[2]["format"] == SCHEMA
    assert "tools" not in requests[2]
    assert requests[2]["messages"][-1]["role"] == "tool"


async def test_ollama_answers_directly_when_the_model_calls_no_tool():
    provider, requests = scripted_ollama(
        ollama_message("I do not need the tool."),
        ollama_message('{"answer": "hi"}'),
    )

    reply = await chat_with_tools(provider, [add_tool()])

    assert reply.content == '{"answer": "hi"}'
    assert reply.tool_calls == 0
    assert len(requests) == 2
    assert requests[1]["messages"] == MESSAGES


async def test_ollama_sends_a_tool_error_back_to_the_model():
    provider, requests = scripted_ollama(
        ollama_message(tool_calls=[("add", {"a": -1, "b": 3})]),
        ollama_message("ok"),
        ollama_message("{}"),
    )

    await chat_with_tools(provider, [add_tool()])

    assert requests[1]["messages"][-1]["content"] == "Error: a must not be negative"


@pytest.mark.parametrize(
    "name, arguments, expected",
    [
        ("launch_rocket", {}, "Error: Unknown tool: launch_rocket"),
        ("add", {"a": 1}, "Error: The arguments do not match the schema of add."),
        ("add", "not json", "Error: The arguments do not match the schema of add."),
        ("add", None, "Error: The arguments do not match the schema of add."),
    ],
)
async def test_ollama_reports_a_bad_tool_call_to_the_model(name, arguments, expected):
    # A local model gives no guarantee that its arguments match the schema.
    provider, requests = scripted_ollama(
        ollama_message(tool_calls=[(name, arguments)]),
        ollama_message("ok"),
        ollama_message("{}"),
    )

    await chat_with_tools(provider, [add_tool()])

    assert requests[1]["messages"][-1]["content"] == expected


async def test_ollama_accepts_tool_arguments_sent_as_a_json_string():
    provider, requests = scripted_ollama(
        ollama_message(tool_calls=[("add", '{"a": 4, "b": 4}')]),
        ollama_message("ok"),
        ollama_message("{}"),
    )

    await chat_with_tools(provider, [add_tool()])

    assert requests[1]["messages"][-1]["content"] == '{"sum": 8}'


async def test_ollama_stops_a_model_that_never_stops_calling_tools():
    endless = [ollama_message(tool_calls=[("add", {"a": 1, "b": 1})])] * 10
    provider, requests = scripted_ollama(*endless[:3], ollama_message("{}"))

    reply = await chat_with_tools(provider, [add_tool()])

    # Three tool rounds, then the final answer is asked for regardless.
    assert len(requests) == OllamaProvider.MAX_TOOL_ROUNDS + 1
    assert reply.tool_calls == 3
    assert "tools" not in requests[-1]


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        _build_provider("crystal-ball")


# --- LLMClient -------------------------------------------------------------


class ScriptedProvider:
    """A provider that returns or raises the queued results in order."""

    supports_tools = False

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
            "tools": [],
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


SOME_TOOL = Tool(
    name="noop", description="Does nothing.", input_schema={}, handler=lambda _: None
)


async def test_generate_offers_tools_to_a_provider_that_supports_them():
    provider = ScriptedProvider(
        ProviderReply(content="hi", input_tokens=1, output_tokens=1, tool_calls=2)
    )
    provider.supports_tools = True

    response = await client_for(provider).generate(MESSAGES, tools=[SOME_TOOL])

    assert provider.calls[0]["tools"] == [SOME_TOOL]
    assert response.tool_calls == 2


async def test_generate_does_not_offer_tools_to_a_provider_without_them():
    provider = ScriptedProvider(REPLY)

    response = await client_for(provider).generate(MESSAGES, tools=[SOME_TOOL])

    assert provider.calls[0]["tools"] == []
    assert response.tool_calls == 0


async def test_generate_logs_latency_and_token_usage(caplog):
    provider = ScriptedProvider(REPLY)

    with caplog.at_level(logging.INFO, logger="app.core.llm"):
        await client_for(provider).generate(MESSAGES)

    message = caplog.messages[-1]
    assert "model=test-model" in message
    assert "latency_ms=" in message
    assert "input_tokens=12" in message
    assert "output_tokens=7" in message
    assert "tool_calls=0" in message


async def test_generate_logs_each_failed_attempt(caplog):
    provider = ScriptedProvider(LLMRetryableError("timeout"), REPLY)

    with caplog.at_level(logging.WARNING, logger="app.core.llm"):
        await client_for(provider).generate(MESSAGES)

    assert "attempt=1/3" in caplog.messages[0]
    assert "error=timeout" in caplog.messages[0]
