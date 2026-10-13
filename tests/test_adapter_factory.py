import pytest

from adapters.factory import get_adapter
from adapters.synthea import SyntheaAdapter
from config import Settings
from tests.conftest import SYNTHEA_FIXTURES

BASE = {"agent_model": "llama3.1:8b", "judge_model": "qwen2.5:7b", "_env_file": None}


def test_synthea_selected() -> None:
    adapter = get_adapter(Settings(adapter="synthea", synthea_data_dir=SYNTHEA_FIXTURES, **BASE))
    assert isinstance(adapter, SyntheaAdapter)


def test_athena_not_built_yet() -> None:
    settings = Settings(
        adapter="athena",
        athena_base_url="https://example.test",
        athena_client_id="id",
        athena_client_secret="secret",
        **BASE,
    )
    with pytest.raises(NotImplementedError, match="Phase 11"):
        get_adapter(settings)
