"""The single entry point for every LLM call.

The rest of the app only knows `LLMClient.generate()`. It adds a timeout,
retries, and a log line with latency and token usage to every call. Each
provider is a small class that turns our request into that provider's API,
so changing provider means changing LLM_PROVIDER in the settings.

Two providers exist: Ollama (a local model, free) and Anthropic (hosted
Claude, paid). Both can let the model call tools.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import anthropic
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# A chat message: {"role": "system" | "user" | "assistant", "content": "..."}
Message = dict[str, str]

# The model used when LLM_MODEL is not set, for each provider.
DEFAULT_MODELS = {
    "ollama": "qwen2.5:7b",
    "anthropic": "claude-opus-5-5",
}


class LLMError(Exception):
    """The LLM could not be reached or returned an unusable response."""


class LLMRetryableError(LLMError):
    """A failure that may not happen again, such as a timeout."""


class ToolError(Exception):
    """Raised by a tool handler for a bad input. The message is sent back to
    the model so it can correct the call."""


@dataclass(frozen=True)
class Tool:
    """A function the model may call while it writes its answer. `handler`
    receives the arguments the model chose, as a dict matching `input_schema`,
    and returns something JSON can represent."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]


@dataclass(frozen=True)
class ProviderReply:
    content: str
    input_tokens: int | None
    output_tokens: int | None
    tool_calls: int = 0


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    tool_calls: int = 0


def run_tool(
    tools_by_name: dict[str, Tool], name: str, arguments: Any
) -> tuple[str, bool]:
    """Run one tool call. Returns (text for the model, whether it failed).
    A failure is not raised: the model is told what was wrong so it can call
    the tool again."""
    tool = tools_by_name.get(name)
    if tool is None:
        return f"Error: Unknown tool: {name}", True
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return json.dumps(tool.handler(arguments)), False
    except ToolError as error:
        return f"Error: {error}", True
    except (KeyError, TypeError, ValueError):
        # Not every provider guarantees the arguments match the schema.
        return f"Error: The arguments do not match the schema of {name}.", True


class LLMProvider(Protocol):
    # Whether the provider can let the model call tools.
    supports_tools: bool

    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        json_schema: dict | None,
        tools: list[Tool],
        timeout_seconds: float,
    ) -> ProviderReply: ...


