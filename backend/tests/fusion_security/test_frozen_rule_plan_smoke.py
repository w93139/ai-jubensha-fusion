"""New smoke dispatch/budget tests with synthetic data and no real network."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.fusion import authoring_model, frozen_rule_plan_smoke as smoke
from src.fusion.authoring_jobs import AuthoringJobStore
from src.fusion.authoring_rule_plan import text_entities
from src.fusion.package_validation import content_hash
from tests.fusion_security.test_authoring_model import fixture_config, good_response
from tests.fusion_security.test_authoring_v12 import report_v12_for


def sdk_fixture(monkeypatch, mutation=None, unknown_step=None, audit_blocker=False, audit_mutation=None,
                audit_finish="stop"):
    holder = {}
    original = smoke.make_fixture

    def fixture(root, **options):
        result = original(root, **options)
        holder.update(root=root, context=result[2])
        return result

    async def response(messages, **kwargs):
        payload = json.loads(messages[1].content)
        step = payload["task"]
        engine = create_engine("sqlite:///" + str(holder["root"] / "synthetic-authoring.sqlite3"))
        try:
            job = AuthoringJobStore(sessionmaker(engine)).list()[0]
            assert [item["status"] for item in job["attempts"] if item["step"] == step] == ["IN_FLIGHT"]
            expected_version = "authoring-model/1.12" if holder["context"].get("audit_mode") == "CITATION_CATALOG" else "authoring-model/1.11" if holder["context"].get("audit_mode") == "RUNTIME_CONTEXT_SOURCE_INDEXES" else "authoring-model/1.10" if holder["context"].get("audit_mode") == "TYPED_STRICT_SOURCE_INDEXES" else "authoring-model/1.9" if holder["context"].get("audit_mode") == "PORTABLE_STRICT_SOURCE_INDEXES" else "authoring-model/1.8" if holder["context"].get("audit_mode") == "STRICT_BOUNDED_SOURCE_INDEXES" else "authoring-model/1.7" if holder["context"].get("audit_mode") == "DIRECT_BOUNDED_SOURCE_INDEXES" else "authoring-model/1.6" if holder["context"].get("audit_mode") == "BOUNDED_TARGET_SOURCE_INDEXES" else "authoring-model/1.5" if "audit_mode" in holder["context"] else "authoring-model/1.4" if "compiler_mode" in holder["context"] else "authoring-model/1.3"
            assert job["model_snapshot"]["schema_version"] == expected_version
        finally:
            engine.dispose()
        assert kwargs["temperature"] == 0
        if step == "AUDIT" and holder["context"].get("audit_mode") in {"STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}:
            assert kwargs["response_format"]["type"] == "json_schema"
            assert kwargs["response_format"]["json_schema"]["strict"] is True
        else:
            assert kwargs["response_format"] == {"type":"json_object"}
        plan = holder["context"]["rule_plan"]
        assert json.loads((holder["root"] / "rule-plan-input.json").read_text()) == plan
        if step == unknown_step:
            raise TimeoutError("PRIVATE_PROVIDER_ERROR")
        if step == "COMPILE":
            assert "rule_plan" not in payload["input"]
            draft = {"schema_version": "compiler-text-draft/1.0", "status": "CANDIDATE", "blockers": [],
                     "slots": [{"collection": collection, "id": identifier, "text": entity["text"]}
                               for (collection, identifier), entity in text_entities(plan)]}
            if "compiler_mode" in holder["context"]:
                draft["schema_version"] = "compiler-text-confirmation/1.0"
                for slot in draft["slots"]:
                    del slot["text"]
                assert all("text" in slot for slot in payload["text_slots"])
            if mutation:
                mutation(draft)
            return good_response(draft)
        assert payload["rule_plan_hash"] == content_hash(plan)
        report = report_v12_for(payload["candidate"])
        if audit_blocker:
            report["findings"][0]["severity"] = "BLOCKER"
        if holder["context"].get("audit_mode") == "CITATION_CATALOG":
            assert "audit_source_catalog" not in payload
            assert kwargs["response_format"]["json_schema"]["name"] == "citation_audit"
            report.update(schema_version="citation-audit-draft/1.0", status="COMPLETE")
            for finding in report["findings"]:
                finding["citation_indexes"] = [next(item["index"] for item in payload["citation_catalog"]
                    if item["target"] == finding["target"])]
                for key in ("id", "target", "sources"):
                    del finding[key]
        elif "audit_mode" in holder["context"]:
            assert payload["audit_source_catalog"]
            report["schema_version"] = "indexed-audit-draft/1.0"
            for finding in report["findings"]:
                del finding["sources"]
                finding["source_indexes"] = [0]
        if holder["context"].get("audit_mode") in {"BOUNDED_TARGET_SOURCE_INDEXES", "DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}:
            report.update(schema_version="citation-audit-draft/1.0" if holder["context"].get("audit_mode") == "CITATION_CATALOG" else "bounded-audit-draft/1.0", status="COMPLETE")
        if audit_mutation:
            audit_mutation(report)
        result = good_response(report)
        result.finish_reason = audit_finish
        return result

    monkeypatch.setattr(smoke, "make_fixture", fixture)
    sdk = Mock(_client=None)
    sdk.chat_completion = AsyncMock(side_effect=response)
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(authoring_model, "OpenAILLMService", factory)
    return factory, sdk


def test_rule_plan_smoke_preview_never_dispatches(tmp_path, monkeypatch):
    factory, sdk = sdk_fixture(monkeypatch)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private"))
    assert code == 0 and receipt["status"] == "PREVIEW"
    assert receipt["model_requests"] == 0 and receipt["attempts"] == []
    assert receipt["rule_plan_hash"] and receipt["rule_plan_is_input_not_approval"]
    assert receipt["model"]["schema_version"] == "authoring-model/1.3"
    factory.assert_not_called()


def test_rule_plan_smoke_complete_keeps_explicit_plan_and_original_quality_gate(tmp_path, monkeypatch):
    _, sdk = sdk_fixture(monkeypatch)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True, paid_authorized=True))
    assert code == 0 and receipt["status"] == "PASSED", receipt
    assert receipt["calls_started"] == ["COMPILE", "AUDIT"] and sdk.chat_completion.await_count == 2
    assert receipt["quality"]["both_human_roles_completed"] and receipt["quality"]["exact_fixture_mapping"]
    assert receipt["quality"]["audit_blockers"] == 0
    assert receipt["publication_ready"] is False and receipt["runtime_ready"] is False
    root = Path(receipt["artifact_directory"])
    assert json.loads((root / "candidate.json").read_text()) == json.loads((root / "rule-plan-input.json").read_text())
    assert Decimal(receipt["charged_cost_cny"]) <= Decimal(receipt["total_reserved_cny"]) <= Decimal("0.10")
    assert all(item["receipt"]["response_fingerprint"]["content_sha256"] for item in receipt["attempts"])


@pytest.mark.parametrize("mutation", [
    lambda draft: draft["slots"][0].update(required_public_evidence_ids=["key"]),
    lambda draft: draft["slots"].pop(),
    lambda draft: draft["slots"].append(deepcopy(draft["slots"][0])),
    lambda draft: draft["slots"][0].update(text="Not from the frozen source"),
])
def test_rule_plan_smoke_invalid_output_never_repaired_or_retried(tmp_path, monkeypatch, mutation):
    _, sdk = sdk_fixture(monkeypatch, mutation=mutation)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True, paid_authorized=True))
    assert code == 3 and receipt["status"] == "FAILED"
    assert receipt["model_requests"] == 1 and sdk.chat_completion.await_count == 1
    assert receipt["candidate_version_id"] is None and receipt["usage_known"] is True


@pytest.mark.parametrize("step", ["COMPILE", "AUDIT"])
def test_rule_plan_smoke_unknown_call_keeps_reservation_and_stops(tmp_path, monkeypatch, step):
    _, sdk = sdk_fixture(monkeypatch, unknown_step=step)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True, paid_authorized=True))
    assert code == 3 and receipt["job_state"] == "NEEDS_RECONCILIATION"
    assert receipt["usage_known"] is False and Decimal(receipt["charged_cost_cny"]) > 0
    assert sdk.chat_completion.await_count == (1 if step == "COMPILE" else 2)
    assert "PRIVATE_PROVIDER_ERROR" not in json.dumps(receipt)


def test_rule_plan_smoke_missing_paid_authorization_refuses_call(tmp_path, monkeypatch):
    factory, _ = sdk_fixture(monkeypatch)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True))
    assert code == 3 and receipt["result_code"] == "SMOKE_PAID_CALLS_DISABLED"
    factory.assert_not_called()


def test_rule_plan_smoke_audit_blocker_cannot_be_called_passed(tmp_path, monkeypatch):
    _, sdk = sdk_fixture(monkeypatch, audit_blocker=True)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True, paid_authorized=True))
    assert code == 3 and receipt["result_code"] == "SMOKE_AUDIT_BLOCKERS_FOUND"
    assert receipt["job_state"] == "COMPLETED" and receipt["quality"]["audit_blockers"] == 1
    assert receipt["publication_ready"] is False and sdk.chat_completion.await_count == 2


def test_confirmation_smoke_complete_preserves_all_text(tmp_path, monkeypatch):
    _, sdk = sdk_fixture(monkeypatch)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True,
                                                paid_authorized=True, confirm_frozen_text=True))
    assert code == 0 and receipt["model"]["schema_version"] == "authoring-model/1.4", receipt
    assert receipt["quality"]["exact_fixture_mapping"] and sdk.chat_completion.await_count == 2
    root = Path(receipt["artifact_directory"])
    assert json.loads((root / "candidate.json").read_text()) == json.loads((root / "rule-plan-input.json").read_text())


@pytest.mark.parametrize("mutation", [lambda d: d["slots"][0].update(text="Injected management explanation"),
                                    lambda d: d["slots"].pop(), lambda d: d["slots"].append(d["slots"][0])])
def test_confirmation_smoke_refuses_text_or_missing_confirmations(tmp_path, monkeypatch, mutation):
    _, sdk = sdk_fixture(monkeypatch, mutation=mutation)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True,
                                                paid_authorized=True, confirm_frozen_text=True))
    assert code == 3 and receipt["result_code"] == "COMPILER_TEXT_DRAFT_INVALID"
    assert receipt["candidate_version_id"] is None and sdk.chat_completion.await_count == 1


@pytest.mark.parametrize("audit_blocker", [False, True])
def test_indexed_smoke_durable_workflow_still_has_no_approval(tmp_path, monkeypatch, audit_blocker):
    _, sdk = sdk_fixture(monkeypatch, audit_blocker=audit_blocker)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True,
        paid_authorized=True, confirm_frozen_text=True, indexed_audit=True))
    assert code == (3 if audit_blocker else 0), receipt
    assert receipt["model"]["schema_version"] == "authoring-model/1.5"
    assert receipt["job_state"] == "COMPLETED" and sdk.chat_completion.await_count == 2
    assert receipt["quality"]["audit_blockers"] == int(audit_blocker)
    assert receipt["publication_ready"] is False and receipt["job_reload_verified"]
    root = Path(receipt["artifact_directory"])
    report = json.loads((root / "audit.json").read_text())
    assert report["schema_version"] == "script-audit/1.1"
    assert report["findings"][0]["sources"] and "source_indexes" not in report["findings"][0]


@pytest.mark.parametrize("failure", ["index", "timeout", "length"])
def test_indexed_smoke_failed_audit_keeps_candidate_and_cost_without_resending(tmp_path, monkeypatch, failure):
    unknown = failure == "timeout"
    _, sdk = sdk_fixture(monkeypatch, unknown_step="AUDIT" if unknown else None,
                         audit_finish="length" if failure == "length" else "stop",
                         audit_mutation=lambda r: r["findings"][0].update(source_indexes=[99]))
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path / "private", execute=True,
        paid_authorized=True, confirm_frozen_text=True, indexed_audit=True))
    assert code == 3 and receipt["candidate_version_id"] is not None
    assert receipt["job_state"] == ("NEEDS_RECONCILIATION" if unknown else "BLOCKED")
    assert sdk.chat_completion.await_count == 2 and Decimal(receipt["charged_cost_cny"]) > 0
    assert receipt["usage_known"] is not unknown
    assert receipt["attempts"][1]["receipt"]["schema_version"] == "authoring-model/1.5"
    assert receipt["result_code"] == {"index": "AUDIT_INDEXED_OUTPUT_INVALID",
        "timeout": "AUTHORING_TIMEOUT_USAGE_UNKNOWN", "length": "AUTHORING_FINISH_INVALID"}[failure]


@pytest.mark.parametrize('failure', ['none', 'incomplete', 'length', 'unknown-finish', 'timeout', 'format'])
@pytest.mark.parametrize('direct', [False, True, 'strict', 'portable', 'typed', 'runtime', 'citation'])
def test_bounded_smoke_complete_or_stopped_without_retry(tmp_path, monkeypatch, failure, direct):
    _, sdk = sdk_fixture(monkeypatch, unknown_step='AUDIT' if failure == 'timeout' else None,
        audit_finish={'length':'length','unknown-finish':'PRIVATE_PROVIDER_REASON'}.get(failure,'stop'),
        audit_mutation=(lambda r:r.update(status='INCOMPLETE')) if failure == 'incomplete' else (lambda r:r.pop('status')) if failure == 'format' else None)
    code, receipt = asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path/'private',
        execute=True, paid_authorized=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=bool(direct), strict_audit_schema=direct in {"strict", "portable", "typed", "runtime", "citation"}, portable_audit_patterns=direct in {"portable", "typed", "runtime", "citation"}, typed_audit_schema=direct in {"typed", "runtime", "citation"}, runtime_audit_context=direct in {"runtime", "citation"}, citation_audit=direct == "citation"))
    assert code == (0 if failure == 'none' else 3), receipt
    assert sdk.chat_completion.await_count == 2 and receipt['candidate_version_id'] is not None
    assert receipt['model']['schema_version'] == ('authoring-model/1.12' if direct == 'citation' else 'authoring-model/1.11' if direct == 'runtime' else 'authoring-model/1.10' if direct == 'typed' else 'authoring-model/1.9' if direct == 'portable' else 'authoring-model/1.8' if direct == 'strict' else 'authoring-model/1.7' if direct else 'authoring-model/1.6')
    assert receipt['publication_ready'] is False and receipt['job_reload_verified']
    assert receipt['attempts'][1]['receipt']['response_finish'] == {
        'none':'stop', 'incomplete':'stop', 'format':'stop', 'length':'length', 'unknown-finish':'OTHER', 'timeout':None}[failure]
    assert 'PRIVATE_PROVIDER_REASON' not in json.dumps(receipt)
    if failure == 'format':
        assert receipt['attempts'][1]['receipt']['output_diagnostics'] == ([{'code':'AUDIT_CITATION_SCHEMA_INVALID','entity_path':'/'}] if direct == 'citation' else [{'code':'AUDIT_BOUNDED_SCHEMA_INVALID','entity_path':'/status'}])
    if failure != 'none':
        assert receipt['attempts'][1]['output_hash'] is None
        assert receipt['job_state'] == ('NEEDS_RECONCILIATION' if failure == 'timeout' else 'BLOCKED')
        assert receipt['usage_known'] is (failure != 'timeout')
    else:
        assert receipt['quality']['exact_fixture_mapping'] and receipt['quality']['audit_blockers'] == 0
