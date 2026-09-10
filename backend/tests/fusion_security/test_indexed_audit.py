"""Selected target-local references only; no invented or repaired citations."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.fusion.audit_references import audit_source_catalog
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, parse_indexed_audit
from src.schemas.authoring import AuthoringRequestV15, parse_authoring_request
from src.schemas.indexed_audit import IndexedAuditDraft
from tests.fusion_security.test_authoring_model import fixture_config
from tests.fusion_security.test_authoring_v12 import synthetic_v12_context_and_package, report_v12_for


def indexed_report(package):
    report = report_v12_for(package)
    report["schema_version"] = "indexed-audit-draft/1.0"
    for finding in report["findings"]:
        del finding["sources"]
        finding["source_indexes"] = [0]
    return report


def test_indexed_reference_diagnostics_are_bounded_positions_without_values():
    from src.fusion.authoring_model import parse_indexed_audit, AuthoringModelError
    _, package = synthetic_v12_context_and_package()
    report = indexed_report(package)
    finding = deepcopy(report['findings'][0])
    report['findings'] = [deepcopy(finding) | {'id':f'f-{i}', 'target':{'collection':'evidence','id':'private-missing-target'}} for i in range(12)]
    with pytest.raises(AuthoringModelError) as error:
        parse_indexed_audit(report, package)
    assert error.value.details == [{'code':'AUDIT_TARGET_NOT_FOUND','entity_path':f'/findings/{i}/target'} for i in range(10)]
    assert 'private-missing-target' not in str(error.value.details)
    report['findings'] = [finding | {'source_indexes':[98,99]}]
    with pytest.raises(AuthoringModelError) as error:
        parse_indexed_audit(report, package)
    assert error.value.details == [{'code':'AUDIT_SOURCE_INDEX_UNAVAILABLE','entity_path':f'/findings/0/source_indexes/{i}'} for i in range(2)]


@pytest.mark.parametrize('path', ['/findings/200/source_indexes/0', '/findings/0/source_indexes/100',
                                  '/findings/00/source_indexes/0', '/findings/0/sources/0',
                                  '/findings/0/source_indexes/private-value'])
def test_source_index_diagnostics_reject_unbounded_or_private_paths(path):
    from src.fusion.authoring_model import validate_output_diagnostics
    with pytest.raises(ValueError, match='AUTHORING_DIAGNOSTICS_INVALID'):
        validate_output_diagnostics([{'code':'AUDIT_SOURCE_INDEX_UNAVAILABLE','entity_path':path}])


def test_indexed_audit_resolves_only_selected_reference_without_mutation():
    _, package = synthetic_v12_context_and_package()
    refs = package["mechanics"]["actions"][0]["sources"]
    refs.append({"source_id": refs[0]["source_id"], "anchor": "L1-L2"})
    draft = indexed_report(package)
    draft["findings"][0]["source_indexes"] = [1]
    before = deepcopy((package, draft))
    result = parse_indexed_audit(draft, package)
    assert result["schema_version"] == "script-audit/1.1"
    assert result["findings"][0]["sources"] == [refs[1] | {"page": None}]
    assert "source_indexes" not in result["findings"][0]
    assert (package, draft) == before


@pytest.mark.parametrize("indexes", [[], [-1], [100], [1], [0, 0], [True], [0.0], ["0"], None, "0"])
def test_indexed_audit_refuses_invalid_or_out_of_target_indices(indexes):
    _, package = synthetic_v12_context_and_package()
    draft = indexed_report(package)
    draft["findings"][0]["source_indexes"] = indexes
    with pytest.raises(AuthoringModelError, match="AUDIT_INDEXED_OUTPUT_INVALID"):
        parse_indexed_audit(draft, package)


@pytest.mark.parametrize("mutation", ["raw-source", "target", "singleton", "coverage", "duplicate-id", "version", "approval"])
def test_indexed_audit_rejects_schema_target_or_report_overreach(mutation):
    _, package = synthetic_v12_context_and_package()
    draft = indexed_report(package)
    if mutation == "raw-source": draft["findings"][0]["sources"] = package["introduction"]["sources"]
    elif mutation == "target": draft["findings"][0]["target"]["id"] = "unknown"
    elif mutation == "singleton": draft["findings"][0]["target"] = {"collection": "introduction", "id": "not-null"}
    elif mutation == "coverage": draft["coverage"].pop()
    elif mutation == "duplicate-id": draft["findings"][1]["id"] = draft["findings"][0]["id"]
    elif mutation == "version": draft["schema_version"] = "script-audit/1.1"
    else: draft["approved"] = True
    with pytest.raises(AuthoringModelError, match="AUDIT_INDEXED_OUTPUT_INVALID"):
        parse_indexed_audit(draft, package)


def test_indexed_audit_catalog_has_all_target_types_and_local_zero_index():
    _, package = synthetic_v12_context_and_package()
    catalog = audit_source_catalog(package)
    assert {item["target"]["collection"] for item in catalog} == {
        "introduction", "settlement", "characters", "phases", "knowledge", "evidence", "truth",
        "mechanics.actions", "mechanics.phase_budgets"}
    assert all(item["sources"][0]["index"] == 0 for item in catalog)
    for collection, identifier in [("introduction", None), ("settlement", None),
                                    ("mechanics.phase_budgets", package["phases"][0]["id"])]:
        draft = indexed_report(package)
        draft["findings"][0]["target"] = {"collection": collection, "id": identifier}
        assert parse_indexed_audit(draft, package)["findings"][0]["sources"]


def test_indexed_audit_empty_findings_preserves_complete_coverage():
    _, package = synthetic_v12_context_and_package()
    draft = indexed_report(package)
    draft["findings"] = []
    result = parse_indexed_audit(draft, package)
    assert result["findings"] == [] and len(result["coverage"]) == 5


def test_indexed_audit_preparation_version_catalog_and_old_mode_isolation():
    context, package = synthetic_v12_context_and_package()
    context.update(rule_plan=deepcopy(package), compiler_mode="CONFIRM_FROZEN_TEXT", audit_mode="TARGET_SOURCE_INDEXES")
    model = AuthoringModel(fixture_config(), "script-package/1.2", rule_plan_enabled=True,
                           confirm_frozen_text=True, indexed_audit=True)
    prepared = model.prepare("AUDIT", context, package)
    payload = json.loads(prepared.messages[1].content)
    assert payload["audit_source_catalog"] == audit_source_catalog(package)
    assert prepared.request_contract["version"] == "bailian-authoring-json/1.5"
    assert prepared.input_tokens == sum(len(m.content.encode()) for m in prepared.messages) + 512
    old = AuthoringModel(fixture_config(), "script-package/1.2", rule_plan_enabled=True, confirm_frozen_text=True)
    with pytest.raises(AuthoringModelError, match="AUTHORING_CONTRACT_MISMATCH"): old.prepare("AUDIT", context, package)
    with pytest.raises(AuthoringModelError): AuthoringModel(fixture_config(), indexed_audit=True)
    body = {key: value for key, value in context.items() if key in AuthoringRequestV15.model_fields}
    body["idempotency_key"] = "indexed-review"
    assert parse_authoring_request(body).model_dump() == body


def test_indexed_audit_static_schemas_and_v14_prompt_are_preserved():
    root = Path(__file__).resolve().parents[3] / "docs/contracts"
    for name, model in [("authoring-request.v1.5.schema.json", AuthoringRequestV15),
                        ("indexed-audit-draft.v1.0.schema.json", IndexedAuditDraft)]:
        assert json.loads((root / name).read_text()) == model.model_json_schema()
    old = AuthoringModel(fixture_config(), "script-package/1.2", rule_plan_enabled=True, confirm_frozen_text=True)
    assert old.snapshot()["prompt_hashes"]["AUDIT"] == "3d67373a259c3fd0a6bd38c10394134cbd2a3fb03f2590b3ce7fac1e4c17519b"
