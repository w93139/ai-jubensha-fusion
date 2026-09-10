"""Human publication decisions bind immutable evidence, never model claims."""
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.script_package import Digest, PackageModel, StableId
from src.schemas.script_review import ReviewText


class ModelFindingDisposition(PackageModel):
    job_id: int = Field(gt=0)
    finding_id: StableId
    status: Literal["ACKNOWLEDGED", "DISMISSED"]
    note: ReviewText


class ApprovePublicationRequest(PackageModel):
    idempotency_key: StableId
    expected_package_hash: Digest
    bundle_hash: Digest
    expected_basis_hash: Digest
    model_dispositions: list[ModelFindingDisposition] = Field(max_length=1000)
    note: ReviewText

    @model_validator(mode="after")
    def unique_dispositions(self) -> "ApprovePublicationRequest":
        keys = [(item.job_id, item.finding_id) for item in self.model_dispositions]
        if len(keys) != len(set(keys)):
            raise ValueError("model finding decisions must be unique")
        return self


class PublishPackageRequest(PackageModel):
    idempotency_key: StableId
    approval_id: int = Field(gt=0)
    expected_approval_hash: Digest
    expected_basis_hash: Digest
