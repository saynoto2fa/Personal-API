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
    # Folder at the top of each source that holds retired notes. It is not indexed, and items in
    # it go to the Recycle Bin after this many days there (0 = keep it out of search, never clear it).
    archive_folder: str = "OUTDATED"
    archive_retention_days: int = 30

    # Where the MCP server (python -m app.mcp_server) reaches this API.
    api_url: str = "http://127.0.0.1:8000"

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
