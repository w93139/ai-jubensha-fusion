"""Synthetic-only smoke harness through real services and an offline SDK stub."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, text

from src.fusion import package_play_smoke as smoke
from src.fusion.budget import BudgetPolicy
from src.fusion.package_play_rules import PackagePlayRules
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.fusion.provider_smoke import SelectedSmokeConfig
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.services.llm_service import LLMResponse, ToolCall


def configuration(provider="volcengine_ark", rate="0.4"):
    profile = PLAYER_PROVIDER_PROFILES[provider]
    pricing = BudgetPolicy(10000, Decimal("0.01"), Decimal(rate), Decimal(rate), Decimal("1"), True, "synthetic-price-v1")
    return SelectedSmokeConfig(profile, "SYNTHETIC_API_KEY_NOT_REAL", profile.default_base_url, profile.default_model, pricing)


def sdk_response(provider="volcengine_ark", **changes):
    response = LLMResponse('{"refs":[{"collection":"evidence","id":"blue-seal"}]}',
                           model=PLAYER_PROVIDER_PROFILES[provider].default_model, finish_reason="stop",
                           usage={"prompt_tokens": 100, "completion_tokens": 20})
    return replace(response, **changes)


def run_case(tmp_path, *, provider="volcengine_ark", execute=True, authorized=True, response=None, error=None, cost="0.01", config=None):
    client = SimpleNamespace(chat_completion=AsyncMock(return_value=response or sdk_response(provider), side_effect=error))
    code, receipt = asyncio.run(smoke.run_smoke(config or configuration(provider), Decimal(cost), output_root=tmp_path,
                                              execute=execute, paid_authorized=authorized, client=client))
    return code, receipt, client, Path(receipt["artifact_directory"])


@pytest.mark.parametrize("provider", list(PLAYER_PROVIDER_PROFILES))
def test_package_play_smoke_real_services_one_call_shares_unlocks_settles_and_replays(tmp_path, provider):
    code, receipt, client, root = run_case(tmp_path, provider=provider)
    assert code == 0 and receipt["status"] == "PASSED", receipt
    assert receipt["publication_mode"] == "SIMULATED_FIXTURE_ONLY" and receipt["runtime_ready"] is False
    assert receipt["model_requests"] == receipt["model_attempts"] == 1
    assert receipt["usage_known"] and receipt["usage"]["prompt_tokens"] == 100
    assert receipt["charged_cost_cny"] == "0.00006"
    assert receipt["event_count"] == 5 and receipt["replay_verified"] and receipt["settlement_verified"]
    client.chat_completion.assert_awaited_once()
    messages = client.chat_completion.call_args.args[0]
    prepared_payload = json.loads(messages[1].content)
    assert {item["id"] for item in prepared_payload["context"]["materials"]} == {"blue-seal", "public-notice"}
    raw = canonical_json([{"role": item.role, "content": item.content} for item in messages])
    assert all(marker not in raw for marker in smoke.FORBIDDEN_MARKERS)
    assert smoke.ALLOWED_EVIDENCE in raw and "relative_path" not in raw and "sources" not in raw
    params = client.chat_completion.call_args.kwargs
    assert params["response_format"]["json_schema"]["name"] == "package_material_selection"
    assert params.get("max_tokens", params.get("max_completion_tokens")) == 256
    assert receipt["prompt_bytes"] == len(raw.encode())
    assert receipt["input_token_upper_bound"] == receipt["prompt_bytes"] + 4096
    assert json.loads((root / "dispatch.json").read_text())["reservation_committed"]
    assert json.loads((root / "receipt.json").read_text()) == receipt
    for item in (root, *root.rglob("*")):
        assert stat.S_IMODE(item.stat().st_mode) == (0o700 if item.is_dir() else 0o600)
    assert "SYNTHETIC_API_KEY_NOT_REAL" not in "".join(path.read_text() for path in root.rglob("*.json"))
    assert all(marker not in canonical_json(receipt) for marker in smoke.FORBIDDEN_MARKERS)


def test_package_play_smoke_preview_is_real_binding_but_zero_events_and_sdk_calls(tmp_path):
    code, receipt, client, root = run_case(tmp_path, execute=False, authorized=False)
    assert code == 0 and receipt["status"] == "PREVIEW" and not receipt["paid_authorized"]
    assert receipt["model_attempts"] == receipt["model_requests"] == receipt["event_count"] == 0
    client.chat_completion.assert_not_awaited()
    assert not (root / "dispatch.json").exists() and not (root / "model-result.json").exists()
    engine = create_engine("sqlite:///" + str(root / "smoke.sqlite"))
    with engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM script_package_plays")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM script_package_play_events")).scalar_one() == 0
    engine.dispose()


def test_package_play_smoke_local_artifact_failure_keeps_committed_known_usage(tmp_path, monkeypatch):
    write_json = smoke._json

    def fail_answer(path, value):
        if path.name == "after-answer.json":
            raise OSError("PRIVATE_DISK_ERROR_SENTINEL")
        write_json(path, value)

    monkeypatch.setattr(smoke, "_json", fail_answer)
    code, receipt, client, root = run_case(tmp_path)
    assert code != 0 and receipt["status"] == "FAILED"
    assert receipt["result_code"] == "SMOKE_LOCAL_FAILURE" and receipt["usage_known"]
    assert receipt["charged_cost_cny"] == "0.00006" and receipt["last_ai_status"] == "OK"
    client.chat_completion.assert_awaited_once()
    assert not (root / "settled-view.json").exists()
    assert json.loads((root / "receipt.json").read_text()) == receipt
    assert "PRIVATE_DISK_ERROR_SENTINEL" not in canonical_json(receipt)
    engine = create_engine("sqlite:///" + str(root / "smoke.sqlite"))
    with engine.connect() as db:
        event = json.loads(db.execute(text(
            "SELECT event_json FROM script_package_play_events WHERE kind='AI_RESULT'"
        )).scalar_one())
        assert event["data"]["accounted"]["cost_cny"] == receipt["charged_cost_cny"]
    engine.dispose()


@pytest.mark.parametrize("authorized,cost,expected", [(False, "0.01", "SMOKE_PAID_CALLS_DISABLED"),
                                                    (True, "0.000001", "SMOKE_RESERVATION_EXCEEDS_LIMIT")])
def test_package_play_smoke_refuses_before_network_and_keeps_receipt(tmp_path, authorized, cost, expected):
    code, receipt, client, root = run_case(tmp_path, authorized=authorized, cost=cost)
    assert code != 0 and receipt["result_code"] == expected and receipt["model_requests"] == 0
    client.chat_completion.assert_not_awaited()
    assert (root / "receipt.json").is_file() and not (root / "dispatch.json").exists()


@pytest.mark.parametrize("response,expected_status,known", [
    (sdk_response(content='{"refs":[]}'), "OK", True),
    (sdk_response(content='{"refs":[{"collection":"knowledge","id":"public-notice"}]}'), "OK", True),
    (sdk_response(content='{"refs":[{"collection":"knowledge","id":"b-keep"}]}'), "INVALID", True),
    (sdk_response(content='{"refs":[],"message":"PRIVATE_PROVIDER_BODY_SENTINEL"}'), "INVALID", True),
    (sdk_response(content='{"refs":[],"refs":[]}'), "INVALID", True),
    (sdk_response(reasoning_content="PRIVATE_PROVIDER_REASONING_SENTINEL"), "INVALID", True),
    (sdk_response(finish_reason="length"), "INVALID", True),
    (sdk_response(model="PRIVATE_PROVIDER_MODEL_SENTINEL"), "INVALID", True),
    (sdk_response(usage=None), "UNKNOWN", False),
])
def test_package_play_smoke_bad_or_irrelevant_response_never_passes_or_retries(tmp_path, response, expected_status, known):
    code, receipt, client, root = run_case(tmp_path, response=response)
    assert code != 0 and receipt["status"] == "FAILED"
    assert receipt["last_ai_status"] == expected_status and receipt["usage_known"] is known
    client.chat_completion.assert_awaited_once()
    assert receipt["model_requests"] == 1
    assert receipt["charged_cost_cny"] == ("0.00006" if known else receipt["reservation"]["cost_cny"])
    assert not (root / "settled-view.json").exists()
    assert "PRIVATE_PROVIDER_" not in "".join(path.read_text() for path in root.rglob("*.json"))
    engine = create_engine("sqlite:///" + str(root / "smoke.sqlite"))
    with engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM script_package_play_events")).scalar_one() == 2
    engine.dispose()


def test_package_play_smoke_transport_unknown_keeps_full_reservation_and_new_invocation(tmp_path):
    roots = []
    for _ in range(2):
        code, receipt, client, root = run_case(tmp_path, error=RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL"))
        roots.append(root)
        assert code != 0 and receipt["last_ai_status"] == "UNKNOWN" and not receipt["usage_known"]
        assert receipt["charged_cost_cny"] == receipt["reservation"]["cost_cny"]
        client.chat_completion.assert_awaited_once()
        assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in canonical_json(receipt)
    assert roots[0] != roots[1] and all((root / "receipt.json").exists() for root in roots)


def test_package_play_smoke_source_text_and_rules_are_actual_and_valid(tmp_path):
    root = smoke.new_run_directory(tmp_path)
    fixture = smoke.make_fixture(root)
    assert validate_package(fixture.package)["valid"]
    assert fixture.sources.verify(fixture.bundle_hash, document=fixture.package, persist=False)["valid"]
    raw, _ = fixture.sources.read_source(fixture.bundle_hash, fixture.package["sources"][0]["id"])
    assert all(marker in raw.decode() for marker in smoke.FORBIDDEN_MARKERS)
    assert "required_public_evidence_ids" in raw.decode() and "KEEP_PRIVATE" in raw.decode()
    # Source mutation is rejected before treating simulated approval as current.
    publisher = smoke.FixturePublisher(fixture, 1)
    source = fixture.sources.root / fixture.bundle_hash / "files/fixture.md"
    source.write_text("PRIVATE_CHANGED_SOURCE_SENTINEL")
    with pytest.raises(smoke.PackagePlaySmokeError, match="SMOKE_SOURCE_VERIFICATION_FAILED"):
        publisher.get_release(1)


@pytest.mark.parametrize("change", ["scope", "question", "budget"])
def test_package_play_smoke_preflight_guard_rejects_changed_input(change):
    package, _ = smoke.synthetic_package()
    model = smoke.make_model(configuration(), False)
    prepared = model.prepare(PackagePlayRules(package, "a").role_context("b"), smoke.QUESTION)
    if change == "budget":
        prepared["input_tokens"] -= 1
    else:
        payload = json.loads(prepared["messages"][1]["content"])
        if change == "scope":
            payload["context"]["materials"].append({"collection": "knowledge", "id": "b-keep", "kind": "FACT", "text": smoke.FORBIDDEN_MARKERS[0]})
        else:
            payload["question"] = "PRIVATE_QUESTION_CHANGE"
        prepared["messages"][1]["content"] = canonical_json(payload)
    with pytest.raises(smoke.PackagePlaySmokeError):
        smoke.verify_prepared(package, prepared)


def test_package_play_smoke_output_rejects_repository_symlink_and_public_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "REPOSITORY", tmp_path / "repo")
    with pytest.raises(smoke.PackagePlaySmokeError, match="INSIDE_REPOSITORY"):
        smoke.new_run_directory(tmp_path / "repo" / "private")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(smoke.PackagePlaySmokeError, match="SYMLINK"):
        smoke.new_run_directory(link / "child")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(smoke.PackagePlaySmokeError, match="NOT_PRIVATE"):
        smoke.new_run_directory(public)


def test_package_play_smoke_authorization_load_is_selected_and_override_can_disable(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.env"
    path.write_text('OTHER_KEY="PRIVATE_UNRELATED_KEY"\nENABLE_PAID_MODEL_CALLS=true\n')
    monkeypatch.delenv("ENABLE_PAID_MODEL_CALLS", raising=False)
    assert smoke.read_paid_authorization(path)
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "false")
    assert not smoke.read_paid_authorization(path)
    monkeypatch.delenv("ENABLE_PAID_MODEL_CALLS")
    path.write_text("ENABLE_PAID_MODEL_CALLS=true\nENABLE_PAID_MODEL_CALLS=false\n")
    with pytest.raises(smoke.PackagePlaySmokeError, match="AMBIGUOUS"):
        smoke.read_paid_authorization(path)


def test_package_play_smoke_cli_requires_exact_confirmation_before_loading_configuration(monkeypatch, capsys):
    loader = Mock(side_effect=AssertionError("must not read configuration"))
    monkeypatch.setattr(smoke, "load_selected_config", loader)
    assert smoke.main(["--provider", "volcengine_ark", "--max-cost-cny", "0.01", "--execute"]) == 2
    loader.assert_not_called()
    assert json.loads(capsys.readouterr().out)["result_code"] == "SMOKE_CONFIRMATION_REQUIRED"


def test_package_play_smoke_cli_preview_safe_output_and_configuration_restore(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_selected_config", lambda _provider: configuration())
    monkeypatch.setattr(smoke, "read_paid_authorization", lambda: False)
    monkeypatch.setenv("ARK_API_KEY", "SYNTHETIC_OLD_KEY")
    monkeypatch.setenv("ARK_BASE_URL", "SYNTHETIC_OLD_ENDPOINT")
    assert smoke.main(["--provider", "volcengine_ark", "--max-cost-cny", "0.01", "--output-root", str(tmp_path)]) == 0
    import os
    assert os.environ["ARK_API_KEY"] == "SYNTHETIC_OLD_KEY"
    assert os.environ["ARK_BASE_URL"] == "SYNTHETIC_OLD_ENDPOINT"
    public = capsys.readouterr().out
    assert "SYNTHETIC_OLD" not in public and "SYNTHETIC_API_KEY" not in public
    assert smoke.QUESTION not in public and smoke.ALLOWED_EVIDENCE not in public
    assert json.loads(public)["model_requests"] == 0
