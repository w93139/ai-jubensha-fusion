"""Fictional event-triggered memories; real isolated store, zero paid calls."""
import asyncio
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import update
from pathlib import Path

from src.db.models.package_play import ScriptPackagePlayEvent
from src.fusion.package_memory_rules import PackageMemoryRules
from src.fusion.package_play import PackagePlayError
from src.fusion.package_import import PackageImportService
from src.fusion.script_publication import ScriptPublicationService
from src.fusion.package_play_engine import play_engine
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.fusion.script_review import ScriptReviewService, _validate_findings
from src.fusion.source_bundles import verifier_version_for
from src.schemas.script_package import package_json_schema
from src.schemas.script_review import parse_audit_report, finding_entity
from tests.fusion_security.test_package_discussion import statement
from tests.fusion_security.test_package_investigation_store import investigation_document, perform_body
from tests.fusion_security.test_package_play_store import play, service, start, events, ask_body, response
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_proposals import command, answer
from tests.fusion_security.test_source_bundles import bundle, candidate_v11_for
from tests.fusion_security.test_package_investigation import investigation_package
from tests.fusion_security.test_script_review import review_db


def memory_package(base=None):
    package = deepcopy(base) if base is not None else investigation_document()
    package['schema_version'] = 'script-package/1.3'
    refs = deepcopy(package['introduction']['sources'])
    def memory(identifier, actor, trigger, phase='opening', retelling='MAY_RETELL'):
        return {'id': identifier, 'character_id': actor, 'phase_id': phase,
            'title': f'虚构回忆 {identifier}', 'text': f'PRIVATE_RECALL_{actor}_{identifier} 藏着三角木片。',
            'kind': 'CLAIM', 'card_disclosure': 'KEEP_PRIVATE', 'retelling': retelling,
            'origin': 'SOURCE_EXPLICIT', 'sources': deepcopy(refs), 'triggers': [trigger]}
    speech = lambda word: {'kind': 'OTHER_PUBLIC_SPEECH', 'keyword': word}
    evidence = lambda identifier: {'kind': 'ACQUIRED_EVIDENCE', 'evidence_id': identifier}
    package['memories'] = [memory('recall-a', 'a', speech('共享标记')),
        memory('recall-b', 'b', speech('三角木片')),
        memory('recall-a-key', 'a', evidence('key'), retelling='MUST_RETELL'),
        memory('recall-b-key', 'b', evidence('key')),
        memory('recall-b-private', 'b', evidence('b-action-card')),
        memory('recall-a-future', 'a', speech('共享标记'), phase='ending')]
    return package


def heard(engine, sequence, actor, words):
    engine.observe_event(sequence, {'speaker': actor, 'text': words, 'phase_id': engine.state()['current_phase_id']})


def grants(engine):
    return {item['id'] for item in engine.state()['memory_grants']}


def test_package_memories_version_pair_schema_and_old_package_rejection():
    package = memory_package()
    assert validate_package(package)['valid']
    assert validate_package(package)['validator_version'] == 'script-package-validator/1.3'
    assert verifier_version_for(package) == 'source-verifier/1.3'
    Draft202012Validator(package_json_schema('script-package/1.3')).validate(package)
    schema_file = Path(__file__).resolve().parents[3] / 'docs/contracts/script-package.v1.3.schema.json'
    assert json.loads(schema_file.read_text()) == {'$schema': 'https://json-schema.org/draft/2020-12/schema', **package_json_schema('script-package/1.3')}
    assert isinstance(play_engine(package, 'a'), PackageMemoryRules)
    with pytest.raises(PlayRulesError, match='SNAPSHOT_INVALID'):
        play_engine(package, 'a', 'package-play-rules/1.1')
    package['schema_version'] = 'script-package/1.2'
    assert not validate_package(package)['valid']
    assert 'memories' not in play_engine(investigation_document(), 'a').view()


