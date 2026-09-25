from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Any standard Postgres URL works (Supabase, Railway, local Docker).
    database_url: str = "postgresql://postgres:postgres@localhost:5432/personal_api"

    # If set, every request (except /health) must send `X-API-Key: <value>`.
    api_key: str | None = None

    # File watcher (python -m app.watcher). Source name -> folder to index, as JSON, e.g.
    # WATCH_SOURCES='{"vault": "C:/Users/me/Obsidian", "project:api": "C:/code/api"}'
    watch_sources: dict[str, str] = {}
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"  # must produce 768-dim vectors (see 0002_knowledge_vectors.sql)

    @field_validator("api_key")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v or None

    @property
    def sqlalchemy_url(self) -> str:
        """Force the psycopg (v3) driver regardless of the URL scheme given."""
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def libpq_url(self) -> str:
        """Plain URL for direct psycopg connections (used by the migration runner)."""
        return self.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
