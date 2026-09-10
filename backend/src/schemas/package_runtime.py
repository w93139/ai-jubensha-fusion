"""A fixed-role opening preview, without gameplay or publication commands."""
from pydantic import Field

from src.schemas.script_package import PackageModel, StableId


class CreatePackageSessionRequest(PackageModel):
    release_id: int = Field(ge=1, le=2147483647)
    character_id: StableId
    idempotency_key: StableId