@pytest.mark.parametrize('mutation', ['character', 'phase', 'evidence', 'foreign-private', 'source', 'origin',
    'duplicate-id', 'knowledge-id', 'duplicate-trigger', 'blank-word', 'space-word', 'unknown-trigger', 'public-card', 'truth-kind'])
def test_package_memories_invalid_declarations_rejected(mutation):
    package = memory_package(); item = package['memories'][0]
    if mutation == 'character': item['character_id'] = 'outsider'
    elif mutation == 'phase': item['phase_id'] = 'missing'
    elif mutation == 'evidence': item['triggers'] = [{'kind': 'ACQUIRED_EVIDENCE', 'evidence_id': 'missing'}]
    elif mutation == 'foreign-private':
        item['triggers'] = [{'kind': 'ACQUIRED_EVIDENCE', 'evidence_id': 'b-action-card'}]
        next(x for x in package['evidence'] if x['id'] == 'b-action-card')['disclosure'] = 'KEEP_PRIVATE'
    elif mutation == 'source': item['sources'] = [{'source_id': 'missing', 'anchor': 'unknown'}]
    elif mutation == 'origin': item['origin'] = 'EDITORIAL'
    elif mutation == 'duplicate-id': package['memories'].append(deepcopy(item))
    elif mutation == 'knowledge-id': item['id'] = package['knowledge'][0]['id']
    elif mutation == 'duplicate-trigger': item['triggers'] *= 2
    elif mutation == 'blank-word': item['triggers'][0]['keyword'] = '   '
    elif mutation == 'space-word': item['triggers'][0]['keyword'] = ' 共享标记 '
    elif mutation == 'unknown-trigger': item['triggers'][0] = {'kind': 'MODEL_DECIDES', 'prompt': 'grant'}
    elif mutation == 'public-card': item['card_disclosure'] = 'MAY_SHARE'
    elif mutation == 'truth-kind': item['kind'] = 'SYSTEM_TRUTH'
    assert not validate_package(package)['valid']


def test_package_memories_self_keywords_reading_and_future_do_not_grant():
    engine = PackageMemoryRules(memory_package(), 'a')
    before = engine.state()
    for _ in range(2):
        engine.view(); engine.role_context('a'); engine.proposal_context('b')
    assert engine.state() == before
    heard(engine, 1, 'a', '共享标记')
    assert not grants(engine)
    heard(engine, 2, 'b', '不是共享标记也值得核对。')  # Mention, not fact verification.
    assert grants(engine) == {'recall-a'}
    assert 'recall-b' not in grants(engine)  # Body contains its keyword; no implicit speech.
    own = engine.view()['memories']['entries'][0]
    assert own['cause'] == {'kind': 'OTHER_PUBLIC_SPEECH', 'speaker': 'b'} and own['sequence'] == 2
    heard(engine, 3, 'b', '共享标记')
    assert engine.view()['memories']['entries'] == [own]
    assert 'recall-a-future' not in grants(engine)
    with pytest.raises(PlayRulesError, match='EVENT_INVALID'):
        heard(engine, 3, 'b', '共享标记')


def test_package_memories_new_evidence_grants_to_actual_recipient_and_no_implicit_retelling():
    engine = PackageMemoryRules(memory_package(), 'a')
    engine.apply('PERFORM_ACTION', {'action_id': 'find-key'}); engine.observe_event(1)
    assert grants(engine) == {'recall-a-key', 'recall-b-key'}
    assert {x['id'] for x in engine.view()['memories']['entries']} == {'recall-a-key'}
    assert engine.view()['memories']['entries'][0]['retelling'] == 'MUST_RETELL'
    assert 'PRIVATE_RECALL_b' not in canonical_json(engine.view())
    engine.apply('PERFORM_ACTION', {'action_id': 'open-case'}); engine.observe_event(2)
    assert 'recall-b-private' in grants(engine)  # a performs action, b receives the reward.
    assert 'PRIVATE_RECALL_b' not in canonical_json(engine.view())
    for actor in ('a', 'b'):
        assert 'PRIVATE_RECALL_' not in canonical_json(engine.role_context(actor))
        private = engine.proposal_context(actor)
        assert all(not x['public'] for x in private['materials'] if x['id'].startswith('recall-'))
        assert f'PRIVATE_RECALL_{"b" if actor == "a" else "a"}' not in canonical_json(private)
    assert 'recall-b' not in grants(engine)
    heard(engine, 3, 'a', '我想起三角木片的事。')
    assert 'recall-b' in grants(engine)
    with pytest.raises(PlayRulesError):
        engine.apply('SHARE_MATERIAL', {'collection': 'knowledge', 'id': 'recall-a-key'})


