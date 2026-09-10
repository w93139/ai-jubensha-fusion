"""Schema shown to the model must describe the exact top-level parser input."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, parse_bounded_audit
from src.schemas.authoring import AuthoringRequestV17, parse_authoring_request
from src.schemas.bounded_audit import BoundedAuditDraft
from tests.fusion_security.test_bounded_audit import fixture
from tests.fusion_security.test_authoring_model import fixture_config


def direct_fixture():
    context, package, draft, old = fixture()
    context['audit_mode'] = 'DIRECT_BOUNDED_SOURCE_INDEXES'
    model = AuthoringModel(fixture_config(), 'script-package/1.2', rule_plan_enabled=True,
        confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True)
    return context, package, draft, old, model


def test_model_facing_schema_is_actual_root_schema_with_resolvable_references():
    context, package, draft, old, model = direct_fixture()
    prepared = model.prepare('AUDIT', context, package)
    sent_schema = json.loads(prepared.messages[0].content.split('\nJSON Schema：\n')[1])
    assert sent_schema == BoundedAuditDraft.model_json_schema()
    validator = Draft202012Validator(sent_schema)
    validator.validate(draft)  # Nonempty findings resolve #/$defs from the root.
    assert not validator.is_valid({}) and not validator.is_valid({'output':draft})
    assert Draft202012Validator(old._schema('AUDIT')).is_valid({})  # Reproduces old mismatch.
    assert old.snapshot()['schema_hashes']['AUDIT'] == '47de5f6afab6930d7589cd6a9bfd2fe470a4e1ef7788de66f6f04af58a2fe5e1'
    assert prepared.max_completion_tokens == 4096
    assert prepared.request_contract['version'] == 'bailian-authoring-json/1.7'
    assert model._prompt('AUDIT') == old._prompt('AUDIT')  # Fix only schema placement.
    assert parse_bounded_audit(draft, package)['findings']


@pytest.mark.parametrize('mutation', ['wrapper','missing-status','source-type','long-message','extra'])
def test_actual_schema_and_parser_agree_on_invalid_shape(mutation):
    _, package, draft, _, model = direct_fixture()
    if mutation == 'wrapper': draft = {'output':draft}
    elif mutation == 'missing-status': draft.pop('status')
    elif mutation == 'source-type': draft['findings'][0]['source_indexes'] = ['0']
    elif mutation == 'long-message': draft['findings'][0]['message'] = '字'*161
    else: draft['approved'] = True
    assert not Draft202012Validator(model._schema('AUDIT')).is_valid(draft)
    with pytest.raises(AuthoringModelError): parse_bounded_audit(draft, package)


def test_direct_request_version_is_explicit_and_rejects_old_model():
    context, package, _, old, model = direct_fixture()
    body = {k:v for k,v in context.items() if k in AuthoringRequestV17.model_fields} | {'idempotency_key':'direct-test'}
    assert parse_authoring_request(body).model_dump() == body
    schema_file = Path(__file__).resolve().parents[3]/'docs/contracts/authoring-request.v1.7.schema.json'
    assert json.loads(schema_file.read_text()) == AuthoringRequestV17.model_json_schema()
    before = deepcopy(context)
    with pytest.raises(AuthoringModelError, match='AUTHORING_CONTRACT_MISMATCH'): old.prepare('AUDIT',context,package)
    assert context == before
    with pytest.raises(AuthoringModelError): AuthoringModel(fixture_config(),direct_audit_schema=True)


def strict_fixture():
    context, package, draft, _, _ = direct_fixture()
    context['audit_mode'] = 'STRICT_BOUNDED_SOURCE_INDEXES'
    model = AuthoringModel(fixture_config(), 'script-package/1.2', rule_plan_enabled=True,
        confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True)
    return context, package, draft, model


def test_strict_schema_is_sent_to_provider_and_its_bytes_are_reserved():
    from src.fusion.package_validation import canonical_json
    from src.schemas.authoring import AuthoringRequestV18
    context, package, _, model = strict_fixture()
    prepared = model.prepare('AUDIT',context,package)
    response_format = prepared.request_contract['response_format']
    assert response_format == {'type':'json_schema','json_schema':{'name':'bounded_audit','strict':True,'schema':BoundedAuditDraft.model_json_schema()}}
    assert prepared.input_tokens == sum(len(m.content.encode()) for m in prepared.messages) + 512 + len(canonical_json(response_format).encode())
    assert prepared.reservation.prompt_tokens == prepared.input_tokens
    assert prepared.max_completion_tokens == 4096
    assert model.prepare('COMPILE',context).request_contract['response_format'] == {'type':'json_object'}
    body = {k:v for k,v in context.items() if k in AuthoringRequestV18.model_fields} | {'idempotency_key':'strict-test'}
    assert parse_authoring_request(body).model_dump() == body
    path = Path(__file__).resolve().parents[3]/'docs/contracts/authoring-request.v1.8.schema.json'
    assert json.loads(path.read_text()) == AuthoringRequestV18.model_json_schema()


@pytest.mark.parametrize('mutation',['disabled','removed','changed-schema','old-mode'])
def test_strict_prepared_ledger_rejects_format_tampering(mutation):
    from src.fusion.authoring_jobs import _prepared, AuthoringJobError
    from src.fusion.package_validation import content_hash
    context, package, _, model = strict_fixture()
    p = model.prepare('AUDIT',context,package)
    raw = {'step':p.step,'prompt_hash':p.prompt_hash,'contract_hash':p.contract_hash,'input_tokens':p.input_tokens,
        'max_completion_tokens':p.max_completion_tokens,'reservation':p.reservation.to_metadata(),'request_contract':deepcopy(p.request_contract)}
    assert _prepared(raw,'AUDIT',model.snapshot()) == raw
    if mutation == 'disabled': raw['request_contract']['response_format']['json_schema']['strict'] = False
    elif mutation == 'removed': raw['request_contract']['response_format']['json_schema'].pop('schema')
    elif mutation == 'changed-schema': raw['request_contract']['response_format']['json_schema']['schema']['required'] = []
    else: raw['request_contract']['response_format'] = {'type':'json_object'}
    raw['contract_hash'] = content_hash({'request':raw['request_contract'],'configuration':model.snapshot()})
    with pytest.raises(AuthoringJobError): _prepared(raw,'AUDIT',model.snapshot())


@pytest.mark.parametrize('text', ['', ' ', '\n\t', '已', '3', '已检查五个维度，仍需人工核对。', '\n中\n', 'a b', '😀', '\u2003', 'a'*241])
def test_portable_pattern_preserves_local_language_and_accepts_sentences_with_fullmatch(text):
    import re
    from src.fusion.authoring_model import portable_audit_schema
    original = BoundedAuditDraft.model_json_schema()['properties']['summary']
    portable = portable_audit_schema()['properties']['summary']
    assert Draft202012Validator(original).is_valid(text) == Draft202012Validator(portable).is_valid(text)
    assert bool(re.search(original['pattern'],text)) == bool(re.fullmatch(portable['pattern'],text))
    assert {k:v for k,v in original.items() if k != 'pattern'} == {k:v for k,v in portable.items() if k != 'pattern'}


def test_portable_mode_freezes_its_exact_wire_schema_and_keeps_old_snapshot():
    from src.schemas.authoring import AuthoringRequestV19
    from src.fusion.authoring_model import portable_audit_schema
    context, package, draft, old = strict_fixture()
    context['audit_mode'] = 'PORTABLE_STRICT_SOURCE_INDEXES'
    model = AuthoringModel(fixture_config(),'script-package/1.2',rule_plan_enabled=True,
        confirm_frozen_text=True,indexed_audit=True,bounded_audit=True,direct_audit_schema=True,
        strict_audit_schema=True,portable_audit_patterns=True)
    prepared = model.prepare('AUDIT',context,package)
    schema = prepared.request_contract['response_format']['json_schema']['schema']
    assert schema == portable_audit_schema() == model._schema('AUDIT')
    Draft202012Validator(schema).validate(draft)
    assert prepared.request_contract['version'] == 'bailian-authoring-json/1.9'
    assert old.snapshot()['schema_hashes']['AUDIT'] == '8fafaf97fa9a4b573b012a86ac8804737e9e03448b91fba7458278a79fa01c5e'
    with pytest.raises(AuthoringModelError,match='AUTHORING_CONTRACT_MISMATCH'):old.prepare('AUDIT',context,package)
    body = {k:v for k,v in context.items() if k in AuthoringRequestV19.model_fields} | {'idempotency_key':'portable-test'}
    assert parse_authoring_request(body).model_dump() == body
    path=Path(__file__).resolve().parents[3]/'docs/contracts/authoring-request.v1.9.schema.json'
    assert json.loads(path.read_text()) == AuthoringRequestV19.model_json_schema()


@pytest.mark.parametrize('field,value', [('summary','  '),('message','\n\t'),('id','INVALID ID')])
def test_provider_regex_projection_never_relaxes_local_acceptance(field,value):
    from src.fusion.authoring_model import typed_audit_schema
    _,package,draft,_ = strict_fixture()
    if field == 'summary': draft[field] = value
    else: draft['findings'][0][field] = value
    assert Draft202012Validator(typed_audit_schema()).is_valid(draft)
    with pytest.raises(AuthoringModelError): parse_bounded_audit(draft,package)


def test_typed_schema_removes_only_regex_and_accounts_for_actual_provider_schema():
    from src.fusion.authoring_model import typed_audit_schema
    from src.fusion.package_validation import canonical_json
    from src.schemas.authoring import AuthoringRequestV110
    original = BoundedAuditDraft.model_json_schema()
    projected = typed_audit_schema()
    def compare(a,b):
        if isinstance(a,dict):
            assert set(b) == set(a)-{'pattern'}
            for key in b:compare(a[key],b[key])
        elif isinstance(a,list):
            assert len(a)==len(b)
            for x,y in zip(a,b):compare(x,y)
        else:assert a==b
    compare(original,projected)
    context,package,_,_ = strict_fixture()
    context['audit_mode']='TYPED_STRICT_SOURCE_INDEXES'
    model=AuthoringModel(fixture_config(),'script-package/1.2',rule_plan_enabled=True,confirm_frozen_text=True,
        indexed_audit=True,bounded_audit=True,direct_audit_schema=True,strict_audit_schema=True,
        portable_audit_patterns=True,typed_audit_schema=True)
    p=model.prepare('AUDIT',context,package)
    assert p.request_contract['response_format']['json_schema']['schema']==projected
    assert p.input_tokens==sum(len(m.content.encode()) for m in p.messages)+512+len(canonical_json(p.request_contract['response_format']).encode())
    assert p.request_contract['version']=='bailian-authoring-json/1.10'
    body={k:v for k,v in context.items() if k in AuthoringRequestV110.model_fields}|{'idempotency_key':'typed-test'}
    assert parse_authoring_request(body).model_dump()==body
    path=Path(__file__).resolve().parents[3]/'docs/contracts/authoring-request.v1.10.schema.json'
    assert json.loads(path.read_text())==AuthoringRequestV110.model_json_schema()
