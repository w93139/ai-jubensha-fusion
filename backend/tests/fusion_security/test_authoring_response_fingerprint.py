"""Digests identify SDK content before parsing; old receipts remain unchanged."""
import asyncio
from copy import deepcopy
from hashlib import sha256

import pytest

from src.fusion.authoring_jobs import AuthoringJobStore
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, validate_response_fingerprint
from tests.fusion_security.test_authoring_model import (
    blocked_draft, fixture_config, good_response, stub_sdk, synthetic_context_and_package,
)


@pytest.mark.parametrize('invalid', [False, {}, {'content_sha256': 'PRIVATE'},
    {'content_sha256': 'a'*64, 'response_id_sha256': None, 'raw': 'PRIVATE'},
    {'content_sha256': 'A'*64, 'response_id_sha256': None}])
def test_fingerprint_rejects_raw_or_malformed_values(invalid):
    with pytest.raises(ValueError, match='^AUTHORING_RESPONSE_FINGERPRINT_INVALID$'):
        validate_response_fingerprint(invalid)


@pytest.mark.parametrize('failure', [None, 'parse', 'usage'])
def test_fingerprint_is_sdk_content_digest_even_for_rejected_response(monkeypatch, failure):
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    response = good_response(blocked_draft())
    response.request_id = 'SENSITIVE_PROVIDER_RESPONSE_ID'
    if failure == 'parse': response.content = 'NOT_JSON_PRIVATE_TEXT'
    if failure == 'usage': response.usage = None
    _, client = stub_sdk(monkeypatch, response)
    if failure:
        with pytest.raises(AuthoringModelError) as caught:
            asyncio.run(model.call('COMPILE', context))
        receipt = caught.value.receipt
    else:
        receipt = asyncio.run(model.call('COMPILE', context))['receipt']
    assert receipt['response_fingerprint'] == {
        'content_sha256': sha256(response.content.encode()).hexdigest(),
        'response_id_sha256': sha256(response.request_id.encode()).hexdigest(),
    }
    assert response.request_id not in str(receipt) and response.content not in str(receipt)
    assert client.chat_completion.await_count == 1


def test_fingerprint_network_failure_has_no_content_digest(monkeypatch):
    context, _ = synthetic_context_and_package()
    stub_sdk(monkeypatch, exception=TimeoutError())
    with pytest.raises(AuthoringModelError) as caught:
        asyncio.run(AuthoringModel(fixture_config()).call('COMPILE', context))
    assert caught.value.receipt['response_fingerprint'] is None


def test_fingerprint_persist_validation_preserves_old_receipts(monkeypatch):
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    stub_sdk(monkeypatch, good_response(blocked_draft()))
    prepared = model.prepare('COMPILE', context)
    result = asyncio.run(model.call('COMPILE', context, prepared=prepared))
    wire = {'step': 'COMPILE', 'prompt_hash': prepared.prompt_hash, 'contract_hash': prepared.contract_hash,
            'reservation': prepared.reservation.to_metadata()}
    for receipt in [result['receipt'], {key: value for key, value in result['receipt'].items() if key != 'response_fingerprint'}]:
        before = deepcopy(receipt)
        status, _, restored, _ = AuthoringJobStore._finish_data(wire, model.snapshot(), result['output'], receipt, None)
        assert status == 'SUCCEEDED' and restored == before and receipt == before


def test_finish_metadata_preserves_missing_legacy_field_and_refuses_raw_value(monkeypatch):
    from src.fusion.authoring_jobs import AuthoringJobError
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    stub_sdk(monkeypatch, good_response(blocked_draft()))
    prepared = model.prepare('COMPILE', context)
    result = asyncio.run(model.call('COMPILE', context, prepared=prepared))
    wire = {'step':'COMPILE','prompt_hash':prepared.prompt_hash,'contract_hash':prepared.contract_hash,
        'reservation':prepared.reservation.to_metadata()}
    receipt = {k:v for k,v in result['receipt'].items() if k != 'response_finish'}
    before = deepcopy(receipt)
    _, _, restored, _ = AuthoringJobStore._finish_data(wire, model.snapshot(), result['output'], receipt, None)
    assert receipt == restored == before and 'response_finish' not in restored
    with pytest.raises(AuthoringJobError):
        AuthoringJobStore._finish_data(wire, model.snapshot(), result['output'], receipt | {'response_finish':'PRIVATE_RAW'}, None)


@pytest.mark.parametrize('finish', ['length', 'content_filter', 'OTHER', None])
def test_success_receipt_cannot_claim_nonstop_finish(monkeypatch, finish):
    from src.fusion.authoring_jobs import AuthoringJobError
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    stub_sdk(monkeypatch, good_response(blocked_draft()))
    prepared = model.prepare('COMPILE', context)
    result = asyncio.run(model.call('COMPILE', context))
    wire = {'step':'COMPILE','prompt_hash':prepared.prompt_hash,'contract_hash':prepared.contract_hash,
        'reservation':prepared.reservation.to_metadata()}
    with pytest.raises(AuthoringJobError):
        AuthoringJobStore._finish_data(wire, model.snapshot(), result['output'], result['receipt'] | {'response_finish':finish}, None)
