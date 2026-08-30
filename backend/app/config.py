"""Central configuration. One `LLM_PROVIDER` env var switches the whole app
between cloud (OpenAI) and local (Ollama) without any code change."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # App
    app_env: str = "local"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:8080,http://localhost:5174"

    # Provider toggle
    llm_provider: str = "local"      # cloud | local
    agent_backend: str = "agent_sdk"  # agent_sdk | litellm

    # Cloud (OpenAI)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Local (Ollama)
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"

    # Embeddings. `embed_provider` picks where they run:
    #   ollama -> nomic-embed-text (768 dims), fully local, no key
    #   openai -> text-embedding-3-small (1536 dims), pure-cloud, no Ollama needed
    # The dim is fixed per deployment (it defines the pgvector column), so switching
    # providers requires a matching embed_dim + a fresh ingest.
    embed_provider: str = "ollama"   # ollama | openai
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # LiteLLM proxy (Anthropic passthrough the Agent SDK targets)
    litellm_proxy_url: str = "http://litellm:4000"
    litellm_master_key: str = "sk-lenny-local-dev"

    # Database
    database_url: str = "postgresql://lenny:lenny@db:5432/lenny"

    # Retrieval
    retrieval_top_k: int = 6
    retrieval_min_score: float = 0.25

    # Ingestion
    transcripts_repo: str = "https://github.com/ChatPRD/lennys-podcast-transcripts.git"
    ingest_max_episodes: int = 50
    # If true, populate the knowledge base on startup when it's empty (handy on a
    # PaaS like Railway where running a one-off ingest command is awkward).
    auto_ingest: bool = False

    # ---- derived helpers ----------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_cloud(self) -> bool:
        return self.llm_provider.lower() == "cloud"

    @property
    def proxy_model_name(self) -> str:
        """Model name the LiteLLM proxy routes on (see litellm/config.yaml)."""
        return "lenny-cloud" if self.is_cloud else "lenny-local"

    @property
    def direct_model_name(self) -> str:
        """Model string for the direct-LiteLLM fallback path."""
        return f"openai/{self.openai_model}" if self.is_cloud else f"ollama_chat/{self.ollama_model}"

    @property
    def active_model_label(self) -> str:
        return (
            f"OpenAI · {self.openai_model}" if self.is_cloud else f"Ollama · {self.ollama_model}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
