"""Policy shared by legacy authoring routes and their Agent/repository callers.

Publishing is intentionally not an editing operation. Only the dedicated
administrator publish workflow may expose a script to players.
"""

from collections.abc import Mapping


class LegacyPublicationDenied(ValueError):
    """An authoring operation attempted to bypass the publish workflow."""


def reject_legacy_publication(changes: Mapping[str, object]) -> None:
    """Reject publication before any database mutation, including Agent edits."""
    status = changes.get("status")
    status = getattr(status, "value", status)
    if str(status).upper() == "PUBLISHED" or changes.get("is_public"):
        raise LegacyPublicationDenied(
            "编辑接口不能发布或公开剧本，请使用管理员审核发布流程"
        )
