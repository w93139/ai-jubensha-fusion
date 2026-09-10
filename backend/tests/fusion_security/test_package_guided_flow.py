"""Guided human investigation, authored speech and explicitly requested hints."""
import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from src.fusion.package_guided_flow import GuidedFlowContent, digest
from src.fusion.package_play import PackagePlayError
from src.fusion.package_validation import content_hash, canonical_json
from src.schemas.package_play import GuidedPlayRequest
from tests.fusion_security.test_package_play_store import play, service, start, events, action_body
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_full_play import full_package, REFS
from tests.fusion_security.test_full_play_store import command
from tests.fusion_security.test_full_play_decisions import decision


def content(package, retells=False):
    topics = []
    for phase in package['phases']:
        topics.append({'id': f"hint-{phase['id']}", 'phase_id': phase['id'], 'title': '当前卡点', 'sources': REFS,
            'levels': [{'level': level, 'text': f"HINT_{phase['id']}_{level} 三角木片", 'source_refs': REFS}
                       for level in (1, 2, 3)]})
    entries = []
    if retells:
        for collection, material_id, actor in [('knowledge','initial-b','b'), ('knowledge','initial-a','a'),
                                              ('memory','memory-c','c')]:
            material = next(m for m in package['memories' if collection == 'memory' else collection] if m['id'] == material_id)
            entries.append({'id': f'retell-{material_id}', 'collection': collection, 'material_id': material_id,
                'owner_character_id': actor, 'text_sha256': digest(material['text']), 'kind': 'CLAIM',
                'speech': f'{actor}声称看见三角木片。', 'sources': REFS})
    return {'schema_version': 'guided-flow-content/1.0', 'package_hash': content_hash(package),
            'selected_character_id': 'a', 'host_topics': topics, 'required_retells': entries}


def setup(play, retells=False):
    package = full_package()
    if retells:
        for m in package['knowledge'][:2]: m['retelling'] = 'MUST_RETELL'
        package['memories'][2]['retelling'] = 'MUST_RETELL'
    catalogue = GuidedFlowContent(package, content(package, retells))
    play.play.guided_content = {content_hash(package): catalogue}
    _, view = start(play, package)
    return package, view


def guided(view, action, payload=None, key=None):
    request = {'schema_version': 'package-guided-command/1.0', 'expected_revision': view['revision'],
        'idempotency_key': key or f"guided-{view['revision']}-{action}", 'action': action}
    if payload is not None: request['payload'] = payload
    return request


def run(play, view, action, payload=None, key=None):
    return play.play.guided(view['play_id'], guided(view, action, payload, key), 1)


def enter(play, view):
    return play.play.act(view['play_id'], action_body(view['revision'], f"advance-{view['revision']}", 'ADVANCE_PHASE'), 1)


def test_guided_selection_spends_once_grants_and_finishes_without_model(play):
    _, initial = setup(play)
    assert initial['guided_play']['last_command'] is None
    assert not initial['guided_play']['can_investigate']
    view = enter(play, initial)
    request = guided(view, 'INVESTIGATE', {'action_id': 'find-key'})
    view = play.play.guided(view['play_id'], request, 1)
    assert view['mechanics']['spent_points'] == 1
    assert any(e['id'] == 'evidence-find-key' for e in view['public_evidence'])
    assert view['full_game']['ballot'] is None
    assert view['guided_play']['last_command']['idempotency_key'] == request['idempotency_key']
    assert play.play.guided(view['play_id'], request, 1) == view
    before = len(events(play))
    for identifier in ('look-note', 'unknown', 'find-key'):
        with pytest.raises(PackagePlayError): run(play, view, 'INVESTIGATE', {'action_id': identifier})
    assert len(events(play)) == before
    view = run(play, view, 'INVESTIGATE', {'action_id': 'open-box'})
    assert view['mechanics']['spent_points'] == 2
    view = run(play, view, 'FINISH_INVESTIGATION')
    assert view['current_phase']['id'] == 'read-two'
    assert play.sdk.chat_completion.await_count == 0
    play.db.commit()
    fresh = service(play, play.db).get(view['play_id'], 1)
    assert fresh['current_phase'] == view['current_phase']  # replay survives registry removal


