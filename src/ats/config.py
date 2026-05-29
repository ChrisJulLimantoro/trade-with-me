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


settings = Settings()
