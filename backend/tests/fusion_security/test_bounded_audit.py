"""Completeness is mandatory; compacting does not repair or approve a report."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from src.fusion.authoring_model import (AuthoringModel, AuthoringModelError, parse_bounded_audit,
    safe_response_finish, validate_response_finish)
from src.schemas.authoring import AuthoringRequestV16, parse_authoring_request
from src.schemas.bounded_audit import BoundedAuditDraft
from tests.fusion_security.test_indexed_audit import indexed_report
from tests.fusion_security.test_authoring_model import fixture_config
from tests.fusion_security.test_authoring_v12 import synthetic_v12_context_and_package


def fixture():
    context, package = synthetic_v12_context_and_package()
    context.update(rule_plan=deepcopy(package), compiler_mode='CONFIRM_FROZEN_TEXT', audit_mode='BOUNDED_TARGET_SOURCE_INDEXES')
    draft = indexed_report(package) | {'schema_version': 'bounded-audit-draft/1.0', 'status': 'COMPLETE'}
    model = AuthoringModel(fixture_config(), 'script-package/1.2', rule_plan_enabled=True,
        confirm_frozen_text=True, indexed_audit=True, bounded_audit=True)
    return context, package, draft, model


def test_bounded_report_preserves_selected_sources_and_never_approves():
    context, package, draft, model = fixture()
    before = deepcopy((context, package, draft))
    result = parse_bounded_audit(draft, package)
    assert result['schema_version'] == 'script-audit/1.1'
    assert result['findings'][0]['sources'] and 'status' not in result and 'approved' not in result
    assert (context, package, draft) == before
    prepared = model.prepare('AUDIT', context, package)
    assert prepared.max_completion_tokens == 4096
    assert prepared.request_contract['version'] == 'bailian-authoring-json/1.6'
    body = {k:v for k,v in context.items() if k in AuthoringRequestV16.model_fields} | {'idempotency_key':'bounded-test'}
    assert parse_authoring_request(body).model_dump() == body


@pytest.mark.parametrize('mutation,code', [
    ('incomplete','AUDIT_INCOMPLETE'), ('missing-status','AUDIT_BOUNDED_OUTPUT_INVALID'),
    ('long-message','AUDIT_BOUNDED_OUTPUT_INVALID'), ('long-summary','AUDIT_BOUNDED_OUTPUT_INVALID'),
    ('too-many','AUDIT_BOUNDED_OUTPUT_INVALID'), ('coverage','AUDIT_BOUNDED_OUTPUT_INVALID'),
    ('empty-source','AUDIT_BOUNDED_OUTPUT_INVALID'), ('index','AUDIT_INDEXED_OUTPUT_INVALID'),
    ('approval','AUDIT_BOUNDED_OUTPUT_INVALID'), ('old-version','AUDIT_BOUNDED_OUTPUT_INVALID')])
def test_bounded_audit_rejects_incomplete_or_invalid_without_repair(mutation, code):
    _, package, draft, _ = fixture()
    if mutation == 'incomplete': draft['status'] = 'INCOMPLETE'
    elif mutation == 'missing-status': draft.pop('status')
    elif mutation == 'long-message': draft['findings'][0]['message'] = '字' * 161
    elif mutation == 'long-summary': draft['summary'] = '字' * 241
    elif mutation == 'too-many': draft['findings'] = [deepcopy(draft['findings'][0]) | {'id':f'f-{i}'} for i in range(11)]
    elif mutation == 'coverage': draft['coverage'].pop()
    elif mutation == 'empty-source': draft['findings'][0]['source_indexes'] = []
    elif mutation == 'index': draft['findings'][0]['source_indexes'] = [99]
    elif mutation == 'approval': draft['approved'] = True
    else: draft['schema_version'] = 'indexed-audit-draft/1.0'
    with pytest.raises(AuthoringModelError, match=code): parse_bounded_audit(draft, package)


def test_bounded_mode_does_not_upgrade_old_model_or_prompt():
    context, package, _, _ = fixture()
    old = AuthoringModel(fixture_config(), 'script-package/1.2', rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True)
    assert old.snapshot()['prompt_hashes']['AUDIT'] == 'd2ad1d39ebfe36afc6f4c18a6096b92559422c98ad17073e95b1f685fc2f3f04'
    with pytest.raises(AuthoringModelError, match='AUTHORING_CONTRACT_MISMATCH'): old.prepare('AUDIT', context, package)
    with pytest.raises(AuthoringModelError): AuthoringModel(fixture_config(), bounded_audit=True)


@pytest.mark.parametrize('value,expected', [('stop','stop'),('length','length'),('content_filter','content_filter'),
    ('tool_calls','tool_calls'),('function_call','function_call'),(None,None),('PRIVATE_ERROR','OTHER'),({},'OTHER'),(False,'OTHER')])
def test_finish_diagnostics_only_allow_known_fixed_values(value, expected):
    assert safe_response_finish(value) == expected
    assert validate_response_finish(expected) == expected
    if expected == 'OTHER':
        with pytest.raises(ValueError): validate_response_finish(value)


def test_bounded_static_schema_matches_actual_parser():
    root = Path(__file__).resolve().parents[3] / 'docs/contracts'
    for name, model in [('authoring-request.v1.6.schema.json',AuthoringRequestV16),('bounded-audit-draft.v1.0.schema.json',BoundedAuditDraft)]:
        assert json.loads((root/name).read_text()) == model.model_json_schema()


@pytest.mark.parametrize('mutation,path', [('status','/status'),('length','/findings/0/message'),
    ('source','/findings/0/source_indexes/0'),('extra','/findings/0'),('root','/')])
def test_bounded_diagnostics_locate_only_safe_positions(mutation, path):
    _, package, draft, _ = fixture()
    if mutation == 'status': draft.pop('status')
    elif mutation == 'length': draft['findings'][0]['message'] = 'PRIVATE_MESSAGE' * 100
    elif mutation == 'source': draft['findings'][0]['source_indexes'] = ['PRIVATE_INDEX']
    elif mutation == 'extra': draft['findings'][0]['PRIVATE_FIELD'] = 'PRIVATE_VALUE'
    else: draft['PRIVATE_FIELD'] = 'PRIVATE_VALUE'
    with pytest.raises(AuthoringModelError) as caught: parse_bounded_audit(draft, package)
    assert caught.value.details == [{'code':'AUDIT_BOUNDED_SCHEMA_INVALID','entity_path':path}]
    assert 'PRIVATE_' not in json.dumps(caught.value.details)


@pytest.mark.parametrize('path', ['/findings/10/message','/findings/0/PRIVATE_NAME','/findings/-1', '/findings/0/source_indexes/100'])
def test_bounded_diagnostics_refuse_private_or_out_of_range_paths(path):
    from src.fusion.authoring_model import validate_output_diagnostics
    with pytest.raises(ValueError): validate_output_diagnostics([{'code':'AUDIT_BOUNDED_SCHEMA_INVALID','entity_path':path}])