def test_guided_replaces_existing_one_of_five_ballot_without_mutating_old_events(play):
    _, view = start(play, full_package())
    view = enter(play, view)
    view = play.play.table(view['play_id'], command(view['revision'], 'OPEN_BALLOT'), 1)
    view = play.play.table(view['play_id'], command(view['revision'], 'CAST_BALLOT', {'kind':'CHOOSE', 'choice_id':'find-key'}), 1)
    before = [(e.id, e.event_json, e.request_json) for e in events(play)]
    package = full_package(); play.play.guided_content = {content_hash(package): GuidedFlowContent(package, content(package))}
    view = play.play.get(view['play_id'], 1)
    assert view['guided_play']['has_legacy_ballot']
    with pytest.raises(PackagePlayError): run(play, view, 'INVESTIGATE', {'action_id':'open-box'})
    view = run(play, view, 'INVESTIGATE', {'action_id':'find-key'})
    assert view['mechanics']['spent_points'] == 1 and view['full_game']['ballot'] is None
    assert [(e.id, e.event_json, e.request_json) for e in events(play)[:len(before)]] == before
    assert play.sdk.chat_completion.await_count == 0


def test_guided_hints_are_private_explicit_scoped_persistent_and_never_speech(play):
    _, view = setup(play)
    assert 'HINT_' not in canonical_json(view)
    assert [t['id'] for t in view['host_hints']['topics']] == ['hint-read-one']
    for level in (1,2,3):
        view = run(play, view, 'REQUEST_HINT', {'topic_id':'hint-read-one', 'level':level})
        assert f'HINT_read-one_{level}' in canonical_json(view)
    assert len(view['host_hints']['entries']) == 3
    assert view['discussion']['entries'] == [] and view['memories']['entries'] == []
    n = len(events(play))
    assert run(play, view, 'REQUEST_HINT', {'topic_id':'hint-read-one','level':3}) == view
    assert len(events(play)) == n
    with pytest.raises(PackagePlayError, match='HINT_NOT_AVAILABLE'):
        run(play, view, 'REQUEST_HINT', {'topic_id':'hint-finale','level':3})
    assert 'HINT_finale' not in canonical_json(view)
    play.db.commit()
    assert service(play, play.db).get(view['play_id'],1)['host_hints']['entries'] == view['host_hints']['entries']
    assert play.sdk.chat_completion.await_count == 0


def test_guided_required_retellings_only_acquired_other_players_once_and_trigger_memories(play):
    _, view = setup(play, True)
    assert view['discussion']['entries'] == []
    view = enter(play, view)
    assert [e['speaker'] for e in view['discussion']['entries']] == ['b','c']
    assert len(view['memories']['entries']) == 1
    raw = canonical_json(view)
    assert 'PRIVATE_BOOK_b' not in raw and 'PRIVATE_MEMORY_c' not in raw
    assert 'a声称' not in raw
    view = run(play, view, 'PRESENT_REQUIRED')
    assert len(view['discussion']['entries']) == 2
    view = run(play, view, 'INVESTIGATE', {'action_id':'find-key'})
    assert len(view['discussion']['entries']) == 2
    play.db.commit()
    fresh = service(play, play.db).get(view['play_id'],1)
    assert fresh['discussion'] == view['discussion']
    assert play.sdk.chat_completion.await_count == 0


def test_guided_hint_does_not_flush_legacy_pending_retellings(play):
    package=full_package(); package['knowledge'][1]['retelling']='MUST_RETELL'
    document=content(package); material=package['knowledge'][1]
    document['required_retells']=[{'id':'pending-b','collection':'knowledge','material_id':material['id'],
        'owner_character_id':'b','text_sha256':digest(material['text']),'kind':'CLAIM','speech':'三角木片','sources':REFS}]
    _, view = start(play, package); view = enter(play,view)
    play.play.guided_content={content_hash(package):GuidedFlowContent(package,document)}
    view = run(play,view,'REQUEST_HINT',{'topic_id':'hint-investigate-one','level':3})
    assert view['guided_play']['can_present_required']
    assert view['discussion']['entries']==[] and view['memories']['entries']==[]


