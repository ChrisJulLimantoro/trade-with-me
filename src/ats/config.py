from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://ats:ats@localhost:5432/ats"
    x_bearer_token: str | None = None
    log_level: str = "INFO"
    log_render: Literal["rich", "json"] = "rich"
    seed: int = 42

    # --- LLM-plan trading layer (POC, see LLM_BASED_ARCHITECTURE.md) ---
    # OpenAI client. Default to mock so the full pipeline + tests run with no key.
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # e.g. https://openrouter.ai/api/v1
    llm_mock: bool = True
    llm_plan_model: str = "gpt-4o"  # create_plan — heavier reasoning
    llm_confirm_model: str = "gpt-4o-mini"  # confirm_setup — cheap/fast
    llm_max_tokens: int = 2048

    # --- Engine / planning / risk knobs ---
    plan_refresh_bars: int = 16  # replay: refresh the plan every N feature bars (~4h on 15m)
    soft_threshold: float = 0.5  # min weighted soft-rule score to "detect" a setup
    max_position_pct: float = 0.10  # risk: max size_pct per trade
    min_rr: float = 1.5  # risk: min reward:risk
    paper_equity_usd: float = 10_000.0


settings = Settings()
