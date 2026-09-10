"""Isolated real-SDK boundary tests; no paid or commercial I/O."""
import asyncio
from decimal import Decimal
import json
from unittest.mock import AsyncMock, Mock

import pytest

from src.fusion import audit_semantic_smoke as smoke, authoring_model
from src.fusion.package_validation import content_hash
from tests.fusion_security.test_authoring_model import fixture_config, good_response
from tests.fusion_security.test_indexed_audit import indexed_report


def stub(monkeypatch, failure=None):
    async def respond(messages, **kwargs):
        user = json.loads(messages[1].content)
        assert user['task'] == 'AUDIT'
        assert 'engine_traces' not in user and 'criteria' not in user and 'oracles' not in user
        assert kwargs['response_format']['json_schema']['strict']
        if failure == 'timeout': raise TimeoutError('PRIVATE_ERROR')
        report = indexed_report(user['candidate']) | {'schema_version':'bounded-audit-draft/1.0', 'status':'COMPLETE'}
        # A valid BLOCKER is deliberately kept; collection is not quality pass.
        report['findings'][0]['severity'] = 'BLOCKER'
        if failure == 'format': report.pop('status')
        if failure == 'incomplete': report['status'] = 'INCOMPLETE'
        if failure == 'source-index': report['findings'][0]['source_indexes'] = [99]
        if failure == 'target': report['findings'][0]['target'] = {'collection':'evidence', 'id':'private-missing-target'}
        response = good_response(report)
        if failure == 'length': response.finish_reason = 'length'
        return response
    sdk = Mock(_client=None, chat_completion=AsyncMock(side_effect=respond))
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(authoring_model, 'OpenAILLMService', factory)
    return factory, sdk


def preview(root):
    code, receipt = asyncio.run(smoke.run_suite(fixture_config(), output_root=root))
    assert code == 0 and receipt['status'] == 'PREVIEW'
    return receipt


def test_preview_freezes_oracles_separately_and_holdout_allows_remaining(tmp_path, monkeypatch):
    factory, _ = stub(monkeypatch)
    a, b = preview(tmp_path/'private'), preview(tmp_path/'private')
    assert a['suite_hash'] == b['suite_hash']
    from pathlib import Path
    suite = json.loads((Path(a['directory'])/'suite.json').read_text())
    assert content_hash(suite) == a['suite_hash']
    assert len(suite['packets']) == len(suite['oracles']) == 4
    assert {x['case'] for x in suite['packets']} == set(smoke.CASE_IDS)
    holdout = suite['oracles'][3]
    assert all(x['settled'] and x['before_advance_remaining_points'] == 2 for x in holdout['engine_traces'])
    for packet in suite['packets']:
        payload = json.loads(packet['messages'][1]['content'])
        assert payload['candidate_observations']['package_hash'] == content_hash(packet['candidate'])
        assert payload['candidate'] == packet['context']['rule_plan']
        assert 'oracles' not in payload and 'criteria' not in payload
    assert a['model_requests'] == 0 and a['charged_cost_cny'] == '0'
    assert Decimal(a['planned_reservation_cny']) <= smoke.MAX_TOTAL_CNY
    factory.assert_not_called()


