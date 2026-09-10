"""Fixed synthetic authoring smoke with the real adapter and a local fake SDK."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.fusion import authoring_model, investigation_authoring_smoke as smoke
from src.fusion.authoring_jobs import AuthoringJobError, AuthoringJobStore
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError
from src.fusion.authoring_runner import AuthoringRunner
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.source_bundles import SourceBundleStore
from tests.fusion_security.test_authoring_model import fixture_config, good_response
from tests.fusion_security.test_authoring_v12 import draft_v12_for, report_v12_for


def expected_package(context, prefix=""):
    """A stub SDK answer, never a fallback in the production smoke harness."""
    identifier = lambda value: prefix + value
    source_id = context["sources"][0]["id"]
    refs = lambda heading: [{"source_id": source_id, "anchor": heading}]
    a, b, start, end, find, unlock, key, card = map(identifier, ("a", "b", "start", "end", "find", "unlock", "key", "card"))
    package = {"schema_version": "script-package/1.2", **deepcopy({name: context[name] for name in
        ("script_key", "content_version", "title", "player_count", "sources")})}
    package.update({"introduction": {"text": smoke.INTRODUCTION, "sources": refs("开场介绍")},
        "characters": [{"id": a, "name": smoke.NAMES[0], "sources": refs("角色甲")},
                       {"id": b, "name": smoke.NAMES[1], "sources": refs("角色乙")}],
        "initial_phase_id": start, "phases": [
            {"id": start, "title": smoke.PHASE_NAMES[0], "next_phase_id": end, "sources": refs("入场阶段")},
            {"id": end, "title": smoke.PHASE_NAMES[1], "next_phase_id": None, "sources": refs("复盘阶段")}],
        "knowledge": [{"id": identifier("fact-" + local), "text": smoke.PRIVATE_FACTS[name], "kind": "FACT",
            "visibility": "CHARACTER_PRIVATE", "character_id": character, "disclosure": "KEEP_PRIVATE",
            "release": {"phase_id": start}, "sources": refs(heading)}
            for local, character, name, heading in (("a", a, smoke.NAMES[0], "角色甲"), ("b", b, smoke.NAMES[1], "角色乙"))],
        "evidence": [
            {"id": key, "text": smoke.KEY_TEXT, "visibility": "PUBLIC", "character_id": None, "disclosure": "PUBLIC",
             "release": {"phase_id": start, "required_action_ids": [find]}, "sources": refs("公开钥匙证据")},
            {"id": card, "text": smoke.CARD_TEXT, "visibility": "CHARACTER_PRIVATE", "character_id": b, "disclosure": "MAY_SHARE",
             "release": {"phase_id": start, "required_action_ids": [unlock]}, "sources": refs("乙的柜内证据")}],
        "truth": [{"id": identifier("truth"), "text": smoke.TRUTH_TEXT, "visibility": "SYSTEM_TRUTH", "sources": refs("系统真相")}],
        "settlement": {"phase_id": end, "truth_ids": [identifier("truth")],
            "instructions": {"text": smoke.SETTLEMENT, "sources": refs("结算说明")}},
        "mechanics": {"phase_budgets": [
            {"phase_id": start, "points": 3, "advance_policy": "REQUIRE_EXHAUSTED", "origin": "SOURCE_EXPLICIT", "sources": refs("入场阶段")},
            {"phase_id": end, "points": 0, "advance_policy": "ALLOW_REMAINING", "origin": "SOURCE_EXPLICIT", "sources": refs("复盘阶段")}],
            "actions": [
                {"id": find, "label": smoke.ACTION_LABELS[0], "cost": 1, "phase_ids": [start], "allowed_character_ids": [a, b],
                 "origin": "SOURCE_EXPLICIT", "sources": refs("调查动作一")},
                {"id": unlock, "label": smoke.ACTION_LABELS[1], "cost": 2, "phase_ids": [start], "allowed_character_ids": [a, b],
                 "required_action_ids": [find], "required_public_evidence_ids": [key],
                 "origin": "SOURCE_EXPLICIT", "sources": refs("调查动作二")}]}})
    return package


def fake_sdk(monkeypatch, *, mutate=None, audit_mutate=None, unknown_step=None, prefix=""):
    holder, calls = {}, []
    original = smoke.make_fixture

    def fixture(root):
        result = original(root)
        holder.update(context=result[2], root=root)
        return result

    async def response(messages, **kwargs):
        payload = json.loads(messages[1].content)
        step = payload["task"]
        calls.append(step)
        assert kwargs["extra_body"] == {"enable_thinking": False, "preserve_thinking": False}
        assert kwargs["response_format"] == {"type": "json_object"} and kwargs["temperature"] == 0
        # Inspect the committed DB from another connection before any fake SDK response.
        engine = create_engine("sqlite:///" + str(holder["root"] / "synthetic-authoring.sqlite3"))
        try:
            job = AuthoringJobStore(sessionmaker(engine)).list()[0]
            assert [item["status"] for item in job["attempts"] if item["step"] == step] == ["IN_FLIGHT"]
        finally:
            engine.dispose()
        if unknown_step == step:
            raise RuntimeError("SENSITIVE_PROVIDER_BODY_MUST_NOT_ESCAPE")
        if step == "COMPILE":
            package = expected_package(holder["context"], prefix)
            if mutate:
                mutate(package)
            return good_response(draft_v12_for(package))
        report = report_v12_for(payload["candidate"])
        if audit_mutate:
            audit_mutate(report)
        return good_response(report)

    client = Mock(_client=None)
    client.chat_completion = AsyncMock(side_effect=response)
    factory = Mock(return_value=client)
    monkeypatch.setattr(smoke, "make_fixture", fixture)
    monkeypatch.setattr(authoring_model, "OpenAILLMService", factory)
    return calls, factory, holder


def run(tmp_path, **kwargs):
    return asyncio.run(smoke.run_smoke(fixture_config(), output_root=tmp_path, **kwargs))


def test_package_investigation_smoke_preview_is_zero_sdk_and_new_job_only(tmp_path, monkeypatch):
    calls, factory, _ = fake_sdk(monkeypatch)
    code, receipt = run(tmp_path)
    assert code == 0 and receipt["status"] == "PREVIEW" and receipt["job_state"] == "QUEUED"
    assert not calls and receipt["attempts"] == [] and receipt["model_requests"] == 0
    factory.assert_not_called()
    assert receipt["model"]["schema_version"] == "authoring-model/1.2"
    assert receipt["model"]["request_contract"] == "bailian-authoring-json/1.2"
    assert receipt["compile_input_token_bound"] <= 32768
    assert Decimal(receipt["compile_plus_audit_ceiling_cny"]) <= Decimal("0.10")
    assert receipt["audit_maximum_allowed_reservation_cny"] == "0.05"
    assert receipt["audit_reservation_not_yet_known"] and not receipt["publication_ready"]
    root = Path(receipt["artifact_directory"])
    assert not (root / "candidate.json").exists() and not (root / "audit.json").exists()
    assert json.loads((root / "receipt.json").read_text()) == receipt


def test_package_investigation_smoke_two_exact_calls_persist_sources_candidate_audit_and_cost(tmp_path, monkeypatch):
    calls, factory, holder = fake_sdk(monkeypatch, prefix="generated-")
    code, receipt = run(tmp_path, execute=True, paid_authorized=True)
    assert code == 0 and receipt["status"] == "PASSED", receipt
    assert calls == receipt["calls_started"] == ["COMPILE", "AUDIT"] and factory.call_count == 2
    assert all(item.kwargs["client_max_retries"] == 0 for item in factory.call_args_list)
    assert receipt["job_state"] == "COMPLETED" and receipt["job_reload_verified"]
    assert receipt["usage_known"] and Decimal(receipt["charged_cost_cny"]) == Decimal("0.00012")
    assert Decimal(receipt["total_reserved_cny"]) <= Decimal("0.10")
    assert all(Decimal(item["reservation"]["cost_cny"]) <= Decimal("0.05") for item in receipt["attempts"])
    assert receipt["quality"]["both_human_roles_completed"] and receipt["quality"]["audit_blockers"] == 0
    root = holder["root"]
    assert json.loads((root / "receipt.json").read_text()) == receipt
    for path in root.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(json.loads((root / f"dispatch-{step.lower()}.json").read_text())["reservation_committed"] for step in calls)
    assert all(text not in canonical_json(receipt) for text in
               (*smoke.PRIVATE_FACTS.values(), smoke.CARD_TEXT, smoke.TRUTH_TEXT, "SYNTHETIC_KEY_SENTINEL"))
    assert content_hash(json.loads((root / "candidate.json").read_text())) == receipt["quality"]["package_hash"]


@pytest.mark.parametrize("field", ["budget", "cost", "actor", "prerequisite", "permission", "extra_card", "intro"])
def test_package_investigation_smoke_semantic_mismatch_blocks_audit_without_repair_or_second_call(tmp_path, monkeypatch, field):
    def mutate(package):
        if field == "budget": package["mechanics"]["phase_budgets"][0]["points"] = 4
        elif field == "cost": package["mechanics"]["actions"][0]["cost"] = 0
        elif field == "actor": package["mechanics"]["actions"][1]["allowed_character_ids"] = ["a"]
        elif field == "prerequisite": package["mechanics"]["actions"][1]["required_action_ids"] = []
        elif field == "permission": package["knowledge"][0]["disclosure"] = "MAY_SHARE"
        elif field == "intro": package["introduction"]["text"] = smoke.CARD_TEXT
        else:
            item = deepcopy(package["evidence"][1])
            item.update(id="extra-card", visibility="PUBLIC", character_id=None, disclosure="PUBLIC")
            item["release"] = {"phase_id": package["initial_phase_id"]}
            package["evidence"].append(item)
    calls, _, _ = fake_sdk(monkeypatch, mutate=mutate)
    code, receipt = run(tmp_path, execute=True, paid_authorized=True)
    assert code == 3 and receipt["status"] == "FAILED" and calls == ["COMPILE"]
    assert len(receipt["attempts"]) == 1 and receipt["usage_known"]
    assert Decimal(receipt["charged_cost_cny"]) == Decimal("0.00006")
    assert receipt["job_state"] == "BLOCKED"
    assert receipt["result_code"] != "SMOKE_WORKFLOW_INCOMPLETE"
    assert not (Path(receipt["artifact_directory"]) / "dispatch-audit.json").exists()


@pytest.mark.parametrize("step", ["COMPILE", "AUDIT"])
def test_package_investigation_smoke_unknown_is_conservatively_accounted_and_never_resent(tmp_path, monkeypatch, step):
    calls, _, holder = fake_sdk(monkeypatch, unknown_step=step)
    code, receipt = run(tmp_path, execute=True, paid_authorized=True)
    assert code == 3 and receipt["job_state"] == "NEEDS_RECONCILIATION"
    assert calls == (["COMPILE"] if step == "COMPILE" else ["COMPILE", "AUDIT"])
    assert not receipt["usage_known"] and receipt["attempts"][-1]["status"] == "UNKNOWN"
    expected = Decimal(receipt["attempts"][-1]["reservation"]["cost_cny"])
    if step == "AUDIT": expected += Decimal("0.00006")
    assert Decimal(receipt["charged_cost_cny"]) == expected
    assert "SENSITIVE_PROVIDER_BODY_MUST_NOT_ESCAPE" not in canonical_json(receipt)
    before = list(calls)
    engine = create_engine("sqlite:///" + str(holder["root"] / "synthetic-authoring.sqlite3"))
    try:
        jobs = AuthoringJobStore(sessionmaker(engine))
        runner = AuthoringRunner(jobs, SourceBundleStore(holder["root"] / "source-bundles"),
                                 AuthoringModel(fixture_config(), package_contract="script-package/1.2"))
        with pytest.raises(AuthoringJobError):
            asyncio.run(runner.run(receipt["job_id"], allow_paid=True))
    finally:
        engine.dispose()
    assert calls == before


def test_package_investigation_smoke_audit_blocker_does_not_pass_and_retains_known_cost(tmp_path, monkeypatch):
    calls, _, _ = fake_sdk(monkeypatch, audit_mutate=lambda audit: audit["findings"][0].update(severity="BLOCKER"))
    code, receipt = run(tmp_path, execute=True, paid_authorized=True)
    assert code == 3 and receipt["result_code"] == "SMOKE_AUDIT_BLOCKERS_FOUND" and len(calls) == 2
    assert receipt["job_state"] == "COMPLETED" and receipt["quality"]["audit_blockers"] == 1
    assert Decimal(receipt["charged_cost_cny"]) == Decimal("0.00012")


def test_package_investigation_smoke_invalid_audit_receipt_is_safe_and_known(tmp_path, monkeypatch):
    def mutate(audit):
        audit["findings"][0]["target"]["id"] = "MISSING_PAYLOAD_SENTINEL"
    calls, _, _ = fake_sdk(monkeypatch, audit_mutate=mutate)
    code, receipt = run(tmp_path, execute=True, paid_authorized=True)
    assert code == 3 and calls == ["COMPILE", "AUDIT"]
    assert receipt["usage_known"] and Decimal(receipt["charged_cost_cny"]) == Decimal("0.00012")
    assert receipt["attempts"][-1]["status"] == "FAILED"
    assert "MISSING_PAYLOAD_SENTINEL" not in canonical_json(receipt)


@pytest.mark.parametrize("failure", ["artifact", "ledger"])
def test_package_investigation_smoke_local_failure_never_relabels_paid_calls_zero(tmp_path, monkeypatch, failure):
    calls, _, _ = fake_sdk(monkeypatch)
    if failure == "ledger":
        monkeypatch.setattr(smoke, "_accounting", Mock(side_effect=ValueError("PRIVATE_DB_FAILURE")))
    else:
        original = smoke._json
        def write(path, value):
            if path.name == "candidate.json": raise OSError("PRIVATE_LOCAL_FAILURE")
            return original(path, value)
        monkeypatch.setattr(smoke, "_json", write)
    code, receipt = run(tmp_path, execute=True, paid_authorized=True)
    assert code == 3 and len(calls) == 2 and receipt["model_requests"] == 2
    assert Decimal(receipt["total_reserved_cny"]) > 0
    assert receipt["charged_cost_cny"] == (None if failure == "ledger" else "0.00012")
    if failure == "ledger":
        assert not receipt["usage_known"] and receipt["accounting_basis"] == "UNAVAILABLE_LEDGER"
    assert "PRIVATE_DB_FAILURE" not in canonical_json(receipt) and "PRIVATE_LOCAL_FAILURE" not in canonical_json(receipt)


def test_package_investigation_smoke_default_fields_and_generated_order_are_not_overconstrained(tmp_path):
    root = tmp_path / "fixture"
    root.mkdir(mode=0o700)
    _, _, context = smoke.make_fixture(root)
    package = expected_package(context, "any-ids-")
    for collection in ("characters", "phases", "knowledge", "evidence", "truth"):
        package[collection].reverse()
    package["mechanics"]["phase_budgets"].reverse()
    package["mechanics"]["actions"].reverse()
    for action in package["mechanics"]["actions"]:
        action["allowed_character_ids"].reverse()
        action.setdefault("required_action_ids", [])
        action.setdefault("required_public_evidence_ids", [])
    assert smoke.validate_candidate(context, package)["both_human_roles_completed"]


def test_package_investigation_smoke_pre_dispatch_refusals_and_cli_guard(tmp_path, monkeypatch, capsys):
    calls, factory, _ = fake_sdk(monkeypatch)
    code, receipt = run(tmp_path, execute=True, paid_authorized=False)
    assert code == 3 and receipt["result_code"] == "SMOKE_PAID_CALLS_DISABLED" and not calls
    factory.assert_not_called()
    loader = Mock()
    monkeypatch.setattr(smoke, "load_selected_config", loader)
    monkeypatch.setattr(smoke, "read_paid_authorization", Mock(return_value=False))
    assert smoke.main(["--execute"]) == 2
    assert smoke.main(["--execute", "--confirm-paid", smoke.CONFIRM_PAID]) == 2
    loader.assert_not_called()
    assert "KEY" not in capsys.readouterr().out


def test_package_investigation_smoke_expensive_config_refuses_before_queue_or_sdk(tmp_path, monkeypatch):
    _, factory, _ = fake_sdk(monkeypatch)
    config = fixture_config()
    config = replace(config, pricing=replace(config.pricing, output_rate_cny=Decimal("100")))
    code, receipt = asyncio.run(smoke.run_smoke(config, output_root=tmp_path, execute=True, paid_authorized=True))
    assert code == 3 and receipt["model_requests"] == 0
    assert receipt["result_code"] == "AUTHORING_RESERVATION_TOO_LARGE"
    factory.assert_not_called()


def test_package_investigation_smoke_private_output_and_selected_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "REPOSITORY", tmp_path / "repo")
    with pytest.raises(smoke.InvestigationSmokeError): smoke.new_directory(tmp_path / "repo" / "out")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(smoke.InvestigationSmokeError): smoke.new_directory(public)
    link = tmp_path / "linked"
    link.symlink_to(tmp_path)
    with pytest.raises(smoke.InvestigationSmokeError): smoke.new_directory(link / "child")
    sample = tmp_path / "synthetic.settings"
    sample.write_text("OTHER_PROVIDER_KEY=DO_NOT_PRINT\nENABLE_PAID_MODEL_CALLS=true\n")
    monkeypatch.delenv("ENABLE_PAID_MODEL_CALLS", raising=False)
    assert smoke.read_paid_authorization(sample)
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "false")
    assert not smoke.read_paid_authorization(sample)
