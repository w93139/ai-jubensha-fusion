"""Explicit requests for a separate deterministic rules preview."""
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_serializer, model_validator

from src.schemas.script_package import PackageModel, StableId


class CreatePackageFlowRequest(PackageModel):
    opening_session_id: str = Field(pattern=r"^package-[0-9a-f]{32}$", min_length=40, max_length=40)
    idempotency_key: StableId


class PackageFlowTarget(PackageModel):
    collection: Literal["knowledge", "evidence"]
    id: StableId


class PackageFlowActionRequest(PackageModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [{
        "if": {"properties": {"action": {"const": "SHARE_MATERIAL"}}},
        "then": {"required": ["target"], "properties": {"target": {"type": "object"}}},
        "else": {"not": {"required": ["target"]}},
    }]})

    idempotency_key: StableId
    expected_revision: int = Field(ge=0)
    action: Literal["ADVANCE_PHASE", "SHARE_MATERIAL"]
    target: PackageFlowTarget | None = None

    @model_validator(mode="after")
    def action_target(self) -> "PackageFlowActionRequest":
        if ((self.action == "ADVANCE_PHASE" and "target" in self.model_fields_set)
                or (self.action == "SHARE_MATERIAL" and self.target is None)):
            raise ValueError("invalid action target")
        return self

    @model_serializer(mode="wrap")
    def serialize_action(self, handler: Any) -> dict:
        result = handler(self)
        if self.action == "ADVANCE_PHASE":
            # A legal advance must stay legal after storage/revalidation;
            # never introduce the explicitly forbidden null target default.
            result.pop("target", None)
        return result