def test_package_memories_initial_evidence_and_phase_floor_no_retroactive_speech():
    package = memory_package()
    next(x for x in package['evidence'] if x['id'] == 'key')['release'].pop('required_action_ids')
    engine = PackageMemoryRules(package, 'a')
    assert grants(engine) == {'recall-a-key', 'recall-b-key'}
    assert engine.view()['memories']['entries'][0]['sequence'] == 0
    heard(engine, 1, 'b', '共享标记')
    engine.apply('PERFORM_ACTION', {'action_id': 'find-key'}); engine.observe_event(2)
    engine.apply('PERFORM_ACTION', {'action_id': 'open-case'}); engine.observe_event(3)
    engine.apply('ADVANCE_PHASE'); engine.observe_event(4)
    assert 'recall-a-future' not in grants(engine)
    heard(engine, 5, 'b', '共享标记')
    assert 'recall-a-future' in grants(engine)
    engine.apply('PERFORM_ACTION', {'action_id': 'next-search'}); engine.observe_event(6)
    engine.apply('SETTLE'); engine.observe_event(7)
    before = deepcopy(engine.state()['memory_grants'])
    heard(engine, 8, 'a', '三角木片')
    assert engine.state()['memory_grants'] == before


@pytest.mark.parametrize('visibility', ['PUBLIC', 'CHARACTER_PRIVATE'])
def test_package_memories_forced_prior_evidence_is_not_an_unreachable_future_trigger(visibility):
    package = memory_package()
    evidence = next(x for x in package['evidence'] if x['id'] == 'key')
    evidence['release'].pop('required_action_ids')
    if visibility == 'CHARACTER_PRIVATE':
        evidence.update(visibility=visibility, character_id='a', disclosure='MAY_SHARE')
    package['memories'][2]['phase_id'] = 'ending'
    assert 'MEMORY_TRIGGER_PRECEDES_PHASE' in {item['code'] for item in validate_package(package)['issues']}


def replayed(play, initial):
    row = play.play._row(initial['play_id'], 1)
    return play.play._replay(row, *play.play._resolve(row))


def test_package_memories_saved_speech_triggers_ai_private_input_only_and_replays_once(play):
    opening, initial = start(play, memory_package())
    assert 'memories' not in opening and opening['supports_rules_preview'] is False
    assert initial['memories']['entries'] == []
    body = statement(0, words='请核对三角木片。')
    result = play.play.speak(initial['play_id'], body, 1); play.db.commit()
    assert result['revision'] == 1 and result['memories']['entries'] == []
    state = replayed(play, initial)
    assert grants(state.engine) == {'recall-b'}
    assert 'PRIVATE_RECALL_b' in canonical_json(state.engine.proposal_context('b'))
    assert 'PRIVATE_RECALL_b' not in canonical_json(result)
    assert play.play.speak(initial['play_id'], body, 1) == result
    with play.factory() as db:
        assert service(play, db).get(initial['play_id'], 1) == result
    assert len(events(play)) == 1
    assert json.loads(events(play)[0].event_json)['schema_version'] == 'package-text-play-event/1.3'
    play.sdk.chat_completion.assert_not_awaited()


