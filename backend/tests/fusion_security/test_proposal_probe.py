"""Offline acceptance wiring and billing guards; mock choices are not AI evidence."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.fusion import proposal_probe as probe
from src.fusion.package_proposal_model import PackageProposalModel
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_authoring_model import fixture_config


@pytest.fixture(params=["volcengine_ark", "aliyun_bailian"])
def config(request, monkeypatch):
    profile = PLAYER_PROVIDER_PROFILES[request.param]
    # Exercise either provider without reading local configuration or credentials.
    monkeypatch.delenv(profile.base_url_env, raising=False)
    return replace(fixture_config(), profile=profile, base_url=profile.default_base_url,
                   model=profile.default_model)


def execute(config, root, digest=None):
    return asyncio.run(probe.run_probe(config, root, execute=True, paid_authorized=True,
                                      expected_hash=digest or content_hash(probe.prepare_suite(config))))


def read(path):
    return json.loads(path.read_text())


def sdk_stub(monkeypatch, config, mode="ok", before_reply=None, close_error=False):
    rows = {content_hash(row["context"]): row for row in probe.cases()}
    clients = []

    def construct(**kwargs):
        if mode == "construction":
            raise RuntimeError("PRIVATE_EXCEPTION")

        async def respond(messages, **params):
            if before_reply:
                before_reply(messages, params)
            if mode == "timeout":
                raise TimeoutError("PRIVATE_EXCEPTION")
            if mode == "cancel":
                raise asyncio.CancelledError()
            context = json.loads(messages[1].content)["context"]
            row = rows[content_hash(context)]
            criterion = row["criterion"]
            output = {"action_id": criterion["expected_action"], "public_basis": [
                dict(zip(("collection", "id"), reference.split(":")))
                for reference in criterion["required_basis"]]}
            if mode == "private-ref":
                output["public_basis"] = [{"collection": "knowledge", "id": "goal"}]
            elif mode == "wrong-choice":
                output["action_id"] = "workshop" if output["action_id"] == "archive" else "archive"
            elif mode == "extra-prose":
                output["statement"] = "PRIVATE_MODEL_PROSE"
            usage = {"prompt_tokens": 100, "completion_tokens": 20}
            if mode == "no-usage":
                usage = None
            elif mode == "over-input":
                usage["prompt_tokens"] = 100000
            elif mode == "over-output":
                usage["completion_tokens"] = config.profile.reserved_completion_tokens(probe.MAX_OUTPUT) + 1
            return LLMResponse(json.dumps(output), model=config.model, usage=usage,
                               finish_reason="length" if mode == "truncated" else "stop")

        close = AsyncMock(side_effect=RuntimeError("PRIVATE_CLOSE") if close_error else None)
        client = SimpleNamespace(chat_completion=AsyncMock(side_effect=respond),
                                 _client=SimpleNamespace(close=close))
        clients.append(client)
        return client

    factory = Mock(side_effect=construct)
    monkeypatch.setattr(probe, "OpenAILLMService", factory)
    return factory, clients


def assert_closed(clients):
    for client in clients:
        client._client.close.assert_awaited_once()


def test_proposal_probe_freezes_eight_cases_separate_from_messages(config):
    suite = probe.prepare_suite(config)
    assert [p["case"] for p in suite["packets"]] == [
        "baseline", "unsupported-pressure", "new-fact", "different-goal",
        "injected-claim", "specific-new-claim", "private-only-basis", "irrelevant-public-fact"]
    assert suite["model"] == probe.adapter(config).metadata()
    assert suite["model"]["schema_version"] == "package-proposal-model/1.0"
    assert suite["model"]["thinking_mode"] == "disabled"
    assert suite["model"]["max_attempts"] == 1
    assert suite["limits"]["calls"] == 8
    assert sum(Decimal(p["reservation"]["cost_cny"]) for p in suite["packets"]) <= Decimal("0.10")
    for packet in suite["packets"]:
        prepared = packet["prepared"]
        assert prepared == probe.adapter(config).prepare(packet["context"])
        assert json.loads(prepared["messages"][1]["content"]) == {
            "context": packet["context"], "question": "PROPOSE"}
        assert not {"criterion", "expected_action", "allowed_basis", "required_basis"} & set(
            json.loads(prepared["messages"][1]["content"]))
        assert all(key not in canonical_json(prepared["messages"]) for key in packet["criterion"])
        assert prepared["input_tokens"] == (
            len(canonical_json(prepared["messages"]).encode()) + 4096
            + len(canonical_json(prepared["params"]["response_format"]).encode()))
        assert prepared["params"]["response_format"]["json_schema"]["schema"]["properties"]["action_id"]["enum"] == ["archive", "workshop"]
        assert Decimal(packet["reservation"]["cost_cny"]) <= Decimal("0.02")
    assert "OWN_PRIVATE_481" in canonical_json(suite["packets"][6]["prepared"]["messages"])
    assert config.api_key not in canonical_json(suite)
    assert suite["publication_ready"] is False


def test_proposal_probe_preview_closed_gate_and_wrong_hash_never_construct_sdk(config, tmp_path, monkeypatch):
    factory, _ = sdk_stub(monkeypatch, config)
    preview = asyncio.run(probe.run_probe(config, tmp_path))
    assert preview["status"] == "PREVIEW"
    assert preview["model_requests"] == 0 and preview["accounted_cost_cny"] == "0"
    assert read(Path(preview["directory"]) / "receipt.json") == preview
    for authorized in (False, None, 1, "true"):
        with pytest.raises(ValueError, match="PAID_GATE_CLOSED"):
            asyncio.run(probe.run_probe(config, tmp_path, execute=True,
                paid_authorized=authorized, expected_hash=preview["suite_hash"]))
    with pytest.raises(ValueError, match="SUITE_CHANGED"):
        execute(config, tmp_path, "0" * 64)
    factory.assert_not_called()
    assert not list(tmp_path.glob("executed-*.json"))


def test_proposal_probe_reuses_shipping_prepare_call_render_and_durable_reservation(config, tmp_path, monkeypatch):
    real_prepare, real_call = PackageProposalModel.prepare, PackageProposalModel.call
    preparations, calls = [], []

    def prepare(self, *args, **kwargs):
        preparations.append(deepcopy(args[0]))
        return real_prepare(self, *args, **kwargs)

    async def call(self, prepared):
        calls.append(deepcopy(prepared))
        return await real_call(self, prepared)

    monkeypatch.setattr(PackageProposalModel, "prepare", prepare)
    monkeypatch.setattr(PackageProposalModel, "call", call)
    suite = probe.prepare_suite(config)
    digest = content_hash(suite)

    def before_reply(messages, params):
        claim = read(tmp_path / f"executed-{digest}.json")
        directory = Path(claim["directory"])
        index = len(calls) - 1
        dispatch = read(directory / f"dispatch-{index}.json")
        assert dispatch["status"] == "IN_FLIGHT"
        assert dispatch["reservation"] == suite["packets"][index]["reservation"]
        assert dispatch["context_hash"] == suite["packets"][index]["prepared"]["context_hash"]
        assert not (directory / f"result-{index}.json").exists()
        assert [dict(role=m.role, content=m.content) for m in messages] == calls[index]["messages"]
        assert params == calls[index]["params"]

    factory, clients = sdk_stub(monkeypatch, config, before_reply=before_reply)
    result = execute(config, tmp_path, digest)
    assert result["status"] == "COLLECTED" and result["model_requests"] == 8
    assert len(calls) == 8 and len(preparations) >= 24
    assert result["semantic_status"] == "UNREVIEWED"
    assert result["runtime_ready"] is False and result["publication_ready"] is False
    assert all(r["status"] == "OK" and all(r["checks"].values()) for r in result["results"])
    assert all(r["public_entry"]["kind"] == "CLAIM" for r in result["results"])
    claim_basis = result["results"][5]["public_entry"]["basis"]
    assert claim_basis[0]["kind"] == "CLAIM" and claim_basis[0]["speaker"] == "zhou"
    for index in (3, 6):
        assert result["results"][index]["public_entry"]["basis"] == []
    public = canonical_json([r["public_entry"] for r in result["results"]])
    assert "OWN_PRIVATE_481" not in public and "私信" not in public
    expected = config.pricing.amount(100, 20).cost_cny * 8
    assert Decimal(result["accounted_cost_cny"]) == expected
    for constructor in factory.call_args_list:
        assert constructor.kwargs == dict(api_key=config.api_key, base_url=config.base_url,
                                          model=config.model, client_max_retries=0)
    assert_closed(clients)
    directory = Path(result["directory"])
    assert read(directory / "receipt.json") == result
    assert [read(directory / f"result-{i}.json") for i in range(8)] == result["results"]
    assert config.api_key not in canonical_json(result)
    with pytest.raises(FileExistsError):
        execute(config, tmp_path, digest)
    assert factory.call_count == 8


@pytest.mark.parametrize("mode", ["private-ref", "extra-prose", "truncated"])
def test_proposal_probe_known_invalid_billed_once_each_and_not_public(config, tmp_path, monkeypatch, mode):
    factory, clients = sdk_stub(monkeypatch, config, mode)
    result = execute(config, tmp_path)
    assert result["status"] == "COLLECTED" and result["model_requests"] == 8
    assert all(r["status"] == "INVALID" and r["usage_known"] for r in result["results"])
    assert all(r["output"] is r["public_entry"] is r["checks"] is None for r in result["results"])
    assert Decimal(result["accounted_cost_cny"]) == config.pricing.amount(100, 20).cost_cny * 8
    assert result["semantic_status"] == "UNREVIEWED"
    assert "PRIVATE_MODEL_PROSE" not in canonical_json(result)
    with pytest.raises(FileExistsError):
        execute(config, tmp_path, result["suite_hash"])
    assert factory.call_count == 8
    assert_closed(clients)


def test_proposal_probe_format_success_keeps_wrong_choice_as_failed_check(config, tmp_path, monkeypatch):
    sdk_stub(monkeypatch, config, "wrong-choice")
    result = execute(config, tmp_path)
    assert result["status"] == "COLLECTED"
    assert all(r["status"] == "OK" and r["checks"]["expected_choice"] is False for r in result["results"])
    assert result["semantic_status"] == "UNREVIEWED"


@pytest.mark.parametrize("mode", ["timeout", "no-usage", "construction"])
def test_proposal_probe_unknown_stops_and_retains_reservation_without_retry(config, tmp_path, monkeypatch, mode):
    factory, clients = sdk_stub(monkeypatch, config, mode)
    suite = probe.prepare_suite(config)
    result = execute(config, tmp_path, content_hash(suite))
    assert result["status"] == "STOPPED" and len(result["results"]) == 1
    assert result["model_requests"] == (0 if mode == "construction" else 1)
    item = result["results"][0]
    assert item["status"] == "UNKNOWN" and item["usage_known"] is False
    assert item["output"] is item["public_entry"] is item["checks"] is None
    assert result["accounted_cost_cny"] == suite["packets"][0]["reservation"]["cost_cny"]
    assert item["cost_cny"] == result["accounted_cost_cny"]
    assert "PRIVATE_EXCEPTION" not in canonical_json(result)
    with pytest.raises(FileExistsError):
        execute(config, tmp_path, result["suite_hash"])
    assert factory.call_count == 1
    assert_closed(clients)


def test_proposal_probe_cancellation_keeps_durable_unknown_and_closes_sdk(config, tmp_path, monkeypatch):
    factory, clients = sdk_stub(monkeypatch, config, "cancel")
    suite = probe.prepare_suite(config)
    digest = content_hash(suite)
    with pytest.raises(asyncio.CancelledError):
        execute(config, tmp_path, digest)
    directory = Path(read(tmp_path / f"executed-{digest}.json")["directory"])
    receipt = read(directory / "receipt.json")
    item = read(directory / "result-0.json")
    assert receipt["status"] == "STOPPED" and receipt["model_requests"] == 1
    assert receipt["results"] == [item]
    assert item["status"] == "UNKNOWN" and item["model_attempted"] is True
    assert item["usage_known"] is False
    assert receipt["accounted_cost_cny"] == suite["packets"][0]["reservation"]["cost_cny"]
    with pytest.raises(FileExistsError):
        execute(config, tmp_path, digest)
    assert factory.call_count == 1
    assert_closed(clients)


@pytest.mark.parametrize("mode", ["over-input", "over-output"])
def test_proposal_probe_usage_or_money_anomaly_accounts_actual_and_stops(config, tmp_path, monkeypatch, mode):
    factory, clients = sdk_stub(monkeypatch, config, mode)
    result = execute(config, tmp_path)
    assert result["status"] == "STOPPED" and result["model_requests"] == 1
    item = result["results"][0]
    assert item["status"] == "BUDGET_ANOMALY" and item["usage_known"] is True
    assert item["public_entry"] is item["output"] is item["checks"] is None
    usage = item["usage"]
    amount = config.pricing.amount(usage["prompt_tokens"], usage["completion_tokens"], usage["cached_prompt_tokens"])
    assert Decimal(result["accounted_cost_cny"]) == amount.cost_cny
    with pytest.raises(FileExistsError):
        execute(config, tmp_path, result["suite_hash"])
    assert factory.call_count == 1
    assert_closed(clients)


def test_proposal_probe_close_failure_does_not_discard_usage(config, tmp_path, monkeypatch):
    _, clients = sdk_stub(monkeypatch, config, close_error=True)
    result = execute(config, tmp_path)
    assert result["status"] == "COLLECTED" and all(r["usage_known"] for r in result["results"])
    assert "PRIVATE_CLOSE" not in canonical_json(result)
    assert_closed(clients)


@pytest.mark.parametrize("limit", ["MAX_TOTAL", "MAX_SINGLE"])
def test_proposal_probe_all_budget_checks_precede_claim_and_sdk(config, tmp_path, monkeypatch, limit):
    factory, _ = sdk_stub(monkeypatch, config)
    monkeypatch.setattr(probe, limit, Decimal("0.0000001"))
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        asyncio.run(probe.run_probe(config, tmp_path, execute=True, paid_authorized=True, expected_hash="0" * 64))
    factory.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["key", "placeholder-key", "model", "endpoint", "profile", "paid",
    "version", "placeholder-version", "zero-input", "negative-cached", "nan-output", "infinite-input", "cached-over-input"])
def test_proposal_probe_invalid_configuration_is_rejected_before_sdk(config, tmp_path, monkeypatch, mutation):
    factory, _ = sdk_stub(monkeypatch, config)
    if mutation == "key":
        config = replace(config, api_key=" ")
    elif mutation == "placeholder-key":
        config = replace(config, api_key="CHANGE_ME_KEY")
    elif mutation == "model":
        config = replace(config, model="unapproved-model")
    elif mutation == "endpoint":
        config = replace(config, base_url="https://unapproved.invalid/v1")
    elif mutation == "profile":
        config = replace(config, profile=replace(config.profile, request_contract_version="tampered"))
    else:
        field, value = {"paid": ("paid_calls_enabled", 1), "version": ("pricing_version", " "),
            "placeholder-version": ("pricing_version", "CHANGE_ME_VERSION"),
            "zero-input": ("input_rate_cny", Decimal("0")),
            "negative-cached": ("cached_input_rate_cny", Decimal("-1")),
            "cached-over-input": ("cached_input_rate_cny", Decimal("1000")),
            "nan-output": ("output_rate_cny", Decimal("NaN")),
            "infinite-input": ("input_rate_cny", Decimal("Infinity"))}[mutation]
        config = replace(config, pricing=replace(config.pricing, **{field: value}))
    with pytest.raises(ValueError, match="CONFIG_INVALID"):
        asyncio.run(probe.run_probe(config, tmp_path, execute=True, paid_authorized=True, expected_hash="0" * 64))
    factory.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_proposal_probe_changed_frozen_file_stops_before_next_call(config, tmp_path, monkeypatch):
    digest = content_hash(probe.prepare_suite(config))

    def tamper(messages, params):
        directory = Path(read(tmp_path / f"executed-{digest}.json")["directory"])
        path = directory / "suite.json"
        frozen = read(path)
        frozen["packets"][1]["criterion"]["expected_action"] = "tampered"
        path.write_text(canonical_json(frozen))

    factory, clients = sdk_stub(monkeypatch, config, before_reply=tamper)
    with pytest.raises(ValueError, match="FROZEN_INPUT_CHANGED"):
        execute(config, tmp_path, digest)
    assert factory.call_count == 1
    assert_closed(clients)
    with pytest.raises(FileExistsError):
        execute(config, tmp_path, digest)
    assert factory.call_count == 1


def test_proposal_probe_prepared_change_stops_before_sdk_construction(config, tmp_path, monkeypatch):
    digest = content_hash(probe.prepare_suite(config))
    factory, _ = sdk_stub(monkeypatch, config)
    real_adapter = probe.adapter
    count = 0

    def changed_adapter(selected):
        nonlocal count
        count += 1
        model = real_adapter(selected)
        if count > 1:
            real_prepare = model.prepare
            model.prepare = lambda context: {**real_prepare(context), "input_tokens": 1}
        return model

    monkeypatch.setattr(probe, "adapter", changed_adapter)
    with pytest.raises(ValueError, match="PREPARED_CHANGED"):
        execute(config, tmp_path, digest)
    factory.assert_not_called()
    assert (tmp_path / f"executed-{digest}.json").exists()


def test_proposal_probe_reference_checks_distinguish_relevance_from_valid_format():
    baseline = probe.cases()[0]["criterion"]
    assert probe.assess({"action_id": "archive", "public_basis": []}, baseline) == {
        "expected_choice": True, "allowed_basis": True, "required_basis": False}
    assert probe.assess({"action_id": "archive", "public_basis": [
        {"collection": "evidence", "id": "delivery"},
        {"collection": "evidence", "id": "workshop-paint"}]}, baseline) == {
        "expected_choice": True, "allowed_basis": False, "required_basis": True}


@pytest.fixture
def cli(monkeypatch):
    from src.fusion import investigation_authoring_smoke, provider_smoke
    path = Path(__file__).resolve().parents[2] / "scripts/real_proposal_probe.py"
    spec = importlib.util.spec_from_file_location("proposal_probe_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gate = Mock(return_value=False)
    selected = Mock(return_value=fixture_config())
    run = AsyncMock(return_value={"status": "PREVIEW", "suite_hash": "1" * 64,
        "directory": "/private/synthetic", "model": "fixture", "model_requests": 0,
        "accounted_cost_cny": "0", "semantic_status": "UNREVIEWED"})
    monkeypatch.setattr(investigation_authoring_smoke, "read_paid_authorization", gate)
    monkeypatch.setattr(provider_smoke, "load_selected_config", selected)
    monkeypatch.setattr(probe, "run_probe", run)
    return module, gate, selected, run


def test_proposal_probe_cli_preview_does_not_read_paid_gate(cli, capsys):
    module, gate, selected, run = cli
    assert module.main([]) == 0
    gate.assert_not_called()
    selected.assert_called_once_with("volcengine_ark")
    assert run.await_args.kwargs == dict(execute=False, expected_hash=None, paid_authorized=False)
    assert json.loads(capsys.readouterr().out)["status"] == "PREVIEW"


@pytest.mark.parametrize("authorized,hash_given", [(False, True), (True, False)])
def test_proposal_probe_cli_refuses_gate_or_missing_hash_before_loading_config(cli, capsys, authorized, hash_given):
    module, gate, selected, run = cli
    gate.return_value = authorized
    args = ["--execute"] + (["--expected-hash", "1" * 64] if hash_given else [])
    assert module.main(args) == 2
    selected.assert_not_called()
    run.assert_not_awaited()
    assert "不会自动重发" in capsys.readouterr().err


def test_proposal_probe_cli_forwards_explicit_gate_hash_and_selected_provider(cli, capsys):
    module, gate, selected, run = cli
    gate.return_value = True
    run.return_value = {**run.return_value, "status": "COLLECTED"}
    assert module.main(["--provider", "aliyun_bailian", "--execute", "--expected-hash", "1" * 64]) == 0
    selected.assert_called_once_with("aliyun_bailian")
    assert run.await_args.kwargs == dict(execute=True, expected_hash="1" * 64, paid_authorized=True)
    assert "SYNTHETIC_KEY_SENTINEL" not in capsys.readouterr().out
