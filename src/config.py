from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and a .env file."""

    database_path: str = "data/library.db"
    output_directory: str = "data/outputs"
    api_base_url: str = "https://openlibrary.org"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def load_settings() -> Settings:
    """Load settings once and reuse the cached instance."""
    return Settings()
