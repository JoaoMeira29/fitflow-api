from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    DATABASE_URL: str
    JWT_SECRET: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Safety limits for calorie targets.
    MIN_DAILY_CALORIES_MALE: int = 1500
    MIN_DAILY_CALORIES_FEMALE: int = 1200
    MAX_WEEKLY_WEIGHT_CHANGE_PERCENT: float = 1.0

    # LLM used to generate plans. Every call goes through app/core/llm.py.
    # "ollama" is a local model; "anthropic" is hosted Claude.
    LLM_PROVIDER: Literal["ollama", "anthropic"] = "ollama"
    # Empty means the provider's default model (see DEFAULT_MODELS in llm.py).
    LLM_MODEL: str | None = None
    # Let the model call tools (the nutrition calculator). Off by default:
    # the small local model often fails to write a proper tool call.
    LLM_USE_TOOLS: bool = False
    # Only used by Ollama.
    LLM_BASE_URL: str = "http://localhost:11434"
    # Only used by Anthropic. SecretStr keeps it out of logs and error pages.
    ANTHROPIC_API_KEY: SecretStr | None = None
    LLM_TIMEOUT_SECONDS: float = 180
    # Extra attempts when the provider times out or is unreachable.
    LLM_MAX_RETRIES: int = 2


settings = Settings()
