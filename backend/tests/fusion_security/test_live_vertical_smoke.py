import asyncio
from decimal import Decimal
import json
import os

import pytest

from scripts.test_fusion_postgres_budget import (
    CONFIRM_ENV,
    REQUIRED_CONFIRMATION,
    TEST_DATABASE_ENV,
    validate_test_database_environment,
)
from src.fusion.budget import BudgetPolicy
from src.fusion.live_vertical_smoke import (
    AUTHORIZED_PUBLIC_MARKER,
    AUTHORIZED_ROLE_MARKER,
    FORBIDDEN_OTHER_ROLE_MARKER,
    FORBIDDEN_SYSTEM_MARKER,
    OneShotProjectionClient,
    VerticalSmokeSafetyError,
    build_base_receipt,
    build_parser,
    execute_vertical_smoke,
    main,
    selected_runtime_environment,
)
from src.fusion.provider_smoke import SelectedSmokeConfig, SmokeConfigurationError
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.services.llm_service import LLMMessage, LLMResponse


SAFE_URL = "postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox"


def config(provider: str = "aliyun_bailian") -> SelectedSmokeConfig:
    profile = PLAYER_PROVIDER_PROFILES[provider]
    return SelectedSmokeConfig(
        profile=profile,
        api_key="synthetic-test-key-never-print",
        base_url=profile.default_base_url,
        model=profile.default_model,
        pricing=BudgetPolicy(
            token_limit=10_000,
            cost_limit_cny=Decimal("0.01"),
            input_rate_cny=Decimal("0.2"),
            cached_input_rate_cny=Decimal("0.04"),
            output_rate_cny=Decimal("0.8"),
            paid_calls_enabled=True,
            pricing_version="synthetic-price-v1",
        ),
    )


def target():
    return validate_test_database_environment({
        TEST_DATABASE_ENV: SAFE_URL,
        CONFIRM_ENV: REQUIRED_CONFIRMATION,
    })


def authorized_messages() -> list[LLMMessage]:
    return [LLMMessage(
        "user",
        f"{AUTHORIZED_ROLE_MARKER} {AUTHORIZED_PUBLIC_MARKER}",
    )]


def test_cli_has_no_prompt_file_session_or_output_injection_options() -> None:
    parser = build_parser()
    destinations = {action.dest for action in parser._actions}

    assert destinations == {"help", "provider", "max_cost_cny", "confirm_one_paid_call"}
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--provider", "aliyun_bailian",
            "--max-cost-cny", "0.01",
        ])


def test_unsafe_database_refusal_happens_before_dotenv_or_network(monkeypatch, capsys) -> None:
    monkeypatch.delenv(TEST_DATABASE_ENV, raising=False)
    monkeypatch.delenv(CONFIRM_ENV, raising=False)
    dotenv_called = False

    def forbidden_config_load(provider: str):
        nonlocal dotenv_called
        dotenv_called = True
        raise AssertionError(provider)

    monkeypatch.setattr("src.fusion.live_vertical_smoke.load_selected_config", forbidden_config_load)
    code = main([
        "--provider", "aliyun_bailian",
        "--max-cost-cny", "0.01",
        "--confirm-one-paid-call",
    ])

    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert dotenv_called is False
    assert output == {
        "request_count": 0,
        "result_code": "UNSAFE_DATABASE_TARGET",
        "status": "refused_before_network",
    }


def test_runtime_environment_is_one_provider_zero_retry_and_restored(monkeypatch) -> None:
    selected = config()
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "false")
    monkeypatch.setenv("ARK_API_KEY", "existing-ark-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "existing-bailian-key")
    before = dict(os.environ)

    with selected_runtime_environment(selected, Decimal("0.01")):
        assert os.environ["ENABLE_PAID_MODEL_CALLS"] == "true"
        assert os.environ["FUSION_PLAYER_PROVIDER"] == "aliyun_bailian"
        assert os.environ["LLM_MAX_RETRIES"] == "0"
        assert os.environ["LLM_MAX_TOKENS"] == "256"
        assert os.environ["LLM_TEMPERATURE"] == "0"
        assert os.environ["GAME_COST_BUDGET_CNY"] == "0.01"
        assert "ARK_API_KEY" not in os.environ
        assert "DASHSCOPE_API_KEY" not in os.environ

    assert dict(os.environ) == before

    with pytest.raises(SmokeConfigurationError):
        with selected_runtime_environment(selected, Decimal("0.02")):
            raise AssertionError("unreachable")


def test_projection_guard_refuses_private_canaries_before_inner_call() -> None:
    class FakeInner:
        calls = 0

        async def chat_completion(self, messages, **kwargs):
            self.calls += 1
            raise AssertionError("must stay offline")

    inner = FakeInner()
    guarded = OneShotProjectionClient(inner)  # type: ignore[arg-type]

    with pytest.raises(VerticalSmokeSafetyError):
        asyncio.run(guarded.chat_completion([
            LLMMessage(
                "user",
                f"{AUTHORIZED_ROLE_MARKER} {AUTHORIZED_PUBLIC_MARKER} {FORBIDDEN_SYSTEM_MARKER}",
            ),
        ]))

    assert guarded.network_call_count == 0
    assert guarded.projection_checked is False
    assert inner.calls == 0


def test_projection_guard_allows_exactly_one_inner_call_and_tracks_reasoning() -> None:
    class FakeInner:
        calls = 0

        async def chat_completion(self, messages, **kwargs):
            self.calls += 1
            return LLMResponse(
                content='{"message":"虚构回答","action":"speak"}',
                usage={"prompt_tokens": 10, "completion_tokens": 5},
                model="qwen3.7-flash-2026-07-15",
                reasoning_content=None,
                finish_reason="stop",
            )

    inner = FakeInner()
    guarded = OneShotProjectionClient(inner)  # type: ignore[arg-type]
    response = asyncio.run(guarded.chat_completion(authorized_messages()))

    assert response.finish_reason == "stop"
    assert guarded.projection_checked is True
    assert guarded.network_call_count == 1
    assert guarded.reasoning_detected is False
    with pytest.raises(VerticalSmokeSafetyError):
        asyncio.run(guarded.chat_completion(authorized_messages()))
    assert inner.calls == 1


def test_direct_execution_cannot_bypass_one_cent_limit() -> None:
    with pytest.raises(SmokeConfigurationError) as caught:
        asyncio.run(execute_vertical_smoke(config(), target(), Decimal("0.02")))

    assert caught.value.code == "CONFIRMED_COST_OUT_OF_RANGE"


def test_base_receipt_is_content_free_and_uses_safe_database_label() -> None:
    selected = config()
    receipt = build_base_receipt(selected, target(), Decimal("0.01"))
    serialized = json.dumps(receipt, ensure_ascii=False)

    assert receipt["database_target"] == "127.0.0.1:55432/fusion_pg_it_sandbox"
    assert receipt["request_count"] == 0
    assert receipt["sdk_retries"] == 0
    for forbidden in (
        selected.api_key,
        AUTHORIZED_ROLE_MARKER,
        AUTHORIZED_PUBLIC_MARKER,
        FORBIDDEN_SYSTEM_MARKER,
        FORBIDDEN_OTHER_ROLE_MARKER,
    ):
        assert forbidden not in serialized
