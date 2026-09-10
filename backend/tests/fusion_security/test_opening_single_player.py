"""One opening/manual projection, with fictional snapshots and no model calls."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.package_import import PackageImportService
from src.fusion.package_runtime import PackageRuntimeError, PackageRuntimeService
from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from tests.fusion_security.test_full_play_images import image_package
from tests.fusion_security.test_package_full_play import full_package, REFS
from tests.fusion_security.test_package_play_store import play, start, events
from tests.fusion_security.test_package_runtime import runtime, publish, request


def manual_package():
    package = full_package()
    for identifier in ('old-rules-one', 'old-rules-two'):
        package['knowledge'].append({'id': identifier, 'text': 'OLD_COLLECTIVE_RULE_' + identifier,
            'kind': 'FACT', 'visibility': 'PUBLIC', 'character_id': None, 'disclosure': 'PUBLIC',
            'release': {'phase_id': package['initial_phase_id']}, 'sources': deepcopy(REFS)})
    assert validate_package(package)['valid']
    return package


def catalogue(package, role='a', replacements=None):
    document = {'schema_version': 'single-player-content/1.0', 'package_hash': content_hash(package),
        'selected_character_id': role, 'operation_rules': '## 本局单人操作\n由你直接选择调查地点。终局各自封卷。',
        'replaces_materials': [{'collection': 'knowledge', 'id': identifier}
            for identifier in (replacements or ['old-rules-one', 'old-rules-two'])],
        'phase_guides': [{'phase_id': phase['id'], 'goal': 'PRIVATE_GUIDE_NOT_FOR_OPENING',
            'instructions': ['PRIVATE_STAGE_INSTRUCTION'], 'completion': 'PRIVATE_STAGE_COMPLETION',
            'source_refs': deepcopy(REFS)} for phase in package['phases']],
        'topics': []}
    return SinglePlayerContent(package, document)


def adapted(runtime, package, content=None, **kwargs):
    return PackageRuntimeService(runtime.db, runtime.publisher,
        single_player_content={content_hash(package): content or catalogue(package)}, **kwargs)


def test_opening_single_player_same_manual_as_play_without_rebinding_or_mutating_source(play):
    package = manual_package()
    original_package = canonical_json(package)
    plan = catalogue(package)
    play.play.single_player_content = {content_hash(package): plan}
    old_opening, playing = start(play, package)
    assert 'OLD_COLLECTIVE_RULE' in canonical_json(old_opening)
    row = play.db.query(ScriptPackagePlaySession).one()
    original_binding = (row.binding_json, row.binding_hash, row.request_hash)
    original_events = [(item.event_json, item.request_json, item.state_hash) for item in events(play)]
    service = adapted(play, package, plan)
    opening = service.get(old_opening['session_id'], 1)
    for view in (opening, playing):
        assert [item['text'] for item in view['public_knowledge']] == [plan.rules]
        reading = view['public_knowledge'] + view['private_knowledge']
        assert canonical_json(reading).count(plan.rules.splitlines()[-1]) == 1
        assert 'OLD_COLLECTIVE_RULE' not in canonical_json(view)
    for field in ('session_id', 'release_id', 'version_id', 'package_hash', 'selected_character_id'):
        assert opening[field] == old_opening[field]
    assert opening['private_knowledge'] == old_opening['private_knowledge']
    assert (row.binding_json, row.binding_hash, row.request_hash) == original_binding
    assert [(item.event_json, item.request_json, item.state_hash) for item in events(play)] == original_events
    assert canonical_json(service.resolve_binding(row.session_id, 1)[1]) == original_package
    assert canonical_json(PackageImportService(play.db).get_version(row.version_id)['package']) == original_package
    assert service.get(row.session_id, 1) == opening
    assert service.create(request(), 1) == opening
    assert play.sdk.chat_completion.await_count == 0


def test_opening_single_player_does_not_reveal_other_books_memories_future_guides_or_source_fields(runtime):
    package = manual_package(); publish(runtime, package)
    view = adapted(runtime, package).create(request(), 1)
    raw = canonical_json(view)
    for forbidden in ('PRIVATE_BOOK_b', 'PRIVATE_BOOK_c', 'PRIVATE_BOOK_d', 'PRIVATE_BOOK_e',
                      'PRIVATE_MEMORY', 'SYSTEM_TRUTH', 'PRIVATE_GUIDE_NOT_FOR_OPENING',
                      'PRIVATE_STAGE_INSTRUCTION', 'PRIVATE_STAGE_COMPLETION', 'source_refs',
                      'relative_path', 'replaces_materials', 'single_player', 'source_package_hash'):
        assert forbidden not in raw
    assert 'PRIVATE_BOOK_a' in raw
    assert view['available_actions'] == [] and view['supports_rules_preview'] is False
    with pytest.raises(PackageRuntimeError) as caught:
        adapted(runtime, package).get(view['session_id'], 2)
    assert caught.value.status_code == 404


def test_opening_single_player_unconfigured_packages_and_other_roles_keep_default_projection(runtime):
    package = manual_package(); publish(runtime, package)
    baseline_a = runtime.service.create(request(), 1)
    baseline_b = runtime.service.create(request(character='b', key='other-role'), 1)
    empty = PackageRuntimeService(runtime.db, runtime.publisher, single_player_content={}, single_player_required={})
    assert empty.get(baseline_a['session_id'], 1) == baseline_a
    service = adapted(runtime, package, single_player_required={content_hash(package): 'a'})
    assert service.get(baseline_b['session_id'], 1) == baseline_b
    unrelated = manual_package(); unrelated['content_version'] = 'different-package'
    other = PackageRuntimeService(runtime.db, runtime.publisher,
        single_player_content={content_hash(unrelated): catalogue(unrelated)},
        single_player_required={content_hash(unrelated): 'a'})
    assert other.get(baseline_a['session_id'], 1) == baseline_a


@pytest.mark.parametrize('missing', ['empty', 'wrong-hash', 'wrong-role'])
def test_opening_single_player_required_binding_fails_closed_with_missing_catalogue(runtime, missing):
    package = manual_package(); publish(runtime, package)
    opening = runtime.service.create(request(), 1); runtime.db.commit()
    row = runtime.db.query(ScriptPackagePlaySession).one()
    original = (row.binding_json, row.binding_hash, row.package_hash, row.selected_character_id)
    contents = {}
    if missing == 'wrong-hash':
        another = manual_package(); another['content_version'] = 'different-package'
        contents[content_hash(package)] = catalogue(another)
    elif missing == 'wrong-role':
        contents[content_hash(package)] = catalogue(package, role='b')
    service = PackageRuntimeService(runtime.db, runtime.publisher, single_player_content=contents,
        single_player_required={content_hash(package): 'a'})
    for read in (lambda: service.get(opening['session_id'], 1), lambda: service.create(request(), 1)):
        with pytest.raises(PackageRuntimeError) as caught:
            read()
        assert caught.value.code == 'PACKAGE_OPENING_SINGLE_PLAYER_UNAVAILABLE'
        assert caught.value.status_code == 409
    assert (row.binding_json, row.binding_hash, row.package_hash, row.selected_character_id) == original
    assert runtime.db.query(ScriptPackagePlaySession).count() == 1
    with pytest.raises(PackageRuntimeError) as caught:
        service.get(opening['session_id'], 2)
    assert caught.value.status_code == 404


def test_opening_single_player_required_missing_catalogue_does_not_save_new_session(runtime):
    package = manual_package(); publish(runtime, package)
    service = PackageRuntimeService(runtime.db, runtime.publisher, single_player_required={content_hash(package): 'a'})
    with pytest.raises(PackageRuntimeError, match='PACKAGE_OPENING_SINGLE_PLAYER_UNAVAILABLE'):
        service.create(request(), 1)
    assert runtime.db.query(ScriptPackagePlaySession).count() == 0
    assert service.list_releases(1) == runtime.service.list_releases(1)


def test_opening_single_player_manual_runs_after_reading_repair_and_only_replaces_exact_ids(runtime):
    package = manual_package(); publish(runtime, package)
    class Repair:
        def apply(self, view):
            view['public_knowledge'][0].update(text='REPAIRED_OLD_RULE', original='OLD_RAW', original_text='OLD_RAW')
            return view
    plan = catalogue(package, replacements=['old-rules-one'])
    view = adapted(runtime, package, plan, presentation_repair=Repair()).create(request(), 1)
    assert view['public_knowledge'][0]['text'] == plan.rules
    assert 'original' not in view['public_knowledge'][0] and 'original_text' not in view['public_knowledge'][0]
    assert view['public_knowledge'][1]['id'] == 'old-rules-two'
    assert view['public_knowledge'][1]['text'] == 'OLD_COLLECTIVE_RULE_old-rules-two'


def test_opening_single_player_removed_rule_images_are_not_a_backdoor_to_old_instructions(runtime):
    package = image_package()
    public = deepcopy(package['knowledge'][0]); public.update(id='public-picture', visibility='PUBLIC', character_id=None, disclosure='PUBLIC')
    package['knowledge'].append(public)
    visual = deepcopy(package['visuals'][0]); visual.update(id='other-picture', material_id='public-picture')
    package['visuals'].append(visual)
    assert validate_package(package)['valid']
    publish(runtime, package)
    plan = catalogue(package, replacements=['initial-a'])
    service = adapted(runtime, package, plan)
    view = service.create(request(), 1)
    assert [item['id'] for item in view['visuals']] == ['other-picture']
    store = Mock()
    with pytest.raises(PackageRuntimeError) as caught:
        service.image(view['session_id'], 'image-initial-a', 1, source_store=store)
    assert caught.value.status_code == 404 and not store.mock_calls
