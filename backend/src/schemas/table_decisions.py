"""Finite final vote plans; authority and live state live elsewhere."""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.schemas.package_primitives import PackageModel, StableId, References, FinaleAvailability


class AccusationIdentity(FinaleAvailability):
    id: StableId
    label: str = Field(min_length=1, max_length=100, pattern=r'\S')
    group_id: StableId
    sources: References


class FinaleVotePlan(PackageModel):
    schema_version: Literal['finale-vote-plan/1.0']
    character_ids: list[StableId] = Field(min_length=5, max_length=5)
    identities: list[AccusationIdentity] = Field(min_length=1, max_length=100)
    majority_threshold: Literal[3]
    sources: References

    @field_validator('majority_threshold', mode='before')
    @classmethod
    def strict_threshold(cls, value):
        if type(value) is not int:
            raise ValueError('FINALE_THRESHOLD_INVALID')
        return value

    @model_validator(mode='after')
    def identities_unique(self):
        if (len(set(self.character_ids)) != 5
                or len({i.id for i in self.identities}) != len(self.identities)):
            raise ValueError('FINALE_VOTE_IDENTITIES_INVALID')
        return self


class FinaleVote(PackageModel):
    accusation_id: StableId | None
    trust_character_id: StableId | None



class Seat(PackageModel):
    id: StableId


class InvestigationChoice(PackageModel):
    id: StableId
    order: int = Field(ge=0, le=10000)


class InvestigationVote(PackageModel):
    kind: Literal['CHOOSE', 'ABSTAIN', 'SKIP']
    choice_id: StableId | None

    @model_validator(mode='after')
    def choice_shape(self):
        if (self.kind == 'CHOOSE') != (self.choice_id is not None):
            raise ValueError('TABLE_VOTE_CHOICE_INVALID')
        return self

