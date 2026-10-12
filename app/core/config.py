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


settings = Settings()
