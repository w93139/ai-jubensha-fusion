import asyncio
from decimal import Decimal
import json
from pathlib import Path

import pytest

from src.fusion.provider_smoke import (
    HARD_MAX_CONFIRMED_COST_CNY,
    SMOKE_FIXTURE_ID,
    SmokeConfigurationError,
    build_parser,
    execute_smoke,
    load_selected_config,
    parse_confirmed_cost,
    read_selected_dotenv,
    write_sanitized_receipt,
)
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.services.llm_service import LLMResponse


def _dotenv(provider: str, *, key: str = "fixture-selected-secret") -> str:
    profile = PLAYER_PROVIDER_PROFILES[provider]
    prefix = profile.pricing_env_prefix
    other_key = (
        "DASHSCOPE_API_KEY" if profile.api_key_env == "ARK_API_KEY" else "ARK_API_KEY"
    )
    return "\n".join((
        f"{profile.api_key_env}={key}",
        f"{profile.base_url_env}={profile.default_base_url}",
        f"{profile.model_env}={profile.default_model}",
        f"{prefix}_INPUT_COST_PER_MILLION=0.8",
        f"{prefix}_CACHED_INPUT_COST_PER_MILLION=0.16",
        f"{prefix}_OUTPUT_COST_PER_MILLION=2",
        f"{prefix}_PRICING_VERSION=fixture-price-v1",
        f"{other_key}=fixture-other-provider-secret",
        "COMMERCIAL_SCRIPT_TEXT=must-not-be-read",
        "",
    ))


def _config(tmp_path: Path, provider: str = "volcengine_ark"):
    env_path = tmp_path / ".env"
    env_path.write_text(_dotenv(provider), encoding="utf-8")
    return load_selected_config(provider, env_path)


def test_cli_has_no_default_provider_and_requires_both_paid_confirmations():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--provider", "volcengine_ark", "--max-cost-cny", "0.01",
        ])


@pytest.mark.parametrize("raw", ["0", "-1", "0.010001", "NaN", "Infinity", "oops"])
def test_confirmed_cost_must_be_positive_and_at_most_one_cent(raw):
    with pytest.raises(SmokeConfigurationError):
        parse_confirmed_cost(raw)
    assert HARD_MAX_CONFIRMED_COST_CNY == Decimal("0.01")


def test_direct_execute_call_cannot_bypass_one_cent_limit(tmp_path):
    config = _config(tmp_path)

    with pytest.raises(SmokeConfigurationError) as caught:
        asyncio.run(execute_smoke(config, Decimal("0.02")))

    assert caught.value.code == "CONFIRMED_COST_OUT_OF_RANGE"


