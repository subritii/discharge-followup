"""Pick the data adapter from config."""

from adapters.base import PatientDataAdapter
from adapters.synthea import SyntheaAdapter
from config import Settings


def get_adapter(settings: Settings) -> PatientDataAdapter:
    if settings.adapter == "synthea":
        return SyntheaAdapter(settings.synthea_data_dir)
    if settings.adapter == "athena":
        raise NotImplementedError("AthenaAdapter is built in Phase 11 (Milestone B)")
    raise ValueError(f"unknown adapter: {settings.adapter}")
