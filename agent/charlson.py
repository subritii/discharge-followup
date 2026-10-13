"""Charlson comorbidity score from library/charlson_map.yaml (ILLUSTRATIVE mapping)."""

from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from models import CharlsonResult, Condition

DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent / "library" / "charlson_map.yaml"


class CharlsonCategory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    weight: int
    snomed: tuple[str, ...]
    supersedes: tuple[str, ...] = ()


class CharlsonMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    illustrative: bool
    review_status: str
    categories: dict[str, CharlsonCategory]

    @model_validator(mode="after")
    def _check(self) -> "CharlsonMap":
        for name, cat in self.categories.items():
            unknown = set(cat.supersedes) - set(self.categories)
            if unknown:
                raise ValueError(f"{name} supersedes unknown categories: {sorted(unknown)}")
        return self

    @property
    def code_to_category(self) -> dict[str, str]:
        return {code: name for name, cat in self.categories.items() for code in cat.snomed}

    def score(self, codes: Iterable[str]) -> CharlsonResult:
        lookup = self.code_to_category
        present = {lookup[c] for c in codes if c in lookup}
        superseded = {s for name in present for s in self.categories[name].supersedes}
        counted = sorted(present - superseded)
        return CharlsonResult(
            score=sum(self.categories[name].weight for name in counted),
            categories=tuple(counted),
        )


def load_charlson_map(path: Path = DEFAULT_MAP_PATH) -> CharlsonMap:
    return CharlsonMap.model_validate(yaml.safe_load(path.read_text()))


@lru_cache
def default_charlson_map() -> CharlsonMap:
    return load_charlson_map()


def active_during(condition: Condition, start: datetime, end: datetime) -> bool:
    """Condition known by `end` and not resolved before `start`."""
    if condition.onset is not None and condition.onset > end:
        return False
    return condition.abatement is None or condition.abatement >= start


def charlson_for_stay(
    conditions: Iterable[Condition],
    start: datetime,
    end: datetime,
    mapping: CharlsonMap | None = None,
) -> CharlsonResult:
    """Score SNOMED-coded conditions active at any point during [start, end]."""
    codes = (
        c.code.code
        for c in conditions
        if c.code.code and c.code.system in (None, "http://snomed.info/sct")
        if active_during(c, start, end)
    )
    return (mapping or default_charlson_map()).score(codes)
