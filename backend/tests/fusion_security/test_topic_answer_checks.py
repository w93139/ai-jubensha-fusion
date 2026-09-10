"""Authored finite checks reject known failure classes without rewriting old turns."""
import asyncio
from copy import deepcopy

import pytest

from src.fusion.topic_answer_checks import validate_topic_answer, validate_contract, freeze_contract
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import content_hash
from tests.fusion_security.test_package_play_store import play, runtime, service
from tests.fusion_security.test_single_player_topics import setup, ask_topic, fallback
from tests.fusion_security.test_package_guided_flow import enter
from tests.fusion_security.test_full_play_decisions import output


def contract(**changes):
    return {'schema_version':'topic-answer-check/1.0','required_terms':[],
            'public_basis':[{'collection':'evidence','id':'public-card'}],
            'reported_passages':[{'collection':'knowledge','id':'own-book','passage_ids':['p0001']}],
            'reject_public_personal_observation':True,'reject_unsupported_certainty':True,**changes}


def speech(text, basis=None):
    return {'segments':[{'mode':'REPORT','text':text,'basis':basis if basis is not None else
                        [{'collection':'evidence','id':'public-card','passage_ids':['p0001']}]}]}


@pytest.mark.parametrize('text', ['我醒来后看到散落的碎片。','我之前见过那个样子的人。','我发现桌面是湿的。'])
def test_public_evidence_cannot_become_personal_observation(text):
    with pytest.raises(PlayRulesError,match='PUBLIC_IS_NOT_PERSONAL'):
        validate_topic_answer(speech(text),{'answer_contract':contract()})


@pytest.mark.parametrize('text', [
    '我看到抽屉里有张1912年的报纸，头条说起旧案。',
    '我看到抽屉里有一张1912年3月4日的报纸，上面写着另一件事。',
    '我看到抽屉里有张清宣统元年的报纸，头条写着旧事。',
    '我看到抽屉里有张民国二十年的报纸，头条写着旧事。',
    '我看到了一张旧报纸，头条写着一场火灾。',
    '我看到报纸记载过一场火灾。',
    '我在报纸上看到旧案的记载。',
    '我发现一份旧报纸，头条写着火灾。',
])
def test_explicit_reading_of_an_authored_public_document_is_not_event_witnessing(text):
    c=contract(public_document_basis=[{'collection':'evidence','id':'public-card'}])
    validate_topic_answer(speech(text),{'answer_contract':c})
    with pytest.raises(PlayRulesError,match='PUBLIC_IS_NOT_PERSONAL'):
        validate_topic_answer(speech(text),{'answer_contract':contract()})


@pytest.mark.parametrize('text', [
    '我醒来后看到桌上散落的碎片。',
    '我看到他行凶，报纸头条写着旧案。',
    '我在报纸上看到记载。我醒来后看到满地碎片。',
])
def test_document_exception_cannot_launder_physical_observation(text):
    with pytest.raises(PlayRulesError,match='PUBLIC_IS_NOT_PERSONAL'):
        validate_topic_answer(speech(text),{'answer_contract':contract(
            public_document_basis=[{'collection':'evidence','id':'public-card'}])})


def test_document_exception_is_bound_to_the_actual_cited_material():
    with pytest.raises(PlayRulesError,match='PUBLIC_IS_NOT_PERSONAL'):
        validate_topic_answer(speech('我在报纸上看到旧案。'),{'answer_contract':contract(
            public_document_basis=[{'collection':'evidence','id':'another-card'}])})
    c={'schema_version':'topic-answer-check/1.0','public_document_basis':[{'collection':'evidence','id':'doc'}]}
    with pytest.raises(ValueError):validate_contract(c,{}, {'initial'})
    with pytest.raises(ValueError):validate_contract(c,{('evidence','doc'):{'text':'地上有碎片。'}}, {'initial'})
    validate_contract(c,{('evidence','doc'):{'text':'报纸上有一行标题。'}}, {'initial'})


def test_mixed_third_party_and_own_passages_cannot_be_merged_as_first_person():
    with pytest.raises(PlayRulesError,match='REPORT_IS_NOT_PERSONAL'):
        validate_topic_answer(speech('我之前见过那个少年描述的人。',
            [{'collection':'knowledge','id':'own-book','passage_ids':['p0001','p0002']}]),{'answer_contract':contract()})
    validate_topic_answer(speech('我听说少年见过那个样子的人。',
        [{'collection':'knowledge','id':'own-book','passage_ids':['p0001']}]),{'answer_contract':contract()})