def test_guided_phone_and_ownership_guard_and_no_ai_search_vote(play):
    _, view = setup(play); view=enter(play,view)
    with pytest.raises(PackagePlayError,match='NOT_FOUND'):
        play.play.guided(view['play_id'], guided(view,'FINISH_INVESTIGATION'),2)
    with pytest.raises(PackagePlayError,match='HUMAN_CHOICE_REQUIRED'):
        asyncio.run(play.play.decide(view['play_id'],decision(view['revision']),1))
    view=play.play.table(view['play_id'],command(view['revision'],'START_CALL',{'peer_character_id':'b'}),1)
    n=len(events(play))
    for action, payload in [('INVESTIGATE',{'action_id':'find-key'}),('FINISH_INVESTIGATION',None)]:
        with pytest.raises(PackagePlayError): run(play,view,action,payload)
    assert len(events(play))==n and play.sdk.chat_completion.await_count==0


@pytest.mark.parametrize('level',[True,False,'1',1.0,0,4])
def test_guided_rejects_nonexact_hint_level(level):
    with pytest.raises(ValidationError):
        GuidedPlayRequest.model_validate(guided({'revision':0},'REQUEST_HINT',{'topic_id':'hint-read-one','level':level}))


@pytest.mark.parametrize('change',['hash','source','owner','material','grade'])
def test_guided_catalogue_rejects_mismatched_sources_and_materials(change):
    package=full_package(); document=content(package)
    if change=='hash': document['package_hash']='0'*64
    if change=='source': document['host_topics'][0]['sources']=[{'source_id':'missing','anchor':'L1'}]
    if change=='owner': document['selected_character_id']='missing'
    if change=='material': document['required_retells']=[{'id':'bad','collection':'knowledge','material_id':'initial-b',
        'owner_character_id':'b','kind':'CLAIM','speech':'不得公开私本','text_sha256':digest(package['knowledge'][1]['text']),'sources':REFS}]
    if change=='grade': document['host_topics'][0]['levels'][0]['level']=True
    with pytest.raises(ValueError): GuidedFlowContent(package,document)


def test_guided_paid_result_survives_failed_authored_continuation(play, monkeypatch):
    from tests.fusion_security.test_full_play_private_dialogue import request, reply
    _,view=setup(play);view=enter(play,view)
    view=play.play.table(view['play_id'],command(view['revision'],'START_CALL',{'peer_character_id':'b'}),1)
    view=play.play.table(view['play_id'],command(view['revision'],'PRIVATE_SPEAK',{'text':'三角木片在哪里？'}),1)
    play.sdk.chat_completion.return_value=reply()
    def failed(*args):raise PackagePlayError('FULL_PLAY_EVENT_LIMIT')
    monkeypatch.setattr(play.play,'_present_required',failed)
    view=asyncio.run(play.play.reply_private(view['play_id'],request(),1))
    assert view['last_ai_status']=='OK' and view['budget']['used_tokens']==110
    assert not view['pending_ai'] and len(view['full_game']['private_discussion'])==2
    with play.factory() as db:
        restored=service(play,db).get(view['play_id'],1)
        assert restored['budget']==view['budget']
        assert restored['full_game']['private_discussion']==view['full_game']['private_discussion']
    assert play.sdk.chat_completion.await_count==1


def test_guided_recorded_speech_remains_marked_readable_without_catalogue(play):
    _,view=setup(play,True);view=enter(play,view);play.db.commit()
    restored=service(play,play.db).get(view['play_id'],1)
    assert restored['guided_play']['available'] is False
    assert not restored['guided_play']['can_present_required']
    assert restored['discussion']==view['discussion']