def test_package_memories_ai_actual_sentence_triggers_human_once_without_reading_memory_body(play):
    _, initial = start(play, memory_package())
    play.sdk.chat_completion.return_value = answer('find-key')
    first = asyncio.run(play.play.propose(initial['play_id'], command(), 1))
    assert first['memories']['entries'] == []
    play.sdk.chat_completion.return_value = answer('free-check')
    second = asyncio.run(play.play.propose(initial['play_id'], command(2, 'second'), 1))
    assert [x['id'] for x in second['memories']['entries']] == ['recall-a']
    assert second['memories']['entries'][0]['sequence'] == 4
    assert grants(replayed(play, initial).engine) == {'recall-a'}
    # Memory body is not an implicit speech that would unlock b.
    assert 'PRIVATE_RECALL_b' not in canonical_json(second)
    assert asyncio.run(play.play.propose(initial['play_id'], command(2, 'second'), 1)) == second
    assert play.sdk.chat_completion.await_count == 2
    assert play.play.get(initial['play_id'], 1) == second


def test_package_memories_reference_body_and_material_question_are_not_spoken_events(play):
    package = memory_package()
    package['knowledge'].append({'id': 'public-marker', 'text': '共享标记和三角木片是虚构话题。', 'kind': 'CLAIM',
        'visibility': 'PUBLIC', 'character_id': None, 'disclosure': 'PUBLIC', 'release': {'phase_id': 'opening'},
        'sources': deepcopy(package['introduction']['sources'])})
    _, initial = start(play, package)
    play.sdk.chat_completion.return_value = answer('find-key', [{'collection': 'knowledge', 'id': 'public-marker'}])
    proposed = asyncio.run(play.play.propose(initial['play_id'], command(), 1))
    assert not grants(replayed(play, initial).engine)
    assert '共享标记' in canonical_json(proposed['investigation_proposals']['entries'][0]['basis'])
    play.sdk.chat_completion.return_value = response([{'collection': 'knowledge', 'id': 'public-marker'}])
    result = asyncio.run(play.play.ask(initial['play_id'], ask_body(2, question='共享标记和三角木片是什么？'), 1))
    assert result['last_ai_status'] == 'OK' and '共享标记' in result['dialogue'][0]['text']
    assert not grants(replayed(play, initial).engine)


def test_package_memories_material_answer_acquires_evidence_without_becoming_speech(play):
    package = memory_package()
    evidence = next(x for x in package['evidence'] if x['id'] == 'b-action-card')
    evidence['release'] = {'phase_id': 'opening'}
    evidence['text'] = '共享标记的虚构卡面。'
    memory = deepcopy(package['memories'][2]); memory['id'] = 'a-shared-card'
    memory['triggers'][0]['evidence_id'] = 'b-action-card'; package['memories'].append(memory)
    _, initial = start(play, package)
    assert 'recall-b-private' in grants(replayed(play, initial).engine)
    assert initial['memories']['entries'] == []
    play.sdk.chat_completion.return_value = response([{'collection': 'evidence', 'id': 'b-action-card'}])
    result = asyncio.run(play.play.ask(initial['play_id'], ask_body(), 1))
    assert [x['id'] for x in result['memories']['entries']] == ['a-shared-card']
    assert result['memories']['entries'][0]['cause'] == {'kind': 'ACQUIRED_EVIDENCE', 'evidence_id': 'b-action-card'}
    assert 'recall-a' not in grants(replayed(play, initial).engine)


@pytest.mark.parametrize('mode', ['invalid', 'stale', 'unknown'])
def test_package_memories_nonaccepted_proposal_never_triggers(play, mode):
    _, initial = start(play, memory_package())
    async def respond(*args, **kwargs):
        if mode == 'unknown': raise TimeoutError()
        if mode == 'stale':
            play.play.speak(initial['play_id'], statement(1, words='一条无关的发言。'), 1); play.db.commit()
        return answer('free-check', [{'collection': 'knowledge', 'id': 'recall-a'}] if mode == 'invalid' else [])
    play.sdk.chat_completion.side_effect = respond
    result = asyncio.run(play.play.propose(initial['play_id'], command(), 1))
    assert result['last_ai_status'] == mode.upper()
    assert result['memories']['entries'] == [] and not grants(replayed(play, initial).engine)
    assert play.sdk.chat_completion.await_count == 1


