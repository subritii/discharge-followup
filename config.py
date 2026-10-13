"""Application settings, loaded from environment variables / .env."""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # Discharge detection: an IMP that starts within this many hours after an ER visit
    # ends (or overlaps it) is treated as the same episode.
    er_admit_merge_window_hours: float = Field(default=6.0, ge=0)

    # Deadlines: business days are counted in the practice's local time zone, skipping
    # weekends and the dates in holidays_file.
    practice_timezone: str = "America/Chicago"
    holidays_file: Path = Path("library/holidays.yaml")

    # Pipeline
    retry_dir: Path = Path("data/retry")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("practice_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown IANA time zone: {value!r}") from None
        return value

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
    def er_admit_merge_window(self) -> timedelta:
        return timedelta(hours=self.er_admit_merge_window_hours)

    @property
    def salesforce_configured(self) -> bool:
        return bool(self.sf_username and self.sf_password and self.sf_security_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
