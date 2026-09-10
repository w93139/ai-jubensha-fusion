"""Rule/source comparison against frozen fictional documents; no real model."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.routes import script_review_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.fusion.package_import import PackageImportService
from src.fusion.package_validation import content_hash
from src.fusion import rule_review
from src.fusion.rule_review import RuleReviewError, build_rule_review
from src.fusion.script_review import ScriptReviewService
from src.fusion.source_bundles import SourceBundleError
from tests.fusion_security.test_package_investigation_publication import make_investigation_sources
from tests.fusion_security.test_script_review import review_db


@pytest.fixture
def case(tmp_path):
    return make_investigation_sources(tmp_path / "case")


def fingerprints(root):
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_rule_review_detects_material_action_overlap_without_repair_or_writes(case):
    document = deepcopy(case.package)
    card = next(item for item in document['evidence'] if item['id'] == 'b-action-card')
    card['release']['required_public_evidence_ids'] = ['key']
    original = deepcopy(document)
    before = fingerprints(case.root)
    result = build_rule_review(document, case.bundle_hash, case.sources, limit=50)
    row = next(row for row in result['rows'] if row['target']['id'] == 'b-action-card')
    assert row['notices'] == [{'code': 'PUBLIC_PREREQUISITE_ALREADY_REQUIRED_BY_ACTION',
                              'action_id': 'open-case', 'evidence_id': 'key'}]
    assert row['rules']['release']['required_public_evidence_ids'] == ['key']
    declaration = next(line.removeprefix('规则声明：') for line in row['sources'][0]['text'].splitlines()
                       if line.startswith('规则声明：'))
    assert json.loads(declaration)['release'].get('required_public_evidence_ids', []) == []
    assert 'B_ACTION_REWARD_SENTINEL' in row['sources'][0]['text']
    assert '# evidence-' not in row['sources'][0]['text'].split('\n', 1)[1]
    assert result['semantic_status'] == 'UNREVIEWED' and not result['publication_ready']
    assert result['package_hash'] == content_hash(document)
    assert document == original and fingerprints(case.root) == before


def test_rule_review_keeps_independent_material_condition_and_does_not_claim_semantic_pass(case):
    document = deepcopy(case.package)
    card = next(item for item in document['evidence'] if item['id'] == 'b-action-card')
    card['release']['required_public_evidence_ids'] = ['clock']
    result = build_rule_review(document, case.bundle_hash, case.sources, limit=50)
    row = next(row for row in result['rows'] if row['target']['id'] == 'b-action-card')
    assert row['notices'] == [] and row['rules']['release']['required_public_evidence_ids'] == ['clock']
    assert result['semantic_status'] == 'UNREVIEWED'


def test_rule_review_pagination_is_complete_stable_and_read_only(case):
    before = fingerprints(case.root)
    whole = build_rule_review(case.package, case.bundle_hash, case.sources, limit=50)
    rows, offset = [], 0
    while offset is not None:
        page = build_rule_review(case.package, case.bundle_hash, case.sources, offset=offset, limit=3)
        rows.extend(page['rows'])
        offset = page['next_offset']
    assert rows == whole['rows'] and len(rows) == whole['row_count']
    assert fingerprints(case.root) == before


@pytest.mark.parametrize('offset,limit', [(-1, 20), (True, 20), (0, False), (0, 51), (15101, 20)])
def test_rule_review_rejects_unbounded_page_before_filesystem(case, offset, limit):
    store = Mock()
    with pytest.raises(RuleReviewError, match='PAGE_INVALID'):
        build_rule_review(case.package, case.bundle_hash, store, offset=offset, limit=limit)
    store.verify.assert_not_called()


def test_rule_review_rejects_wrong_scope_corrupt_file_and_old_contract(case):
    old = deepcopy(case.package); old['schema_version'] = 'script-package/1.1'
    with pytest.raises(RuleReviewError, match='CONTRACT_UNSUPPORTED'):
        build_rule_review(old, case.bundle_hash, case.sources)
    wrong = deepcopy(case.package); wrong['script_key'] = 'another-script'
    with pytest.raises(RuleReviewError, match='SOURCE_INVALID'):
        build_rule_review(wrong, case.bundle_hash, case.sources)
    source = case.root / 'source-bundles' / case.bundle_hash / 'files' / 'revised.md'
    source.write_text('Corrupt fictional source')
    with pytest.raises(RuleReviewError, match='SOURCE_INVALID'):
        build_rule_review(case.package, case.bundle_hash, case.sources)


def test_rule_review_rechecks_source_changed_after_verification(case, monkeypatch):
    original = case.sources.verify
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (case.root / 'source-bundles' / case.bundle_hash / 'files' / 'revised.md').write_text('Changed')
        return result
    monkeypatch.setattr(case.sources, 'verify', changed)
    with pytest.raises(SourceBundleError, match='完整性'):
        build_rule_review(case.package, case.bundle_hash, case.sources)


def test_rule_review_marks_excerpt_and_byte_limits(case, monkeypatch):
    monkeypatch.setattr(rule_review, 'MAX_EXCERPT_CHARS', 10)
    result = build_rule_review(case.package, case.bundle_hash, case.sources)
    assert all(source['truncated'] and len(source['text']) == 10
               for row in result['rows'] for source in row['sources'])
    monkeypatch.setattr(rule_review, 'MAX_SOURCE_BYTES', 1)
    result = build_rule_review(case.package, case.bundle_hash, case.sources)
    assert all(source['status'] == 'TEXT_LIMIT' and source['text'] is None
               for row in result['rows'] for source in row['sources'])


def test_rule_review_http_auth_binding_and_no_writes(case, review_db):
    db, _ = review_db
    candidate = PackageImportService(db).submit(case.package, submitted_by=1, idempotency_key='review-only')
    db.commit()
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return {'admin': SimpleNamespace(id=1, is_active=True, is_admin=True),
                    'player': SimpleNamespace(id=2, is_active=True, is_admin=False)}.get(token)
    app = FastAPI(); app.include_router(script_review_routes.router); app.add_middleware(Auth)
    service = ScriptReviewService(db)
    app.dependency_overrides[script_review_routes.review_service] = lambda: service
    app.dependency_overrides[script_review_routes.source_store] = lambda: case.sources
    url = f'/api/admin/fusion/script-packages/{candidate["version_id"]}/rule-review'
    params = {'bundle_hash': case.bundle_hash, 'expected_package_hash': content_hash(case.package)}
    before = fingerprints(case.root)
    previous = service.get_review(candidate['version_id'])
    with TestClient(app) as client:
        assert client.get(url, params=params).status_code == 401
        assert client.get(url, params=params, headers={'Authorization': 'Bearer player'}).status_code == 403
        headers = {'Authorization': 'Bearer admin'}
        response = client.get(url, params=params, headers=headers)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert response.json()['data']['schema_version'] == 'rule-review/1.1'
        assert client.get(url, params=params | {'expected_package_hash': 'a' * 64}, headers=headers).status_code == 409
        assert client.get(url, params=params | {'limit': 51}, headers=headers).status_code == 422
        assert client.get(url.replace(str(candidate['version_id']), '9999'), params=params, headers=headers).status_code == 404
    assert fingerprints(case.root) == before and service.get_review(candidate['version_id']) == previous


def test_rule_review_exposes_actual_settlement_and_runtime_scope_without_mutation(case):
    document=deepcopy(case.package); before=fingerprints(case.root)
    result=build_rule_review(document,case.bundle_hash,case.sources,limit=50)
    assert result['schema_version']=='rule-review/1.1'
    assert result['runtime_facts']=={'package_contract':'script-package/1.2','human_players':1,
        'investigation_actor':'SELECTED_HUMAN_ONLY','action_success_limit':'ONCE_PER_SESSION',
        'phase_budget':'SHARED_NO_CARRY','material_recipient':'DECLARED_OWNER'}
    row=next(row for row in result['rows'] if row['target']['collection']=='settlement')
    assert row['target']['id'] is None and row['rules']=={key:document['settlement'][key] for key in ('phase_id','truth_ids')}
    assert row['rules']['truth_ids'] and row['sources'][0]['status']=='AVAILABLE'
    assert not result['publication_ready'] and result['semantic_status']=='UNREVIEWED'
    assert document==case.package and before==fingerprints(case.root)