def test_dotenv_discards_other_provider_key_and_unrelated_text(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(_dotenv("volcengine_ark"), encoding="utf-8")
    profile = PLAYER_PROVIDER_PROFILES["volcengine_ark"]

    selected = read_selected_dotenv(env_path, profile)

    assert selected["ARK_API_KEY"] == "fixture-selected-secret"
    assert "DASHSCOPE_API_KEY" not in selected
    assert "COMMERCIAL_SCRIPT_TEXT" not in selected


@pytest.mark.parametrize("field,value,code", [
    ("ARK_BASE_URL", "https://example.invalid/v1", "BASE_URL_NOT_ALLOWLISTED"),
    ("ARK_CHARACTER_MODEL", "latest-alias", "MODEL_NOT_ALLOWLISTED"),
])
def test_non_allowlisted_endpoint_or_model_is_refused_before_client_creation(
    tmp_path, field, value, code,
):
    env_path = tmp_path / ".env"
    env_path.write_text(_dotenv("volcengine_ark") + f"{field}={value}\n", encoding="utf-8")
    # Duplicate selected settings are also refused rather than using last-wins.
    with pytest.raises(SmokeConfigurationError) as caught:
        load_selected_config("volcengine_ark", env_path)
    assert caught.value.code == "DUPLICATE_SELECTED_SETTING"

    clean_lines = [line for line in _dotenv("volcengine_ark").splitlines()
                   if not line.startswith(field + "=")]
    env_path.write_text("\n".join(clean_lines + [f"{field}={value}", ""]), encoding="utf-8")
    with pytest.raises(SmokeConfigurationError) as caught:
        load_selected_config("volcengine_ark", env_path)
    assert caught.value.code == code


@pytest.mark.parametrize("provider", ["volcengine_ark", "aliyun_bailian"])
def test_success_is_one_call_and_receipt_never_contains_prompt_response_or_keys(tmp_path, provider):
    config = _config(tmp_path, provider)
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def chat_completion(self, messages, **kwargs):
            calls.append(("call", messages, kwargs))
            return LLMResponse(
                content='{"message":"我只看到一只没有署名的纸鹤。","action":"speak"}',
                usage={"prompt_tokens": 80, "completion_tokens": 20},
                model=config.model,
                request_id="provider-request-secretish-id",
                finish_reason="stop",
            )

    code, receipt = asyncio.run(execute_smoke(config, Decimal("0.01"), FakeClient))

    assert code == 0 and receipt["status"] == "passed"
    assert [item[0] for item in calls].count("call") == 1
    assert calls[0][1]["client_max_retries"] == 0
    assert calls[1][1][1].content.find(SMOKE_FIXTURE_ID) >= 0
    serialized = json.dumps(receipt, ensure_ascii=False)
    for forbidden in (
        config.api_key,
        "provider-request-secretish-id",
        "我只看到一只没有署名的纸鹤",
        "社区图书角志愿者",
    ):
        assert forbidden not in serialized
    assert str(receipt["provider_request_fingerprint"]).startswith("sha256:")
    assert Decimal(str(receipt["estimated_cost_cny"])) > 0
    assert receipt["confirmed_local_estimate_limit_cny"] == "0.01"
    assert receipt["cost_basis"] == "configured_requested_model_rates"
    assert isinstance(receipt["duration_ms"], int) and receipt["duration_ms"] >= 0


def test_provider_failure_is_not_retried_and_exception_detail_is_not_recorded(tmp_path):
    config = _config(tmp_path)

    class FakeClient:
        calls = 0

        def __init__(self, **kwargs):
            pass

        async def chat_completion(self, messages, **kwargs):
            self.calls += 1
            raise RuntimeError("LEAK_ME_NEVER")

    client_holder = FakeClient()

    def factory(**kwargs):
        return client_holder

    code, receipt = asyncio.run(execute_smoke(config, Decimal("0.01"), factory))

    assert code == 3 and client_holder.calls == 1
    assert receipt["result_code"] == "PROVIDER_CALL_FAILED_USAGE_UNKNOWN"
    assert "LEAK_ME_NEVER" not in json.dumps(receipt)
    assert receipt["usage_source"] == "conservative_reservation"
    assert isinstance(receipt["duration_ms"], int)


def test_provider_http_status_is_allowlisted_without_copying_error_text(tmp_path):
    config = _config(tmp_path)

    class SafeFakeProviderError(Exception):
        status_code = 401

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def chat_completion(self, messages, **kwargs):
            raise SafeFakeProviderError("SECRET_PROVIDER_BODY")

    code, receipt = asyncio.run(execute_smoke(config, Decimal("0.01"), FakeClient))

    assert code == 3
    assert receipt["result_code"] == "PROVIDER_AUTHENTICATION_FAILED_USAGE_UNKNOWN"
    assert receipt["provider_http_status"] == 401
    assert "SECRET_PROVIDER_BODY" not in json.dumps(receipt)


def test_missing_usage_fails_closed_with_conservative_cost(tmp_path):
    config = _config(tmp_path)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def chat_completion(self, messages, **kwargs):
            return LLMResponse(
                content='{"message":"纸鹤没有署名。","action":"speak"}',
                usage=None,
                model=config.model,
                finish_reason="stop",
            )

    code, receipt = asyncio.run(execute_smoke(config, Decimal("0.01"), FakeClient))

    assert code == 3
    assert receipt["result_code"] == "USAGE_MISSING_OR_INVALID"
    assert receipt["usage_source"] == "conservative_reservation"


def test_provider_controlled_finish_reason_is_not_copied_to_receipt(tmp_path):
    config = _config(tmp_path)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def chat_completion(self, messages, **kwargs):
            return LLMResponse(
                content='{"message":"纸鹤没有署名。","action":"speak"}',
                usage={"prompt_tokens": 80, "completion_tokens": 20},
                model=config.model,
                finish_reason="MALICIOUS_SECRET_TEXT",
            )

    code, receipt = asyncio.run(execute_smoke(config, Decimal("0.01"), FakeClient))

    assert code == 3 and receipt["finish_reason"] == "other"
    assert "MALICIOUS_SECRET_TEXT" not in json.dumps(receipt)


def test_sanitized_receipt_is_owner_only(tmp_path):
    receipt = {"provider": "volcengine_ark", "status": "passed", "usage": {"prompt_tokens": 1}}
    path = write_sanitized_receipt(receipt, tmp_path / "receipts")

    assert json.loads(path.read_text(encoding="utf-8")) == receipt
    assert path.stat().st_mode & 0o077 == 0
