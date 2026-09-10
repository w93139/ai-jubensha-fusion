"""Single-call material selection with synthetic SDK replies and no network."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.fusion import package_role_model
from src.fusion.agents import PlayerModelSettings
from src.fusion.budget import BudgetPolicy
from src.fusion.package_play_rules import PackagePlayRules
from src.fusion.package_role_model import PackageRoleModel, PackageRoleModelError
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.services.llm_service import LLMResponse, ToolCall
from tests.fusion_security.test_package_play_rules import play_package


def settings(provider="volcengine_ark", **changes):
    profile = PLAYER_PROVIDER_PROFILES[provider]
    value = PlayerModelSettings(provider, profile.default_model, 10, 3, 1000, 20000, "disabled", 0.7, True)
    return replace(value, **changes)


def response(content=None, provider="volcengine_ark", **changes):
    value = LLMResponse(content or '{"refs":[{"collection":"evidence","id":"b-card"}]}',
                        usage={"prompt_tokens": 100, "completion_tokens": 20},
                        model=PLAYER_PROVIDER_PROFILES[provider].default_model, finish_reason="stop")
    return replace(value, **changes)


def model_case(*, provider="volcengine_ark", result=None, **changes):
    client = SimpleNamespace(chat_completion=AsyncMock(return_value=result or response(provider=provider)))
    model = PackageRoleModel(client=client, settings=settings(provider, **changes))
    context = PackagePlayRules(play_package(), "a").role_context("b")
    return model, client, context, model.prepare(context, "你可以提供哪些相关资料？")


@pytest.mark.parametrize("provider", ["volcengine_ark", "aliyun_bailian"])
def test_package_role_model_has_one_provider_request_and_known_usage(provider):
    model, client, context, prepared = model_case(provider=provider)
    assert model.available and model.unavailable_reason is None
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "OK" and result["refs"] == [{"collection": "evidence", "id": "b-card"}]
    assert result["usage"]["prompt_tokens"] == 100 and result["usage"]["completion_tokens"] == 20
    assert result["model_attempted"] and client.chat_completion.await_count == 1
    params = client.chat_completion.call_args.kwargs
    assert params["response_format"]["json_schema"]["name"] == "package_material_selection"
    assert params["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    if provider == "volcengine_ark":
        assert params["max_tokens"] == 1000 and params["extra_body"] == {"thinking": {"type": "disabled"}}
        assert prepared["output_tokens"] == 1000
    else:
        assert params["max_completion_tokens"] == 1000 and params["extra_body"] == {"enable_thinking": False, "preserve_thinking": False}
        assert prepared["output_tokens"] == 1016
    assert model.metadata()["max_attempts"] == 1  # legacy settings.retries=3 cannot cause a retry


def test_package_role_model_disabled_can_prepare_but_never_dispatch():
    model, client, context, prepared = model_case(paid_calls_enabled=False)
    assert not model.available and model.unavailable_reason == "PACKAGE_ROLE_MODEL_DISABLED"
    assert prepared["context_hash"] == content_hash({"context": context, "question": "你可以提供哪些相关资料？"})
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "INVALID" and not result["model_attempted"] and result["usage"] is None
    client.chat_completion.assert_not_awaited()


@pytest.mark.parametrize("patch,reason", [
    ({"model": "PRIVATE_MODEL_SENTINEL"}, "PACKAGE_ROLE_MODEL_UNSUPPORTED"),
    ({"thinking_mode": "enabled"}, "PACKAGE_ROLE_CONFIG_INVALID"),
    ({"temperature": float("nan")}, "PACKAGE_ROLE_CONFIG_INVALID"),
    ({"max_output_tokens": True}, "PACKAGE_ROLE_CONFIG_INVALID"),
    ({"timeout_seconds": 999}, "PACKAGE_ROLE_CONFIG_INVALID"),
])
def test_package_role_model_bad_configuration_is_safe_and_not_callable(patch, reason):
    client = SimpleNamespace(chat_completion=AsyncMock())
    model = PackageRoleModel(client=client, settings=replace(settings(), **patch))
    assert not model.available and model.unavailable_reason == reason
    assert "PRIVATE_MODEL_SENTINEL" not in canonical_json(model.metadata())
    assert asyncio.run(model.call({}))["error_code"] == reason
    client.chat_completion.assert_not_awaited()


def test_package_role_model_endpoint_is_allowlisted_even_for_injected_client(monkeypatch):
    monkeypatch.setenv("ARK_BASE_URL", "https://PRIVATE_ENDPOINT_SENTINEL.invalid")
    client = SimpleNamespace(chat_completion=AsyncMock())
    model = PackageRoleModel(client=client, settings=settings())
    assert model.unavailable_reason == "PACKAGE_ROLE_ENDPOINT_UNSUPPORTED"
    assert "PRIVATE_ENDPOINT_SENTINEL" not in canonical_json(model.metadata())
    assert not asyncio.run(model.call({}))["model_attempted"]


def test_package_role_model_sdk_initialization_is_safe_and_retries_disabled(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "SYNTHETIC_FIXTURE_KEY_SENTINEL")
    constructor = Mock(side_effect=RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL"))
    monkeypatch.setattr(package_role_model, "OpenAILLMService", constructor)
    model = PackageRoleModel(settings=settings())
    context = PackagePlayRules(play_package(), "a").role_context("b")
    prepared = model.prepare(context, "问题")
    assert model.available and model.unavailable_reason is None
    constructor.assert_not_called()
    result = asyncio.run(model.call(prepared))
    assert result["error_code"] == "PACKAGE_ROLE_CLIENT_UNAVAILABLE" and not result["model_attempted"]
    assert model.unavailable_reason == "PACKAGE_ROLE_CLIENT_UNAVAILABLE"
    assert constructor.call_args.kwargs["client_max_retries"] == 0
    assert "SYNTHETIC_FIXTURE_KEY_SENTINEL" not in canonical_json(model.metadata())
    assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in str(result)


@pytest.mark.parametrize("outcome", ["known", "transport", "cancelled", "close-error", "sdk-init"])
def test_package_role_model_closes_only_owned_sdk_and_preserves_outcome(monkeypatch, outcome):
    monkeypatch.setenv("ARK_API_KEY", "SYNTHETIC_FIXTURE_KEY_SENTINEL")
    sdk = SimpleNamespace(close=AsyncMock())
    wrapper = SimpleNamespace(_client=sdk, _get_client=Mock(return_value=sdk), chat_completion=AsyncMock(return_value=response()))
    if outcome == "transport":
        wrapper.chat_completion.side_effect = TimeoutError("PRIVATE_TRANSPORT")
    elif outcome == "cancelled":
        wrapper.chat_completion.side_effect = asyncio.CancelledError()
    elif outcome == "close-error":
        sdk.close.side_effect = RuntimeError("PRIVATE_CLOSE_ERROR")
    elif outcome == "sdk-init":
        wrapper._get_client.side_effect = RuntimeError("PRIVATE_INIT_ERROR")
    constructor = Mock(return_value=wrapper)
    monkeypatch.setattr(package_role_model, "OpenAILLMService", constructor)
    model = PackageRoleModel(settings=settings())
    prepared = model.prepare(PackagePlayRules(play_package(), "a").role_context("b"), "问题")
    model.metadata()
    constructor.assert_not_called()
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(model.call(prepared))
    else:
        result = asyncio.run(model.call(prepared))
        assert "PRIVATE_" not in str(result)
        if outcome == "transport":
            assert result["status"] == "UNKNOWN" and result["model_attempted"]
        elif outcome == "sdk-init":
            assert result["status"] == "INVALID" and not result["model_attempted"]
            wrapper.chat_completion.assert_not_awaited()
        else:
            assert result["status"] == "OK" and result["usage"]["completion_tokens"] == 20
    sdk.close.assert_awaited_once()
    constructor.assert_called_once()


def test_package_role_model_injected_client_lifecycle_belongs_to_caller():
    model, client, _, prepared = model_case()
    client._client = SimpleNamespace(close=AsyncMock())
    assert asyncio.run(model.call(prepared))["status"] == "OK"
    client._client.close.assert_not_awaited()


def test_package_role_model_prepare_preserves_materials_and_counts_complete_schema_and_messages():
    model, _, context, prepared = model_case()
    payload = json.loads(prepared["messages"][1]["content"])
    assert payload["context"] == context
    assert "JSON Schema" in prepared["messages"][0]["content"]
    assert "package_material_selection" == prepared["params"]["response_format"]["json_schema"]["name"]
    assert prepared["input_tokens"] == len(canonical_json(prepared["messages"]).encode("utf-8")) + 4096
    assert all(marker not in canonical_json(prepared) for marker in (
        "AI_KEEP_PRIVATE_SENTINEL", "PRIVATE_a_SENTINEL", "SYSTEM_TRUTH_SENTINEL", "AI_FUTURE_SENTINEL"))
    off = PackageRoleModel(client=model.client, settings=replace(model.settings, paid_calls_enabled=False))
    assert off.metadata() == model.metadata() and off.prepare(context, payload["question"]) == prepared


@pytest.mark.parametrize("question", ["", " \n ", "问" * 1001, True])
def test_package_role_model_question_rejected_before_dispatch(question):
    model, client, context, _ = model_case()
    with pytest.raises(PackageRoleModelError, match="^PACKAGE_ROLE_INPUT_INVALID$"):
        model.prepare(context, question)
    client.chat_completion.assert_not_awaited()


@pytest.mark.parametrize("change", ["extra", "duplicate", "wrong-kind", "missing-kind"])
def test_package_role_model_projection_shape_is_strict(change):
    model, client, context, _ = model_case()
    if change == "extra":
        context["PRIVATE_EXTRA_SENTINEL"] = "secret"
    elif change == "duplicate":
        context["materials"].append(deepcopy(context["materials"][0]))
    elif change == "wrong-kind":
        context["materials"][-1]["kind"] = "FACT"  # evidence cannot become knowledge
    else:
        context["materials"][0].pop("kind")
    with pytest.raises(PackageRoleModelError, match="^PACKAGE_ROLE_INPUT_INVALID$"):
        model.prepare(context, "问题")
    client.chat_completion.assert_not_awaited()


def test_package_role_model_full_input_limit_rejects_without_truncating_context():
    model, client, context, _ = model_case()
    context["materials"][0]["text"] = "原文" * 10000
    original = deepcopy(context)
    with pytest.raises(PackageRoleModelError, match="^PACKAGE_ROLE_INPUT_TOO_LARGE$"):
        model.prepare(context, "问题")
    assert context == original
    client.chat_completion.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["output-limit", "allowlist", "extra", "context-hash"])
def test_package_role_model_modified_prepared_contract_never_dispatches(mutation):
    model, client, _, prepared = model_case()
    if mutation == "output-limit":
        prepared["params"]["max_tokens"] += 1
    elif mutation == "allowlist":
        prepared["allowed_refs"].append({"collection": "knowledge", "id": "b-keep"})
    elif mutation == "extra":
        prepared["PRIVATE_SENTINEL"] = "secret"
    else:
        prepared["context_hash"] = "0" * 64
    result = asyncio.run(model.call(prepared))
    assert result["error_code"] == "PACKAGE_ROLE_PREPARED_INVALID" and not result["model_attempted"]
    assert "PRIVATE_SENTINEL" not in str(result)
    client.chat_completion.assert_not_awaited()


@pytest.mark.parametrize("body", [
    '{"refs":[],"refs":[]}', '{"refs":[],"message":"PRIVATE_OUTPUT_SENTINEL"}',
    '{"refs":[{"collection":"knowledge","id":"b-keep"}]}',
    '{"refs":[{"collection":"evidence","id":"memory-b"}]}',
    '{"refs":[{"collection":"truth","id":"answer"}]}',
    '{"refs":[{"collection":"evidence","id":"b-card"},{"collection":"evidence","id":"b-card"}]}',
    '{"refs":null}', 'PRIVATE_MALFORMED_OUTPUT_SENTINEL',
])
def test_package_role_model_invalid_selection_keeps_known_usage_and_never_raw_text(body):
    model, client, _, prepared = model_case(result=response(content=body))
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "INVALID" and result["refs"] is None
    assert result["usage"]["completion_tokens"] == 20 and client.chat_completion.await_count == 1
    assert "PRIVATE_" not in str(result)


@pytest.mark.parametrize("patch,code", [
    ({"tool_calls": [ToolCall("PRIVATE_TOOL_SENTINEL", {})]}, "PACKAGE_ROLE_TOOLS_FORBIDDEN"),
    ({"reasoning_content": "PRIVATE_REASONING_SENTINEL"}, "PACKAGE_ROLE_REASONING_FORBIDDEN"),
    ({"finish_reason": "length"}, "PACKAGE_ROLE_OUTPUT_TRUNCATED"),
    ({"model": "PRIVATE_MODEL_SENTINEL"}, "PACKAGE_ROLE_MODEL_MISMATCH"),
    ({"usage": {"prompt_tokens": 100, "completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 3}}}, "PACKAGE_ROLE_REASONING_FORBIDDEN"),
    ({"usage": {"prompt_tokens": 100, "completion_tokens": 2000}}, "PACKAGE_ROLE_USAGE_EXCEEDS_RESERVATION"),
])
def test_package_role_model_rejected_provider_contract_still_accounts_known_usage(patch, code):
    model, client, _, prepared = model_case(result=response(**patch))
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "INVALID" and result["error_code"] == code and result["usage"] is not None
    assert result["refs"] is None and result["model_attempted"]
    assert "PRIVATE_" not in str(result) and client.chat_completion.await_count == 1


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 100, "completion_tokens": True},
                                    {"prompt_tokens": 100, "completion_tokens": 20, "cached_prompt_tokens": 101}])
def test_package_role_model_unknown_usage_never_becomes_zero_cost_success(usage):
    model, client, _, prepared = model_case(result=response(usage=usage))
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "UNKNOWN" and result["usage"] is None and result["refs"] is None
    assert result["model_attempted"] and client.chat_completion.await_count == 1


@pytest.mark.parametrize("error", [TimeoutError("PRIVATE_TIMEOUT_SENTINEL"), ConnectionError("PRIVATE_TRANSPORT_SENTINEL")])
def test_package_role_model_unknown_transport_has_no_automatic_retry(error):
    model, client, _, prepared = model_case()
    client.chat_completion.side_effect = error
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "UNKNOWN" and result["usage"] is None and result["model_attempted"]
    assert "PRIVATE_" not in str(result) and client.chat_completion.await_count == 1


def test_package_role_model_empty_selection_is_valid_without_free_text():
    model, client, _, prepared = model_case(result=response(content='{"refs":[]}'))
    result = asyncio.run(model.call(prepared))
    assert result["status"] == "OK" and result["refs"] == []


def test_package_role_model_known_tokens_are_priced_by_callers_frozen_policy():
    from decimal import Decimal
    model, _, _, prepared = model_case()
    result = asyncio.run(model.call(prepared))
    policy = BudgetPolicy(30000, Decimal("0.1"), Decimal("2"), Decimal("1"), Decimal("6"), True, "synthetic-rates")
    usage = result["usage"]
    priced = policy.amount(usage["prompt_tokens"], usage["completion_tokens"], usage["cached_prompt_tokens"], usage["reasoning_tokens"])
    assert priced.cost_cny == Decimal("0.00032")
    assert usage["cost_cny"] == "0"  # normalization is token accounting, not a provider price claim
