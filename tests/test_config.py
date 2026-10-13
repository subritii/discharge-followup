from pathlib import Path

import pytest
from pydantic import ValidationError

from config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

ALL_VARS = [
    "ADAPTER",
    "SYNTHEA_DATA_DIR",
    "ATHENA_BASE_URL",
    "ATHENA_CLIENT_ID",
    "ATHENA_CLIENT_SECRET",
    "OLLAMA_BASE_URL",
    "AGENT_MODEL",
    "JUDGE_MODEL",
    "SF_USERNAME",
    "SF_PASSWORD",
    "SF_SECURITY_TOKEN",
    "SF_DOMAIN",
    "RETRY_DIR",
    "LOG_LEVEL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_loads_from_env_example() -> None:
    settings = Settings(_env_file=ENV_EXAMPLE)
    assert settings.adapter == "synthea"
    assert settings.agent_model == "llama3.1:8b"
    assert settings.judge_model == "qwen2.5:7b"
    assert settings.retry_dir == Path("data/retry")
    assert not settings.salesforce_configured


def test_missing_model_names_fail_loudly() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    errors = str(exc.value)
    assert "agent_model" in errors
    assert "judge_model" in errors


def test_athena_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAPTER", "athena")
    with pytest.raises(ValidationError, match="ATHENA_CLIENT_ID"):
        Settings(_env_file=ENV_EXAMPLE)


def test_athena_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAPTER", "athena")
    monkeypatch.setenv("ATHENA_BASE_URL", "https://api.preview.platform.athenahealth.com")
    monkeypatch.setenv("ATHENA_CLIENT_ID", "id")
    monkeypatch.setenv("ATHENA_CLIENT_SECRET", "s3cr3t-value")
    settings = Settings(_env_file=ENV_EXAMPLE)
    assert settings.adapter == "athena"
    assert settings.athena_client_secret is not None
    assert "s3cr3t-value" not in repr(settings)


def test_invalid_adapter_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAPTER", "epic")
    with pytest.raises(ValidationError):
        Settings(_env_file=ENV_EXAMPLE)


def test_salesforce_configured_when_all_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_USERNAME", "rn@example.dev")
    monkeypatch.setenv("SF_PASSWORD", "pw")
    monkeypatch.setenv("SF_SECURITY_TOKEN", "tok")
    assert Settings(_env_file=ENV_EXAMPLE).salesforce_configured
