"""Build an isolated H catalogue registration without touching prior entries.

This prepares service keyword arguments only. It does not publish a package,
open a database, migrate a play, or alter an existing catalogue registration.
"""
from collections.abc import Mapping

from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import content_hash, validate_package


def register_h_content(package, document, *, previous_package,
                       single_player_content=None, single_player_required=None):
    """Return independent outer registries for a new, version-only H package."""
    if (not validate_package(package)['valid']
            or not validate_package(previous_package)['valid']):
        raise ValueError('H_REGISTRATION_PACKAGE_INVALID')
    package_hash = content_hash(package)
    if (package['content_version'] == previous_package['content_version']
            or package_hash == content_hash(previous_package)):
        raise ValueError('H_REGISTRATION_NEW_VERSION_REQUIRED')
    if ({key: value for key, value in package.items() if key != 'content_version'}
            != {key: value for key, value in previous_package.items() if key != 'content_version'}):
        raise ValueError('H_REGISTRATION_PACKAGE_CONTENT_CHANGED')
    if (not isinstance(document, dict) or document.get('selected_character_id') != 'H'
            or document.get('package_hash') != package_hash
            or document.get('source_package_hash') != package_hash):
        raise ValueError('H_REGISTRATION_DOCUMENT_BINDING_INVALID')
    for registry in (single_player_content, single_player_required):
        if registry is not None and not isinstance(registry, Mapping):
            raise ValueError('H_REGISTRATION_REGISTRY_INVALID')
        if registry is not None and package_hash in registry:
            raise ValueError('H_REGISTRATION_ALREADY_EXISTS')
    catalogue = SinglePlayerContent(package, document)
    contents = dict(single_player_content) if single_player_content is not None else {}
    required = dict(single_player_required) if single_player_required is not None else {}
    contents[package_hash] = {'H': catalogue}
    required[package_hash] = {'H'}
    return {'single_player_content': contents, 'single_player_required': required}
