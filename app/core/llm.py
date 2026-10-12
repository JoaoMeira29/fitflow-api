"""The single entry point for every LLM call.

The rest of the app only knows `LLMClient.generate()`. It adds a timeout,
retries, and a log line with latency and token usage to every call. Each
provider is a small class that turns our request into that provider's HTTP
API, so changing provider means adding one class and changing LLM_PROVIDER.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# A chat message: {"role": "system" | "user" | "assistant", "content": "..."}
Message = dict[str, str]


class LLMError(Exception):
    """The LLM could not be reached or returned an unusable response."""


class LLMRetryableError(LLMError):
    """A failure that may not happen again, such as a timeout."""


@dataclass(frozen=True)
class ProviderReply:
    content: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class LLMProvider(Protocol):
    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        json_schema: dict | None,
        timeout_seconds: float,
    ) -> ProviderReply: ...


class OllamaProvider:
    """Talks to a local Ollama server (https://ollama.com)."""

    # Ollama's default context window is too small for the catalog lists.
    # The food list alone is about 8,000 tokens.
    CONTEXT_TOKENS = 16384
    # A low temperature keeps the output close to the instructions.
    TEMPERATURE = 0.2

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
        timeout_seconds: float,
    ) -> ProviderReply:
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.TEMPERATURE,
                "num_ctx": self.CONTEXT_TOKENS,
            },
        }
        if json_schema is not None:
            # Ollama constrains the output so it always matches this schema.
            payload["format"] = json_schema

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
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as error:
            raise LLMError("Unexpected response format") from error

        return ProviderReply(
            content=content,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float = 1.0,
    ):
        self.model = model
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    async def generate(
        self, messages: list[Message], json_schema: dict | None = None
    ) -> LLMResponse:
        """Send the messages and return the model's reply. With `json_schema`,
        the provider is asked for JSON that matches it. Raises LLMError when
        every attempt fails."""
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                reply = await self._provider.chat(
                    model=self.model,
                    messages=messages,
                    json_schema=json_schema,
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
                "input_tokens=%s output_tokens=%s",
                self.model,
                attempt,
                attempts,
                latency_ms,
                reply.input_tokens,
                reply.output_tokens,
            )
            return LLMResponse(
                content=reply.content,
                model=self.model,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                latency_ms=latency_ms,
            )
        raise AssertionError("unreachable")  # the loop always returns or raises


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _build_provider(name: str) -> LLMProvider:
    if name == "ollama":
        return OllamaProvider(settings.LLM_BASE_URL)
    raise ValueError(f"Unknown LLM_PROVIDER: {name!r}")


@lru_cache
def get_llm_client() -> LLMClient:
    """FastAPI dependency: the app's one LLM client, built from the settings.
    Tests replace it with a fake."""
    return LLMClient(
        provider=_build_provider(settings.LLM_PROVIDER),
        model=settings.LLM_MODEL,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        max_retries=settings.LLM_MAX_RETRIES,
    )
