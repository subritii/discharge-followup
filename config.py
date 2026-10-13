"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Data source
    adapter: Literal["synthea", "athena"] = "synthea"
    synthea_data_dir: Path = Path("data/synthea")
    athena_base_url: str | None = None
    athena_client_id: str | None = None
    athena_client_secret: SecretStr | None = None

    # Local LLMs (required: no defaults, so a missing value fails loudly)
    ollama_base_url: str = "http://localhost:11434"
    agent_model: str
    judge_model: str

    # Salesforce (optional here; the Salesforce client checks them when used)
    sf_username: str | None = None
    sf_password: SecretStr | None = None
    sf_security_token: SecretStr | None = None
    sf_domain: Literal["login", "test"] = "login"

    # Pipeline
    retry_dir: Path = Path("data/retry")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def _require_athena_credentials(self) -> "Settings":
        if self.adapter != "athena":
            return self
        missing = [
            name
            for name in ("athena_base_url", "athena_client_id", "athena_client_secret")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"ADAPTER=athena requires: {', '.join(m.upper() for m in missing)}")
        return self

    @property
    def salesforce_configured(self) -> bool:
        return bool(self.sf_username and self.sf_password and self.sf_security_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