def test_guided_required_speech_respects_shared_discussion_limit(play,monkeypatch):
    _,view=setup(play,True)
    monkeypatch.setattr(play.play,'_statement_limit',lambda state:0)
    view=enter(play,view)
    assert view['discussion']['entries']==[]
    assert not view['guided_play']['can_present_required']
    row=play.play._row(view['play_id'],1);package,binding=play.play._resolve(row)
    state=play.play._replay(row,package,binding)
    entry=next(iter(play.play._guided_catalog(binding)._retells.values()))
    with pytest.raises(Exception,match='DISCUSSION_LIMIT'):
        play.play._consume(state,'ACTION',{'schema_version':'package-scripted-retelling-command/1.0',
            'expected_revision':state.revision,'idempotency_key':'direct-internal','entry_id':entry['id']},
            {'policy':'package-guided-play/1.0','catalog_hash':'1'*64,'collection':entry['collection'],
             'material_id':entry['material_id'],'owner':entry['owner_character_id'],
             'text_sha256':entry['text_sha256'],'speech':entry['speech']},binding)


def test_guided_can_finish_a_legacy_already_closed_investigation(play):
    from tests.fusion_security.test_package_full_play import investigate
    from src.fusion.package_play_engine import play_engine
    engine=play_engine(full_package(),'a');engine.apply('ADVANCE_PHASE')
    investigate(engine) # all five old seats explicitly SKIP
    assert engine.view()['can_advance']
    engine.guided_investigate(finish=True)
    engine.apply('ADVANCE_PHASE')
    assert engine.view()['current_phase']['id']=='read-two'


def test_guided_sealed_finale_cannot_request_more_hints(play):
    from tests.fusion_security.test_structured_finale import submission
    _,view=setup(play)
    for _ in range(2):
        view=enter(play,view);view=run(play,view,'FINISH_INVESTIGATION')
    view=play.play.table(view['play_id'],command(view['revision'],'SEAL_FINALE',submission('a')),1)
    assert view['host_hints']['topics']==[]
    with pytest.raises(PackagePlayError,match='HINT_NOT_AVAILABLE'):
        run(play,view,'REQUEST_HINT',{'topic_id':'hint-finale','level':3})


def test_guided_already_scripted_memory_is_not_assigned_again_to_paid_response(play):
    _,view=setup(play,True);view=enter(play,view)
    view=play.play.speak(view['play_id'],{'schema_version':'package-discussion-command/1.0','action':'SPEAK',
        'expected_revision':view['revision'],'idempotency_key':'after-scripted','text':'还有其他线索吗？'},1)
    row=play.play._row(view['play_id'],1);package,binding=play.play._resolve(row)
    state=play.play._replay(row,package,binding)
    context=play.play._dialogue_context(state,binding,'c',view['discussion']['entries'][-1]['id'],version='package-dialogue-model/1.11')
    assert any(m['id']=='memory-c' for m in context['materials'])
    assert 'response_task' not in context


def proposal_request(view,key):
    return {'schema_version':'package-investigation-command/1.0','action':'PROPOSE','character_id':'b',
        'expected_revision':view['revision'],'idempotency_key':key}


def test_guided_advice_only_selects_legal_location_and_has_no_private_citation_output(play):
    from tests.fusion_security.test_full_play_decisions import output
    _,view=setup(play);view=enter(play,view)
    async def complete(messages, **params):
        assert not play.db.in_transaction()
        schema=params['response_format']['json_schema']['schema']
        assert set(schema['properties'])=={'action_id'}
        assert set(schema['properties']['action_id']['enum'])=={'find-key'}
        assert 'PRIVATE_BOOK_b' in messages[1].content and 'PRIVATE_BOOK_c' not in messages[1].content
        return output({'action_id':'find-key'})
    play.sdk.chat_completion.side_effect=complete
    request=proposal_request(view,'guided-advice')
    view=asyncio.run(play.play.propose(view['play_id'],request,1))
    assert view['last_ai_status']=='OK' and len(view['investigation_proposals']['entries'])==1
    entry=view['investigation_proposals']['entries'][0]
    assert entry['basis']==[] and entry['action']['id']=='find-key'
    assert 'PRIVATE_BOOK' not in canonical_json(entry)
    assert view['mechanics']['spent_points']==0 and view['full_game']['ballot'] is None
    assert asyncio.run(play.play.propose(view['play_id'],request,1))==view
    assert play.sdk.chat_completion.await_count==1
    play.db.commit()
    with play.factory() as db:
        assert service(play,db).get(view['play_id'],1)['investigation_proposals']['entries']==[entry]