def test_package_memories_investigation_saved_once_owner_only_and_history_tamper_rejected(play):
    _, initial = start(play, memory_package())
    with pytest.raises(PackagePlayError): play.play.act(initial['play_id'], perform_body('find-key', 0), 2)
    assert not events(play)
    result = play.play.act(initial['play_id'], perform_body('find-key', 0), 1); play.db.commit()
    assert [x['id'] for x in result['memories']['entries']] == ['recall-a-key']
    assert result['mechanics']['spent_points'] == 1
    assert play.play.act(initial['play_id'], perform_body('find-key', 0), 1) == result
    with play.factory() as db: assert service(play, db).get(initial['play_id'], 1) == result
    event = events(play)[0]
    play.db.execute(update(ScriptPackagePlayEvent).where(ScriptPackagePlayEvent.id == event.id).values(state_hash='0' * 64))
    play.db.commit()
    with pytest.raises(PackagePlayError, match='HISTORY_INVALID'): play.play.get(initial['play_id'], 1)


def test_package_memories_sources_and_audit_locations_are_mandatory(bundle):
    _, _, store, frozen = bundle
    package = memory_package(investigation_package(candidate_v11_for(bundle)))
    # Base source fixture has no b-action-card; restrict to the two speech rules.
    package['memories'] = package['memories'][:2]
    assert validate_package(package)['valid']
    verified = store.verify(frozen['bundle_hash'], document=package)
    assert verified['valid'] and verified['verifier_version'] == 'source-verifier/1.3'
    package['memories'][0]['sources'][0]['anchor'] = 'missing-memory-anchor'
    assert not store.verify(frozen['bundle_hash'], document=package)['valid']
    report = {'schema_version': 'script-audit/1.2', 'summary': '虚构回忆审核', 'coverage': ['KNOWLEDGE_BOUNDARY'],
        'findings': [{'id': 'memory-finding', 'category': 'KNOWLEDGE_BOUNDARY', 'severity': 'BLOCKER',
            'target': {'collection': 'memories', 'id': 'recall-a'}, 'message': '触发来源待核实。',
            'sources': deepcopy(package['memories'][0]['sources'])}]}
    parsed = parse_audit_report(report, package['schema_version'])
    _validate_findings(package, parsed)
    assert finding_entity(package, parsed.findings[0].target) == package['memories'][0]
    with pytest.raises(ValueError): parse_audit_report(report, 'script-package/1.2')
    with pytest.raises(ValueError): parse_audit_report({**report, 'schema_version': 'script-audit/1.1'}, 'script-package/1.3')


def test_package_memories_intake_and_review_cannot_bypass_model_audit_publication_gate(review_db, bundle):
    db, _ = review_db
    package = memory_package(investigation_package(candidate_v11_for(bundle)))
    package['memories'] = package['memories'][:2]
    imported = PackageImportService(db).submit(package, submitted_by=1, idempotency_key='memory-candidate')
    db.commit()
    review = ScriptReviewService(db).get_review(imported['version_id'])
    assert review['candidate']['contract_version'] == 'script-package/1.3'
    assert {x['target']['id'] for x in review['references'] if x['target']['collection'] == 'memories'} == {'recall-a', 'recall-b'}
    gate = ScriptPublicationService(db).gate_state(imported['version_id'], bundle[2], bundle[3]['bundle_hash'])
    assert not gate['can_approve'] and not gate['can_publish']
    assert not next(x['passed'] for x in gate['checks'] if x['code'] == 'MODEL_AUDIT_COMPLETE')