class OllamaProvider:
    """Talks to a local Ollama server (https://ollama.com)."""

    supports_tools = True

    # Ollama's default context window is too small for the catalog lists.
    # The food list alone is about 8,000 tokens.
    CONTEXT_TOKENS = 16384
    # A low temperature keeps the output close to the instructions.
    TEMPERATURE = 0.2
    # The most rounds of tool calls allowed before the answer must come.
    MAX_TOOL_ROUNDS = 3
    # Output limit for one tool round. Without a schema to stop it, a small
    # model sometimes writes a tool call that never ends; this cuts it off.
    TOOL_ROUND_MAX_TOKENS = 1500

    def __init__(
        self, base_url: str, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._base_url = base_url
        # Tests pass a fake transport so no real HTTP request is made.
        self._transport = transport

    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        json_schema: dict | None,
        tools: list[Tool],
        timeout_seconds: float,
    ) -> ProviderReply:
        conversation: list[dict] = [dict(message) for message in messages]
        input_tokens = output_tokens = tool_calls = 0

        async def ask(max_tokens: int | None = None, **extra) -> dict:
            nonlocal input_tokens, output_tokens
            data = await self._post(
                {"model": model, "messages": conversation, **extra},
                timeout_seconds,
                max_tokens,
            )
            input_tokens += data.get("prompt_eval_count") or 0
            output_tokens += data.get("eval_count") or 0
            return data

        if tools:
            # When a request has both tools and a JSON schema, the model just
            # answers in the schema and never calls a tool. So the tool rounds
            # come first, without the schema, and the schema-constrained
            # answer is asked for at the end.
            tools_by_name = {tool.name: tool for tool in tools}
            tool_specs = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            for _ in range(self.MAX_TOOL_ROUNDS):
                message = (
                    await ask(self.TOOL_ROUND_MAX_TOKENS, tools=tool_specs)
                )["message"]
                calls = message.get("tool_calls") or []
                if not calls:
                    break
                tool_calls += len(calls)
                conversation.append(message)
                for call in calls:
                    name = call["function"]["name"]
                    content, _ = run_tool(
                        tools_by_name, name, call["function"].get("arguments")
                    )
                    conversation.append(
                        {"role": "tool", "tool_name": name, "content": content}
                    )

        # With `format`, Ollama constrains the output so it always matches
        # the schema.
        data = await ask(**({"format": json_schema} if json_schema else {}))
        return ProviderReply(
            content=data["message"]["content"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
        )

    async def _post(
        self, body: dict, timeout_seconds: float, max_tokens: int | None = None
    ) -> dict:
        """One request to Ollama, with HTTP problems turned into our errors."""
        options: dict = {
            "temperature": self.TEMPERATURE,
            "num_ctx": self.CONTEXT_TOKENS,
        }
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload = {**body, "stream": False, "options": options}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post("/api/chat", json=payload)
        except httpx.TimeoutException as error:
            raise LLMRetryableError("The request timed out") from error
        except httpx.TransportError as error:
            raise LLMRetryableError(f"Could not connect: {error}") from error

        if response.status_code == 429 or response.status_code >= 500:
            raise LLMRetryableError(f"HTTP {response.status_code}: {response.text}")
        if response.status_code >= 400:
            # For example 404 when the model has not been downloaded.
            raise LLMError(f"HTTP {response.status_code}: {response.text}")

        try:
            data = response.json()
            data["message"]["content"]
        except (ValueError, KeyError, TypeError) as error:
            raise LLMError("Unexpected response format") from error
        return data


class AnthropicProvider:
    """Talks to Claude through the official Anthropic SDK."""

    supports_tools = True

    MAX_OUTPUT_TOKENS = 16000
    # How much effort the model puts into thinking. Set explicitly because
    # the default differs between models.
    EFFORT = "medium"
    # The most rounds of tool calls allowed before the answer must come.
    MAX_TOOL_ROUNDS = 5

    # If the model's safety checks decline a request, these models can hand it
    # to another Claude model on the server instead of failing.
    FALLBACK_BETA = "server-side-fallback-2026-07-01"
    FALLBACK_MODELS = frozenset(
        {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
    )

    def __init__(self, api_key: str | None = None, client: Any = None):
        self._api_key = api_key
        # Tests pass a fake client so no real request is made.
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                # Without an explicit key the SDK looks for credentials in the
                # environment. Retries are ours, in LLMClient, so the SDK's
                # own are switched off.
                self._client = anthropic.AsyncAnthropic(
                    api_key=self._api_key, max_retries=0
                )
            except anthropic.AnthropicError as error:
                raise LLMError(f"Anthropic client could not start: {error}") from error
        return self._client

    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        json_schema: dict | None,
        tools: list[Tool],
        timeout_seconds: float,
    ) -> ProviderReply:
        # Claude takes the system prompt separately from the conversation.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        conversation: list[dict] = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] != "system"
        ]

        output_config: dict = {"effort": self.EFFORT}
        if json_schema is not None:
            # The API guarantees the final answer is JSON matching the schema.
            # transform_schema rewrites the constraints the API cannot enforce
            # (such as minimum and maxItems); our own validation covers those.
            output_config["format"] = {
                "type": "json_schema",
                "schema": anthropic.transform_schema(json_schema),
            }

        request: dict = {
            "model": model,
            "max_tokens": self.MAX_OUTPUT_TOKENS,
            "messages": conversation,
            "output_config": output_config,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    # The arguments are guaranteed to match the schema.
                    "strict": True,
                }
                for tool in tools
            ]
        if model in self.FALLBACK_MODELS:
            request["betas"] = [self.FALLBACK_BETA]
            request["fallbacks"] = "default"

        client = self._get_client().with_options(timeout=timeout_seconds)
        tools_by_name = {tool.name: tool for tool in tools}
        input_tokens = output_tokens = tool_calls = 0

        # The tool loop: each time the model asks for tools, run them, send
        # the results back and ask again, until it gives its final answer.
        for _ in range(self.MAX_TOOL_ROUNDS + 1):
            response = await self._create(client, request)
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens

            if response.stop_reason == "refusal":
                raise LLMError("The model declined the request")
            if response.stop_reason == "max_tokens":
                raise LLMError("The reply was cut off before it was complete")
            if response.stop_reason != "tool_use":
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            tool_calls += len(tool_uses)
            # The whole reply goes back unchanged, then every tool result in
            # one user message.
            conversation.append({"role": "assistant", "content": response.content})
            conversation.append(
                {
                    "role": "user",
                    "content": [
                        self._tool_result(tools_by_name, block) for block in tool_uses
                    ],
                }
            )
        else:
            raise LLMError(
                f"The model was still calling tools after {self.MAX_TOOL_ROUNDS} rounds"
            )

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("The reply had no text")
        return ProviderReply(
            content=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
        )

    @staticmethod
    async def _create(client: Any, request: dict) -> Any:
        """One API call, with the SDK's errors turned into ours."""
        try:
            return await client.beta.messages.create(**request)
        except anthropic.APITimeoutError as error:
            raise LLMRetryableError("The request timed out") from error
        except anthropic.APIConnectionError as error:
            raise LLMRetryableError(f"Could not connect: {error}") from error
        except anthropic.RateLimitError as error:
            raise LLMRetryableError(f"Rate limited: {error.message}") from error
        except anthropic.APIStatusError as error:
            message = f"HTTP {error.status_code}: {error.message}"
            if error.status_code >= 500:
                raise LLMRetryableError(message) from error
            # For example 401 for a bad API key or 404 for an unknown model.
            raise LLMError(message) from error

    @staticmethod
    def _tool_result(tools_by_name: dict[str, Tool], block: Any) -> dict:
        """Run one tool call and build the result block the API expects."""
        content, failed = run_tool(tools_by_name, block.name, block.input)
        result: dict = {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": content,
        }
        if failed:
            result["is_error"] = True
        return result


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float = 1.0,
        use_tools: bool = True,
    ):
        self.model = model
        self._use_tools = use_tools
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    @property
    def supports_tools(self) -> bool:
        """Whether tools will be offered: the provider must be able to, and
        LLM_USE_TOOLS must be on."""
        return self._use_tools and self._provider.supports_tools

    async def generate(
        self,
        messages: list[Message],
        json_schema: dict | None = None,
        tools: list[Tool] | None = None,
    ) -> LLMResponse:
        """Send the messages and return the model's reply. With `json_schema`,
        the provider is asked for JSON that matches it. `tools` are offered to
        the model when the provider supports them, and ignored otherwise.
        Raises LLMError when every attempt fails."""
        offered_tools = list(tools or []) if self.supports_tools else []
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                reply = await self._provider.chat(
                    model=self.model,
                    messages=messages,
                    json_schema=json_schema,
                    tools=offered_tools,
                    timeout_seconds=self._timeout_seconds,
                )
            except LLMError as error:
                logger.warning(
                    "LLM call failed model=%s attempt=%d/%d latency_ms=%d error=%s",
                    self.model,
                    attempt,
                    attempts,
                    _elapsed_ms(started),
                    error,
                )
                if not isinstance(error, LLMRetryableError) or attempt == attempts:
                    raise
                # Wait longer after each failure: 1 s, 2 s, 4 s, ...
                await asyncio.sleep(self._retry_delay_seconds * 2 ** (attempt - 1))
                continue

            latency_ms = _elapsed_ms(started)
            logger.info(
                "LLM call ok model=%s attempt=%d/%d latency_ms=%d "
                "input_tokens=%s output_tokens=%s tool_calls=%d",
                self.model,
                attempt,
                attempts,
                latency_ms,
                reply.input_tokens,
                reply.output_tokens,
                reply.tool_calls,
            )
            return LLMResponse(
                content=reply.content,
                model=self.model,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                latency_ms=latency_ms,
                tool_calls=reply.tool_calls,
            )
        raise AssertionError("unreachable")  # the loop always returns or raises


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _build_provider(name: str) -> LLMProvider:
    if name == "ollama":
        return OllamaProvider(settings.LLM_BASE_URL)
    if name == "anthropic":
        key = settings.ANTHROPIC_API_KEY
        return AnthropicProvider(api_key=key.get_secret_value() if key else None)
    raise ValueError(f"Unknown LLM_PROVIDER: {name!r}")


@lru_cache
def get_llm_client() -> LLMClient:
    """FastAPI dependency: the app's one LLM client, built from the settings.
    Tests replace it with a fake."""
    provider = _build_provider(settings.LLM_PROVIDER)
    return LLMClient(
        provider=provider,
        model=settings.LLM_MODEL or DEFAULT_MODELS[settings.LLM_PROVIDER],
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        max_retries=settings.LLM_MAX_RETRIES,
        use_tools=settings.LLM_USE_TOOLS,
    )