@pytest.mark.parametrize('bad',[{'action_id':'look-note'},{'action_id':'find-key','public_basis':[]},
                               {'action_id':'find-key','text':'秘密'}, {'action_id':'unknown'}])
def test_guided_advice_rejects_future_location_or_any_extra_output(play,bad):
    from tests.fusion_security.test_full_play_decisions import output
    _,view=setup(play);view=enter(play,view);play.sdk.chat_completion.return_value=output(bad)
    view=asyncio.run(play.play.propose(view['play_id'],proposal_request(view,'bad-advice'),1))
    assert view['last_ai_status']=='INVALID' and view['investigation_proposals']['entries']==[]
    assert view['mechanics']['spent_points']==0 and view['budget']['used_tokens']==110


def test_guided_advice_forward_protocol_transition_preserves_old_records_and_receipts(play):
    from tests.fusion_security.test_full_play_decisions import output
    package=full_package();_,view=start(play,package);view=enter(play,view)
    play.sdk.chat_completion.return_value=output({'action_id':'find-key','public_basis':[]})
    old_request=proposal_request(view,'old-advice')
    view=asyncio.run(play.play.propose(view['play_id'],old_request,1))
    old_events=[e.event_json for e in events(play)];old_used=view['budget']['used_tokens']
    play.play.guided_content={content_hash(package):GuidedFlowContent(package,content(package))}
    repeated=asyncio.run(play.play.propose(view['play_id'],old_request,1))
    assert repeated['revision']==view['revision'] and play.sdk.chat_completion.await_count==1
    play.sdk.chat_completion.return_value=output({'action_id':'find-key'})
    view=asyncio.run(play.play.propose(view['play_id'],proposal_request(view,'new-advice'),1))
    assert view['last_ai_status']=='OK' and len(view['investigation_proposals']['entries'])==2
    assert [e.event_json for e in events(play)[:len(old_events)]]==old_events
    assert view['budget']['used_tokens']==old_used+110
    row=play.play._row(view['play_id'],1);p,b=play.play._resolve(row);state=play.play._replay(row,p,b)
    assert state.proposal_model['schema_version']=='package-proposal-model/1.2'
    assert not service(play,play.db).get(view['play_id'],1)['investigation_proposals']['available'] # no silent downgrade


def post_game_content(package):
    """Fictional reviewed answers; deliberately include non-answer metadata."""
    document = content(package)
    for topic in document['host_topics']:
        topic['boundary'] = 'INTERNAL_BOUNDARY'
        topic['levels'][2]['reveal_scope'] = 'CURRENT_FINALE_QUESTION_ANSWER'
        topic['levels'][2]['internal'] = 'INTERNAL_REVIEW'
    final = next(t for t in document['host_topics'] if t['phase_id'] == 'finale')
    for suffix, scope in [('unknown', 'FUTURE_UNKNOWN_SCOPE'), ('ordinary', 'CURRENT_STAGE_ONLY')]:
        extra = deepcopy(final)
        extra['id'] += '-' + suffix
        extra['levels'][2]['reveal_scope'] = scope
        extra['levels'][2]['text'] = 'EXCLUDED_' + suffix
        document['host_topics'].append(extra)
    missing = deepcopy(final)
    missing['id'] += '-missing'
    del missing['levels'][2]['reveal_scope']
    missing['levels'][2]['text'] = 'EXCLUDED_missing'
    document['host_topics'].append(missing)
    return document


def post_game_setup(play):
    package = full_package()
    play.play.guided_content = {content_hash(package): GuidedFlowContent(package, post_game_content(package))}
    _, view = start(play, package)
    return package, view


def seal_post_game(play, view):
    from tests.fusion_security.test_structured_finale import submission
    from tests.fusion_security.test_full_play_decisions import output
    for _ in range(2):
        view = enter(play, view)
        view = run(play, view, 'FINISH_INVESTIGATION')
    view = play.play.table(view['play_id'], command(view['revision'], 'SEAL_FINALE', submission('a')), 1)
    for actor in 'bcde':
        play.sdk.chat_completion.return_value = output(submission(actor))
        view = asyncio.run(play.play.decide(view['play_id'], decision(view['revision'], actor, 'SEAL_FINALE'), 1))
        assert view['last_ai_status'] == 'OK'
    return view


