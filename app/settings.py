"""All parameters and thresholds in one place (PLAN, "Parámetros iniciales").

Defaults live here; environment variables override them (same names, upper case).
Every eval run copies `Settings().model_dump()` into its run.json.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "local"  # "local" or "prod"

    # Paths
    corpus_dir: Path = ROOT / "corpus"
    data_dir: Path = ROOT / "data"

    # Database (Postgres + pgvector, same SQL locally and on Supabase)
    database_url: str = "postgresql://postgres@localhost:5432/arag"

    # Embeddings: same model everywhere (rule 4). Provider: "ollama", "openai_compat", "fake".
    embedding_model: str = "BAAI/bge-m3"  # canonical name stored with the index
    embedding_provider: str = "ollama"
    embedding_provider_model: str = "bge-m3"  # the provider's own id for the same model
    embedding_base_url: str = "http://localhost:11434"
    embedding_api_key: str = ""
    embedding_dim: int = 1024
    embedding_normalize: bool = True

    # LLM: free open-weight model, same locally and by API (rule 5). OpenAI-compatible client.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-20b"
    llm_api_key: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""
    llm_reasoning_effort: str = "low"
    llm_timeout_s: float = 30.0

    # Judge for evals: different family when possible, pinned id.
    judge_base_url: str = ""
    judge_model: str = ""
    judge_api_key: str = ""

    # Retrieval and context
    retrieval_k: int = 5
    retrieval_candidates: int = 20
    context_max_tokens: int = 2000
    output_max_tokens: int = 700
    chunk_min_words: int = 150
    chunk_max_words: int = 300
    chunk_overlap_words: int = 0
    chunk_max_tokens: int = 512
    chars_per_token: float = 3.0  # fallback token estimate when no tokenizer is available

    # Abstention: None = disabled until calibrated in Etapa 5
    abstain_min_similarity: float | None = None

    # Public API limits
    question_max_chars: int = 500
    rate_limit_per_minute: int = 5
    rate_limit_per_day: int = 30
    daily_token_budget: int = 120_000
    eval_key: str = ""  # header X-Eval-Key bypasses per-IP limits for eval runs
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Evals
    eval_max_usd: float = 1.0
    eval_overlap_min_lines: int = 3  # min overlap for a retrieved chunk to hit a gold span
    eval_overlap_tolerance_lines: int = 3  # gold span widened by this many lines each side
    bootstrap_resamples: int = 10_000
    bootstrap_seed: int = 1

    # Index version that the API serves (switching it is the rollback mechanism)
    active_index_version: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
