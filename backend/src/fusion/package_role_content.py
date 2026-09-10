"""Resolve reviewed content by exact package and human role, without fallback.

Legacy registries contain one catalogue per package. New package versions may
register a role map; adding them must not replace an older package's entry.
This resolver does not migrate bindings or opt existing plays into new content.
"""
from collections.abc import Mapping
from typing import Protocol, TypeVar


class RoleContent(Protocol):
    package_hash: str
    character_id: str


Content = TypeVar('Content', bound=RoleContent)
RequiredRoles = str | set[str] | frozenset[str]


def resolve_role_content(registry: Mapping[str, Content | Mapping[str, Content]],
                         package_hash: str, character_id: str) -> Content | None:
    entry = registry.get(package_hash)
    catalogue = entry.get(character_id) if isinstance(entry, Mapping) else entry
    if (getattr(catalogue, 'package_hash', None) != package_hash
            or getattr(catalogue, 'character_id', None) != character_id):
        return None
    return catalogue


def role_content_required(registry: Mapping[str, RequiredRoles],
                          package_hash: str, character_id: str) -> bool:
    roles = registry.get(package_hash)
    if isinstance(roles, str):
        return roles == character_id
    return isinstance(roles, (set, frozenset)) and character_id in roles