def test_post_game_catalogue_projects_only_current_explicit_answers_and_copies():
    package = full_package()
    document = post_game_content(package)
    catalogue = GuidedFlowContent(package, document)
    expected = [{'id': 'hint-finale', 'title': '当前卡点', 'text': 'HINT_finale_3 三角木片'}]
    assert catalogue.post_game_questions('finale') == expected
    assert catalogue.post_game_questions('missing') == []
    projected = catalogue.post_game_questions('finale')
    projected[0]['text'] = 'client change'
    document['host_topics'][-1]['levels'][2]['text'] = 'source change'
    assert catalogue.post_game_questions('finale') == expected
    assert all(set(question) == {'id', 'title', 'text'} for question in projected)


def test_post_game_answers_absent_until_explicit_settlement_and_read_only_after(play):
    _, view = post_game_setup(play)
    assert 'post_game_qa' not in view and 'HINT_finale_3' not in canonical_json(view)
    view = seal_post_game(play, view)
    assert view['full_game']['finale']['all_sealed'] and not view['settled']
    assert 'post_game_qa' not in view and 'HINT_finale_3' not in canonical_json(view)
    view = play.play.act(view['play_id'], action_body(view['revision'], 'post-game-settle', 'SETTLE'), 1)
    expected = {'schema_version': 'package-post-game-qa/1.0', 'questions': [
        {'id': 'hint-finale', 'title': '当前卡点', 'text': 'HINT_finale_3 三角木片'}]}
    assert view['settled'] and view['full_game']['result'] is not None
    assert view['post_game_qa'] == expected
    assert all(text not in canonical_json(view['post_game_qa']) for text in
               ('sources', 'source_refs', 'boundary', 'internal', 'EXCLUDED_', 'HINT_read', 'HINT_investigate'))
    play.db.commit()
    before = [(event.id, event.event_json, event.request_json) for event in events(play)]
    calls = play.sdk.chat_completion.await_count
    original_budget = deepcopy(view['budget'])
    for _ in range(2):
        reread = play.play.get(view['play_id'], 1)
        assert reread['post_game_qa'] == expected and reread['budget'] == original_budget
    assert [(event.id, event.event_json, event.request_json) for event in events(play)] == before
    assert play.sdk.chat_completion.await_count == calls
    with pytest.raises(PackagePlayError, match='NOT_FOUND'):
        play.play.get(view['play_id'], 2)
    assert [(event.id, event.event_json, event.request_json) for event in events(play)] == before
    assert play.sdk.chat_completion.await_count == calls


@pytest.mark.parametrize('mismatch', ['role', 'package', 'absent'])
def test_post_game_answers_require_matching_catalogue(play, mismatch):
    package, view = post_game_setup(play)
    view = seal_post_game(play, view)
    view = play.play.act(view['play_id'], action_body(view['revision'], 'post-game-settle', 'SETTLE'), 1)
    assert view['post_game_qa']['questions']
    document = post_game_content(package)
    if mismatch == 'role':
        document['selected_character_id'] = 'b'
    elif mismatch == 'package':
        package = deepcopy(package)
        package['title'] = 'Different fictional publication'
        document['package_hash'] = content_hash(package)
    if mismatch == 'absent':
        play.play.guided_content = {}
    else:
        play.play.guided_content[view['package_hash']] = GuidedFlowContent(package, document)
    before = len(events(play))
    calls = play.sdk.chat_completion.await_count
    assert 'post_game_qa' not in play.play.get(view['play_id'], 1)
    assert len(events(play)) == before and play.sdk.chat_completion.await_count == calls


@pytest.mark.parametrize('source_field', ['sources', 'source_refs'])
def test_post_game_answer_catalogue_rejects_unknown_source(source_field):
    package = full_package()
    document = post_game_content(package)
    final = next(t for t in document['host_topics'] if t['phase_id'] == 'finale')
    target = final if source_field == 'sources' else final['levels'][2]
    target[source_field] = [{'source_id': 'unreviewed', 'anchor': 'L1'}]
    with pytest.raises(ValueError, match='GUIDED_CONTENT_INVALID'):
        GuidedFlowContent(package, document)
