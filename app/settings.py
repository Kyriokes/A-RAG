"""All parameters and thresholds in one place (PLAN, "Parámetros iniciales").

Defaults live here; the files `.env` and then `.env.<ENV>` (e.g. `.env.prod` with
ENV=prod, which is how `make ... ENV=prod` points at production) override them, and
environment variables override both (same names, upper case).
Every eval run copies `Settings().model_dump()` into its run.json.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


def env_files(env: str | None = None, root: Path = ROOT) -> tuple[Path, ...]:
    """`.env`, then `.env.<ENV>`; a later file overrides an earlier one."""
    env = env if env is not None else os.environ.get("ENV", "local")
    return (root / ".env", root / f".env.{env}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_files(), env_file_encoding="utf-8", extra="ignore"
    )

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
    # Provider: "openai_compat" (Groq, Workers AI, Ollama /v1) or "fake" (scripted, no model).
    llm_provider: str = "openai_compat"
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
    # Bekker references in one question resolved by range (the first ones, duplicates
    # dropped); a question listing more is unusual, and each reference is one query.
    retrieval_max_bekker_refs: int = 5
    context_max_tokens: int = 2000
    output_max_tokens: int = 700
    chunk_min_words: int = 150
    chunk_max_words: int = 300
    chunk_overlap_words: int = 0
    chunk_max_tokens: int = 512
    chars_per_token: float = 3.0  # fallback token estimate when no tokenizer is available

    # Index and retrieval internals (Etapa 2)
    embedding_batch_size: int = 32  # texts per embedding request
    embedding_timeout_s: float = 60.0
    embedding_max_retries: int = 3  # retries on 429, 5xx and timeouts, with backoff
    # Lines per Bekker column assumed when interpolating across a column break. In the NE
    # Greek (perseus-grc2, markers every 5 lines) most columns are marked up to line 35.
    bekker_column_lines: int = 35
    # Two hits from different editions of one work count as the same location (and are
    # deduplicated) when their Bekker ranges share at least this many lines.
    dedup_min_overlap_lines: int = 3

    # make parity: Ollama vs the production embedding provider (PLAN, Etapa 2)
    parity_ollama_base_url: str = "http://localhost:11434"
    parity_ollama_model: str = "bge-m3"
    parity_prod_base_url: str = ""  # OpenAI-compatible base, e.g. Workers AI .../ai/v1
    parity_prod_model: str = "@cf/baai/bge-m3"
    parity_prod_api_key: str = ""
    parity_n_chunks: int = 50
    parity_n_queries: int = 20
    parity_top_k: int = 5
    parity_min_cosine: float = 0.99

    # Abstention: None = disabled until calibrated in Etapa 5
    abstain_min_similarity: float | None = None

    # Public API limits
    question_max_chars: int = 500
    search_max_k: int = 10  # largest k a client may ask POST /search for
    passages_max_context: int = 3  # largest neighbour window for GET /passages/{id}
    rate_limit_per_minute: int = 5
    rate_limit_per_day: int = 30
    # The per-IP limits count an IPv6 client by its network of this many bits (a client
    # usually controls a whole /64); IPv4 addresses count one by one.
    rate_limit_ipv6_prefix: int = 64
    # Key of the HMAC that identifies a client in rate_limit_daily and in the logs, so the
    # stored value cannot be mapped back to an address. Empty: a random key per process
    # (per-day counters then restart with the process; warned about in production).
    client_key_secret: str = ""
    daily_token_budget: int = 120_000
    eval_key: str = ""  # header X-Eval-Key bypasses per-IP limits for eval runs
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Deploy hardening (Etapa 7). For the rate limits, the budget and the cache TTL, a value
    # of 0 or less switches that mechanism off.
    # Reverse proxies allowed to set X-Forwarded-For: comma-separated IPs or networks, "*"
    # for any peer. Same variable and default as uvicorn's --forwarded-allow-ips.
    forwarded_allow_ips: str = "127.0.0.1,::1"
    answer_cache_ttl_days: int = 30  # exact answer cache in Postgres
    rate_limit_retention_days: int = 7  # per-day request counters are deleted after this
    log_level: str = "INFO"
    log_question_text: bool = False  # the per-query log keeps a hash of the question
    # POST /search has its own per-IP limits: each query embeds text with the provider
    # whose free quota /ask also needs (and the LLM fallback shares).
    search_rate_limit_per_minute: int = 20
    search_rate_limit_per_day: int = 300
    request_max_bytes: int = 16_384  # larger request bodies are refused (413) unread
    # /readyz calls the providers (one short embedding, GET /models of the LLM) at most
    # once per this many seconds, each call with this timeout.
    readyz_probe_ttl_s: float = 300.0
    readyz_probe_timeout_s: float = 10.0

    # Evals
    eval_max_usd: float = 1.0
    eval_overlap_min_lines: int = 3  # min overlap for a retrieved chunk to hit a gold span
    eval_overlap_tolerance_lines: int = 3  # gold span widened by this many lines each side
    bootstrap_resamples: int = 10_000
    bootstrap_seed: int = 1
    # Cost estimate before a run with LLM (PLAN, "Costos y topes"). USD per million tokens,
    # all "a confirmar" in the provider's console. The generator default is what the plan
    # implies for Groq gpt-oss-20b (1 USD ~ 2,600 answers of ~3,000 tokens).
    eval_llm_usd_per_mtok_in: float = 0.075  # a confirmar
    eval_llm_usd_per_mtok_out: float = 0.30  # a confirmar
    eval_judge_usd_per_mtok_in: float = 0.15  # a confirmar
    eval_judge_usd_per_mtok_out: float = 0.75  # a confirmar
    eval_prompt_overhead_tokens: int = 300  # generator system prompt + question, per call
    eval_judge_prompt_tokens: int = 500  # judge rubric + question + key points, per call
    eval_judge_max_tokens: int = 300  # judge output cap (strict JSON verdict + reason)
    eval_max_retries: int = 6  # retries on 429 for generator and judge calls
    eval_backoff_s: float = 2.0  # first wait; doubles on each retry
    eval_backoff_max_s: float = 120.0
    # Calibration (PLAN, Etapa 5)
    eval_abstain_max_false_refusal: float = 0.05  # share of answerable questions rejected
    eval_judge_min_agreement: float = 0.85
    eval_judge_sample_n: int = 50
    eval_judge_sample_min_fail: int = 15
    eval_judge_sample_min_cross_lang: int = 10

    # Corpus validation and the admission rule (PLAN, Etapa 1 and "Regla de admisión")
    corpus_min_bekker_coverage: float = 0.99  # share of a work's Bekker columns its units cover
    admission_open_licences: list[str] = Field(
        default_factory=lambda: ["CC BY-SA 4.0", "CC BY 4.0", "CC0 1.0"]
    )
    # US public domain reaches works published up to (current year - this): 1930 in 2026.
    admission_us_pd_lag_years: int = 96
    admission_death_years: int = 70  # ...and the translator died more than this many years ago

    # Index version that the API serves (switching it is the rollback mechanism)
    active_index_version: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
