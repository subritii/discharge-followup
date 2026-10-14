"""Application settings, loaded from environment variables / .env."""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: a blank line like `SF_DOMAIN=` in .env falls back to the default.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # Data source
    adapter: Literal["synthea", "athena"] = "synthea"
    synthea_data_dir: Path = Path("data/synthea")
    athena_base_url: str | None = None
    athena_client_id: str | None = None
    athena_client_secret: SecretStr | None = None

    # Local LLMs (required: no defaults, so a missing value fails loudly)
    ollama_base_url: str = "http://localhost:11434"
    # Generous by default: on an 8 GB machine llama3.1:8b swaps and one draft can take
    # 15-20 minutes.
    ollama_timeout_seconds: float = Field(default=600, gt=0)
    agent_model: str
    judge_model: str

    # Salesforce (optional here; the Salesforce client checks them when used)
    # OAuth client credentials flow (External Client App / Connected App). SF_DOMAIN is
    # the org's My Domain without ".salesforce.com", e.g. "acme-dev-ed.develop.my"; the
    # client credentials flow does not work against login.salesforce.com.
    sf_username: str | None = None  # optional; the client credentials flow doesn't use it
    sf_client_id: str | None = None
    sf_client_secret: SecretStr | None = None
    sf_domain: str | None = None

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

    @field_validator("sf_domain")
    @classmethod
    def _normalize_sf_domain(cls, value: str | None) -> str | None:
        """Accept "https://x.my.salesforce.com/", "x.my.salesforce.com" or "x.my" -> "x.my"
        (simple-salesforce appends ".salesforce.com" itself)."""
        if value is None:
            return None
        value = value.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        return value.removesuffix(".salesforce.com") or None

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
        return bool(self.sf_client_id and self.sf_client_secret and self.sf_domain)


@lru_cache
def get_settings() -> Settings:
    return Settings()