@pytest.mark.parametrize('failure', [None, 'format', 'incomplete', 'length', 'timeout', 'source-index', 'target'])
def test_four_single_attempts_or_stop_on_unknown_with_durable_accounting(tmp_path, monkeypatch, failure):
    _, sdk = stub(monkeypatch, failure)
    root=tmp_path/'private'; frozen = preview(root)
    code, result = asyncio.run(smoke.run_suite(fixture_config(), output_root=root, execute=True,
        paid_authorized=True, expected_suite_hash=frozen['suite_hash']))
    count = 1 if failure == 'timeout' else 4
    assert sdk.chat_completion.await_count == result['model_requests'] == count
    assert code == (3 if failure == 'timeout' else 0)
    assert result['semantic_status'] == 'UNREVIEWED' and not result['publication_ready']
    assert not result['runtime_ready']
    assert Decimal(result['charged_cost_cny']) > 0
    from pathlib import Path
    disk = json.loads((Path(result['directory'])/'receipt.json').read_text())
    assert disk == result
    assert len(list(Path(result['directory']).glob('dispatch-*.json'))) == count
    assert len(list(Path(result['directory']).glob('result-*.json'))) == count
    if failure is None:
        assert all(r['output']['findings'][0]['severity'] == 'BLOCKER' for r in result['results'])
    else:
        assert all(r['output'] is None for r in result['results'])
    if failure == 'timeout':
        assert result['charged_cost_cny'] == result['reserved_cny']
    if failure in ('source-index', 'target'):
        code, path = ('AUDIT_SOURCE_INDEX_UNAVAILABLE', '/findings/0/source_indexes/0') if failure == 'source-index' else ('AUDIT_TARGET_NOT_FOUND', '/findings/0/target')
        assert result['results'][0]['receipt']['output_diagnostics'] == [{'code':code, 'entity_path':path}]
        assert 'private-missing-target' not in json.dumps(result)
    _, again = asyncio.run(smoke.run_suite(fixture_config(), output_root=root, execute=True,
        paid_authorized=True, expected_suite_hash=frozen['suite_hash']))
    assert again['result_code'] == 'SEMANTIC_SUITE_ALREADY_CLAIMED'
    assert sdk.chat_completion.await_count == count
    assert 'PRIVATE_ERROR' not in json.dumps(result)


@pytest.mark.parametrize('mutation', ['authorization', 'hash', 'missing-hash', 'budget'])
def test_pre_dispatch_gates_leave_sdk_unused(tmp_path, monkeypatch, mutation):
    factory, _ = stub(monkeypatch)
    root=tmp_path/'private'; frozen = preview(root)
    if mutation == 'budget': monkeypatch.setattr(smoke, 'MAX_TOTAL_CNY', Decimal('0.001'))
    code, result = asyncio.run(smoke.run_suite(fixture_config(), output_root=root, execute=True,
        paid_authorized=mutation != 'authorization', expected_suite_hash=None if mutation == 'missing-hash' else 'f'*64 if mutation == 'hash' else frozen['suite_hash']))
    assert code == 3 and result['model_requests'] == 0
    factory.assert_not_called()
    assert not list(root.glob('executed-*.json'))


def test_local_write_failure_after_dispatch_preserves_reservation_and_claim(tmp_path, monkeypatch):
    _, sdk = stub(monkeypatch)
    root=tmp_path/'private'; frozen = preview(root)
    original = smoke._json
    def fail_result(path, value):
        if path.name == 'result-0.json': raise OSError('PRIVATE_DISK_DETAIL')
        original(path,value)
    monkeypatch.setattr(smoke, '_json', fail_result)
    _, result = asyncio.run(smoke.run_suite(fixture_config(), output_root=root, execute=True,
        paid_authorized=True, expected_suite_hash=frozen['suite_hash']))
    assert result['result_code'] == 'SEMANTIC_LOCAL_FAILURE'
    assert result['model_requests'] == sdk.chat_completion.await_count == 1
    assert Decimal(result['charged_cost_cny']) > 0 and Decimal(result['reserved_cny']) > 0
    assert len(list(root.glob('executed-*.json'))) == 1
    assert 'PRIVATE_DISK_DETAIL' not in json.dumps(result)


def test_cli_preview_never_reads_paid_authorization_or_calls_sdk(tmp_path, monkeypatch, capsys):
    factory,_ = stub(monkeypatch)
    monkeypatch.setattr(smoke,'load_selected_config',lambda provider: fixture_config())
    paid=Mock(side_effect=AssertionError('not authorized'))
    monkeypatch.setattr(smoke,'read_paid_authorization',paid)
    assert smoke.main(['--output-root',str(tmp_path/'private')]) == 0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='PREVIEW' and 'results' not in result
    factory.assert_not_called(); paid.assert_not_called()
