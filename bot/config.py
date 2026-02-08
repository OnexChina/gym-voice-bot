"""Загрузка переменных окружения и настройки приложения."""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения"""

    # Telegram
    telegram_bot_token: str

    # OpenAI
    openai_api_key: str

    # Database
    database_url: str

    # Опциональные настройки
    log_level: str = "INFO"
    whisper_model: str = "whisper-1"
    gpt_model: str = "gpt-4o-mini"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def database_url_sync(self) -> str:
        """URL для Alembic (sync): postgresql:// вместо postgresql+asyncpg://."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# Глобальный экземпляр настроек
settings = Settings()
