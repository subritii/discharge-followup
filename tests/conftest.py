from pathlib import Path

import pytest

from adapters.synthea import SyntheaAdapter
from privacy.redactor import Redactor

SYNTHEA_FIXTURES = Path(__file__).parent / "fixtures" / "synthea"
DISPOSITION_SYSTEM = "http://terminology.hl7.org/CodeSystem/discharge-disposition"

# Patient / encounter ids from tests/fixtures/synthea (see tests/fixtures/README.md).
INPATIENT_ID = "922fb35e-148d-9e82-7e65-bfa05e3b3515"
INPATIENT_IMP_ENCOUNTER = "922fb35e-148d-9e82-7d81-7ab53ed9279d"
ER_ID = "982750f4-569b-5949-196b-60699bdda3fb"
ER_EMER_ENCOUNTER = "982750f4-569b-5949-8fdc-8e669895b24d"
MED_CHANGES_ID = "ea33f67a-c1d2-1f17-720a-0f7ed938f253"
MED_CHANGES_EMER_ENCOUNTER = "ea33f67a-c1d2-1f17-af32-2873e6ef1b5f"
MED_CHANGES_IMP_ENCOUNTER = "ea33f67a-c1d2-1f17-008d-1f519af64a3c"
NO_HOSPITAL_ID = "aee7bbe1-0c45-c028-1e62-1f4cdb30c273"
HIGH_RISK_ID = "91c3a84d-ed05-266b-2681-0a62cb9ab6f5"
HIGH_RISK_PRIOR_ER = "91c3a84d-ed05-266b-c5ea-5bcd0d4c06cc"
HIGH_RISK_INDEX_ER = "91c3a84d-ed05-266b-4d87-85528531d6df"
HIGH_RISK_IMP_ENCOUNTER = "91c3a84d-ed05-266b-0a1e-ba0e976152b2"
HIGH_RISK_ER_2026_APR = "91c3a84d-ed05-266b-d706-fc7e7e4ea279"
HIGH_RISK_ER_2026_MAY = "91c3a84d-ed05-266b-2bd3-ad550ddf2c7d"


@pytest.fixture(scope="session")
def synthea() -> SyntheaAdapter:
    return SyntheaAdapter(SYNTHEA_FIXTURES)


@pytest.fixture(scope="session")
def redactor() -> Redactor:
    r = Redactor()
    r.analyze("warm up")  # load spaCy once per session
    return r
