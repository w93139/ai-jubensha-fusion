"""Shared strict package values, with no database or runtime dependencies."""
from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

StableId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=40000, pattern=r"\S")]


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceReference(PackageModel):
    source_id: StableId
    page: int | None = Field(default=None, ge=1, le=100000)
    anchor: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"\S")


References = Annotated[list[SourceReference], Field(min_length=1, max_length=100)]


def normalize_heard_text(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', value) if unicodedata.category(c)[0] not in 'PZ')


class FinaleMaterialRef(PackageModel):
    collection: Literal['knowledge', 'evidence', 'memory']
    id: StableId


class FinaleAvailability(PackageModel):
    # Either an acquired-material clause OR an actually heard authored term.
    # These admit a hypothesis, never prove it or grant a source material.
    available_when: list[list[FinaleMaterialRef]] = Field(default_factory=list, max_length=100)
    heard_terms: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def permissions_valid(self):
        if any(not clause or len(clause) > 30 or len({(r.collection, r.id) for r in clause}) != len(clause)
               for clause in self.available_when):
            raise ValueError('FINALE_OPTION_PERMISSION_INVALID')
        terms = [normalize_heard_text(t) for t in self.heard_terms]
        if (any(not t or not t.strip() for t in terms) or len(set(terms)) != len(terms)
                or any(unicodedata.category(c).startswith('C') for t in self.heard_terms for c in t)):
            raise ValueError('FINALE_HEARD_TERM_INVALID')
        return self