@pytest.mark.parametrize('word',['肯定','一定','绝对','准是','必然','百分之百'])
def test_uncertain_topic_does_not_accept_definite_cause(word):
    with pytest.raises(PlayRulesError,match='CERTAINTY_UNSUPPORTED'):
        validate_topic_answer(speech(f'这{word}是同一个原因。'),{'answer_contract':contract()})


@pytest.mark.parametrize('text',['不能肯定。','不一定是这样。','不能说这肯定就是答案。','并非必然如此。'])
def test_explicit_negated_certainty_is_valid(text):
    validate_topic_answer(speech(text),{'answer_contract':contract()})


@pytest.mark.parametrize('text',['不能肯定前一件，但这一定是他干的。','我不得不肯定是他。','我不是不肯定。'])
def test_negation_does_not_hide_later_or_double_negated_assertions(text):
    with pytest.raises(PlayRulesError,match='CERTAINTY_UNSUPPORTED'):
        validate_topic_answer(speech(text),{'answer_contract':contract()})


def test_required_source_name_and_conditional_qualifier():
    c=contract(required_terms=[['许先生','许某']],conditional_terms=[{'when_any':['尖角'],'requires_any':['似乎','可能']}])
    with pytest.raises(PlayRulesError,match='ANSWER_INCOMPLETE'):
        validate_topic_answer(speech('我听说以前有过这件事。'),{'answer_contract':c})
    with pytest.raises(PlayRulesError,match='QUALIFIER_REQUIRED'):
        validate_topic_answer(speech('记录提到许先生，画像上有尖角。'),{'answer_contract':c})
    validate_topic_answer(speech('记录提到许先生，画像上似乎有尖角。'),{'answer_contract':c})


def test_unversioned_existing_topics_are_not_retroactively_rejudged():
    validate_topic_answer(speech('我醒来后看到，肯定是这样。'),{'id':'legacy-topic'})


def test_unfounded_authored_terms_or_passage_are_rejected():
    materials={('knowledge','own-book'):{'text':'许先生说起此事。'}}
    c={'schema_version':'topic-answer-check/1.0','required_terms':{'initial':[['不存在的名字']]}}
    with pytest.raises(ValueError):validate_contract(c,materials,{'initial'})
    c['required_terms']={'initial':[['许先生']]};validate_contract(c,materials,{'initial'})
    c['reported_passages']=[{'collection':'knowledge','id':'own-book','passage_ids':['p9999']}]
    with pytest.raises(ValueError):validate_contract(c,materials,{'initial'})


def test_new_contract_rejects_paid_incomplete_reply_and_preserves_legacy_replay(play):
    package,doc,view=setup(play);view=enter(play,view)
    # First question is frozen before the optional acceptance version exists.
    _,view=ask_topic(play,view)
    req=view['single_player']['turns'][-1]['reply_request']
    play.sdk.chat_completion.return_value=output({'segments':[{'mode':'REPORT','text':'我记得那件事。',
        'basis':[{'collection':'knowledge','id':'initial-b'}]}]})
    view=asyncio.run(play.play.respond(view['play_id'],req,1))
    assert view['single_player']['turns'][-1]['status']=='OK'
    doc['topics'][0]['responders'][0]['answer_contract']={
        'schema_version':'topic-answer-check/1.0','required_terms':{'clarify':[['PRIVATE_BOOK_b']]},
        'reject_public_personal_observation':True,'reject_unsupported_certainty':True}
    play.play.single_player_content={content_hash(package):SinglePlayerContent(package,doc)}
    _,view=ask_topic(play,view,'clarify')
    req=view['single_player']['turns'][-1]['reply_request']
    view=asyncio.run(play.play.respond(view['play_id'],req,1))
    assert view['single_player']['turns'][-1]['status']=='FAILED'
    assert view['single_player']['turns'][0]['status']=='OK' and view['budget']['used_tokens']==220
    _,view=fallback(play,view)
    assert view['single_player']['turns'][-1]['status']=='FALLBACK'
    assert len(view['role_responses']['entries'])==1
    play.db.commit()
    replay=service(play,play.db).get(view['play_id'],1)
    assert [t['status'] for t in replay['single_player']['turns']]==['OK','FALLBACK']
    assert replay['budget']==view['budget'] and play.sdk.chat_completion.await_count==2
