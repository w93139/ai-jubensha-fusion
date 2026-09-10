"""Fictional H registrations retain old objects and reject ambiguous bindings."""
import asyncio
from copy import deepcopy

import pytest

from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.package_h_registration import register_h_content
from src.fusion.package_play import PackagePlayService, PackagePlayError
from src.fusion.package_role_content import resolve_role_content
from src.fusion.package_runtime import PackageRuntimeService, PackageRuntimeError
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_package_guided_flow import enter
from tests.fusion_security.test_package_play_store import play, ask_body
from tests.fusion_security.test_package_runtime import runtime, publish, request
from tests.fusion_security.test_single_player_topics import document, ask_topic


def fixture():
    def rename(value):
        if isinstance(value, dict):
            return {key: rename(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rename(item) for item in value]
        return 'H' if value == 'a' else value
    previous = rename(full_package())
    previous['knowledge'][1]['retelling'] = 'MAY_RETELL'
    package = deepcopy(previous)
    package['content_version'] = 'h-offline-v2'
    doc = document(package)
    doc['selected_character_id'] = 'H'
    doc['source_package_hash'] = content_hash(package)
    return previous, package, doc


def test_h_registration_preserves_input_packages_and_prior_entry_identity():
    previous, package, doc = fixture()
    old_legacy = object()
    old_nested = {'T': object(), 'J': object()}
    old_required = {'T', 'J'}
    contents = {'legacy-package': old_legacy, 'tj-package': old_nested}
    required = {'legacy-package': 'T', 'tj-package': old_required}
    inputs = canonical_json([previous, package, doc])
    result = register_h_content(package, doc, previous_package=previous,
        single_player_content=contents, single_player_required=required)
    key = content_hash(package)
    assert result['single_player_content'] is not contents
    assert result['single_player_required'] is not required
    assert contents == {'legacy-package': old_legacy, 'tj-package': old_nested}
    assert required == {'legacy-package': 'T', 'tj-package': old_required}
    for old_key in contents:
        assert result['single_player_content'][old_key] is contents[old_key]
        assert result['single_player_required'][old_key] is required[old_key]
    assert result['single_player_content'][key].keys() == {'H'}
    assert result['single_player_required'][key] == {'H'}
    assert (key, 'H') not in result['single_player_content']
    catalogue = resolve_role_content(result['single_player_content'], key, 'H')
    assert catalogue.character_id == 'H' and catalogue.package_hash == key
    assert resolve_role_content(result['single_player_content'], key, 'b') is None
    assert canonical_json([previous, package, doc]) == inputs
    # The constructed catalogue freezes nested authoring content as well.
    doc['phase_guides'][0]['instructions'][0] = 'later-authoring-change'
    assert 'later-authoring-change' not in catalogue.stages[package['initial_phase_id']]['instructions']


def test_h_registration_opens_private_book_and_exposes_only_current_other_role_options(play):
    previous, package, doc = fixture()
    kwargs = register_h_content(package, doc, previous_package=previous)
    runtime_service = PackageRuntimeService(play.db, play.publisher, **kwargs)
    play.play = PackagePlayService(play.db, play.publisher, play.model, play.policy,
                                   lambda: play.clock[0], **kwargs)
    release = publish(play, package)
    opening = runtime_service.create(request(release['id'], 'H'), 1)
    assert opening['selected_character_id'] == 'H'
    assert opening['private_knowledge'][0]['text'] == doc['operation_rules']
    assert all(item['id'] == 'initial-a' for item in opening['private_knowledge'])
    view = play.play.create({'opening_session_id': opening['session_id'], 'idempotency_key': 'start-h'}, 1)
    assert view['single_player']['topics'] == []
    assert set(kwargs['single_player_content'][content_hash(package)]['H'].stages) == {p['id'] for p in package['phases']}
    view = enter(play, view)
    topics = view['single_player']['topics']
    assert len(topics) == 1 and topics[0]['id'] == 'scene'
    assert [r['character_id'] for r in topics[0]['responders']] == ['b']
    assert [i['available'] for i in topics[0]['responders'][0]['intents']] == [True, False, False]
    _, view = ask_topic(play, view)
    assert view['single_player']['turns'][-1]['character_id'] == 'b'
    assert view['single_player']['turns'][-1]['status'] == 'READY'
    play.db.commit()
    restored = PackagePlayService(play.db, play.publisher, play.model, play.policy,
                                 lambda: play.clock[0], **kwargs)
    assert restored.get(view['play_id'], 1) == view
    assert play.sdk.chat_completion.await_count == 0


def test_registered_h_without_its_catalogue_fails_closed_in_opening_and_play(play):
    previous, package, doc = fixture()
    kwargs = register_h_content(package, doc, previous_package=previous)
    release = publish(play, package)
    opening_service = PackageRuntimeService(play.db, play.publisher, **kwargs)
    opening = opening_service.create(request(release['id'], 'H'), 1)
    service = PackagePlayService(play.db, play.publisher, play.model, play.policy, **kwargs)
    view = service.create({'opening_session_id': opening['session_id'], 'idempotency_key': 'start-h'}, 1)
    kwargs['single_player_content'].clear()
    missing = PackageRuntimeService(play.db, play.publisher, **kwargs)
    with pytest.raises(PackageRuntimeError, match='PACKAGE_OPENING_SINGLE_PLAYER_UNAVAILABLE'):
        missing.get(opening['session_id'], 1)
    count = play.db.query(ScriptPackagePlaySession).count()
    with pytest.raises(PackageRuntimeError, match='PACKAGE_OPENING_SINGLE_PLAYER_UNAVAILABLE'):
        missing.create(request(release['id'], 'H', 'second-h'), 1)
    assert play.db.query(ScriptPackagePlaySession).count() == count
    play.play = PackagePlayService(play.db, play.publisher, play.model, play.policy, **kwargs)
    view = enter(play, view)
    assert view['single_player']['available'] is False and view['single_player']['topics'] == []
    with pytest.raises(PackagePlayError, match='SINGLE_TOPIC_REQUIRED'):
        asyncio.run(play.play.ask(view['play_id'], ask_body(view['revision']), 1))
    assert play.sdk.chat_completion.await_count == 0


@pytest.mark.parametrize('target', ['content', 'required', 'both'])
def test_h_registration_refuses_any_existing_target_key_without_overwriting(target):
    previous, package, doc = fixture()
    key = content_hash(package)
    contents = {key: {}} if target in ('content', 'both') else {}
    required = {key: set()} if target in ('required', 'both') else {}
    old_contents, old_required = dict(contents), dict(required)
    with pytest.raises(ValueError, match='H_REGISTRATION_ALREADY_EXISTS'):
        register_h_content(package, doc, previous_package=previous,
                           single_player_content=contents, single_player_required=required)
    assert contents == old_contents and required == old_required
    for name in contents:
        assert contents[name] is old_contents[name]
    for name in required:
        assert required[name] is old_required[name]


@pytest.mark.parametrize('mutation', [
    'wrong-role', 'missing-role', 'wrong-hash', 'missing-source-hash', 'previous-source-hash',
    'invalid-material-digest', 'unknown-source-ref', 'wrong-source-sha', 'human-responder', 'missing-phase',
])
def test_h_registration_rejects_invalid_directory_binding_or_content(mutation):
    previous, package, doc = fixture()
    basis = doc['topics'][0]['responders'][0]['basis'][0]
    if mutation == 'wrong-role': doc['selected_character_id'] = 'b'
    elif mutation == 'missing-role': del doc['selected_character_id']
    elif mutation == 'wrong-hash': doc['package_hash'] = '0' * 64
    elif mutation == 'missing-source-hash': del doc['source_package_hash']
    elif mutation == 'previous-source-hash': doc['source_package_hash'] = content_hash(previous)
    elif mutation == 'invalid-material-digest': basis['text_sha256'] = '0' * 64
    elif mutation == 'unknown-source-ref': basis['source_refs'] = [{'source_id': 'missing', 'anchor': 'L1'}]
    elif mutation == 'wrong-source-sha': basis['source_refs'] = [{'source_id': 'fiction', 'sha256': '0' * 64}]
    elif mutation == 'human-responder': doc['topics'][0]['responders'][0]['character_id'] = 'H'
    elif mutation == 'missing-phase': doc['phase_guides'].pop()
    with pytest.raises(ValueError):
        register_h_content(package, doc, previous_package=previous)


@pytest.mark.parametrize('mutation', ['same-version', 'changed-fact', 'changed-source', 'invalid-new', 'invalid-previous'])
def test_h_registration_requires_valid_packages_with_only_new_content_version(mutation):
    previous, package, doc = fixture()
    if mutation == 'same-version': package['content_version'] = previous['content_version']
    elif mutation == 'changed-fact': package['knowledge'][0]['text'] = 'Another fictional fact.'
    elif mutation == 'changed-source': package['sources'][0]['sha256'] = '2' * 64
    elif mutation == 'invalid-new': package['player_count'] = 4
    elif mutation == 'invalid-previous': previous['player_count'] = 4
    doc['package_hash'] = doc['source_package_hash'] = content_hash(package)
    with pytest.raises(ValueError, match='H_REGISTRATION_'):
        register_h_content(package, doc, previous_package=previous)


@pytest.mark.parametrize('registry', [[], 'not-a-registry', 1])
def test_h_registration_rejects_non_mapping_registries(registry):
    previous, package, doc = fixture()
    assert validate_package(package)['valid'] and validate_package(previous)['valid']
    for keyword in ('single_player_content', 'single_player_required'):
        with pytest.raises(ValueError, match='H_REGISTRATION_REGISTRY_INVALID'):
            register_h_content(package, doc, previous_package=previous, **{keyword: registry})
