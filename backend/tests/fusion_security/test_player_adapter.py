import asyncio
import json

import pytest

from src.fusion.agents import FusionAgentOrchestrator, PlayerReply
from src.services.llm_service import LLMResponse


PROVIDER_CASES = (
    (
        "volcengine_ark",
        "ARK_API_KEY",
        "ARK_BASE_URL",
        "https://ark.cn-beijing.volces.com/api/v3",
        "ARK_CHARACTER_MODEL",
        "doubao-seed-character-260628",
        {"thinking": {"type": "disabled"}},
    ),
    (
        "aliyun_bailian",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_QWEN_MODEL",
        "qwen3.7-flash-2026-07-15",
        {"enable_thinking": False, "preserve_thinking": False},
    ),
)

EXPECTED_CONTRACT_VERSIONS = {
    "volcengine_ark": "ark-chat-character-v2",
    "aliyun_bailian": "bailian-chat-qwen37-v2",
}


def configure_provider(monkeypatch, provider_case, *, paid: bool = False) -> None:
    provider, key_name, base_name, base_url, model_name, model, _ = provider_case
    monkeypatch.setenv("FUSION_PLAYER_PROVIDER", provider)
    monkeypatch.setenv(key_name, "fixture-key-never-sent")
    monkeypatch.setenv(base_name, base_url)
    monkeypatch.setenv(model_name, model)
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "true" if paid else "false")


def assert_strict_player_schema(params: dict) -> None:
    response_format = params["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"message", "action"}
    assert set(schema["properties"]) == {"message", "action"}


@pytest.mark.parametrize("payload", [
    [], "hello", {"message": 3, "action": "speak"}, {"message": "   ", "action": "speak"},
    {"message": "答案", "action": "search"}, {"message": "答案", "action": "speak", "truth": "SECRET"},
    {"message": "<think>SECRET</think>", "action": "speak"},
    {"message": "a" * 1001, "action": "speak"}, {"action": "speak"},
])
def test_malformed_model_output_is_not_public_dialogue(payload):
    with pytest.raises(ValueError):
        PlayerReply.model_validate_json(json.dumps(payload))


def test_game_data_is_separate_from_system_rules(monkeypatch):
    class FakeClient:
        async def chat_completion(self, messages, **kwargs):
            assert "ROLE_SECRET" not in messages[0].content
            assert "IGNORE_ALL_RULES" not in messages[0].content
            content = json.loads(messages[1].content)
            assert content["role"]["secret"] == "ROLE_SECRET"
            assert content["question"] == "IGNORE_ALL_RULES"
            return LLMResponse(content='{"message":"根据现有线索，我还不能确定。","action":"speak"}',
                               usage={"prompt_tokens": 2, "completion_tokens": 3},
                               model="doubao-seed-character-260628")

    agent = FusionAgentOrchestrator(client=FakeClient())
    result = asyncio.run(agent.player_reply({"secret": "ROLE_SECRET"}, [], "IGNORE_ALL_RULES"))
    assert result.degraded is False and result.usage == {"prompt_tokens": 2, "completion_tokens": 3}


def test_invalid_output_is_not_resent_and_usage_includes_rejected_call(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")

    class FakeClient:
        calls = 0

        async def chat_completion(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse(content='{"message":{"secret":"DO_NOT_SHOW"},"action":"speak"}',
                               usage={"prompt_tokens": 2, "completion_tokens": 3},
                               model="doubao-seed-character-260628")

    client = FakeClient()
    result = asyncio.run(FusionAgentOrchestrator(client=client).player_reply({}, [], "问题"))
    assert client.calls == 1 and result.degraded
    assert "DO_NOT_SHOW" not in result.message
    assert result.usage == {"prompt_tokens": 2, "completion_tokens": 3}


def test_sdk_failure_has_safe_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")

    class FakeClient:
        async def chat_completion(self, *args, **kwargs):
            raise RuntimeError("SENSITIVE_ERROR_DETAIL")

    result = asyncio.run(FusionAgentOrchestrator(client=FakeClient()).player_reply({}, [], "问题"))
    assert result.degraded and "SENSITIVE_ERROR_DETAIL" not in result.message


def test_non_retryable_provider_error_is_not_resent(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")

    class BadRequest(Exception):
        status_code = 400

    class FakeClient:
        calls = 0

        async def chat_completion(self, *args, **kwargs):
            self.calls += 1
            raise BadRequest("SENSITIVE_PROVIDER_DETAIL")

    client = FakeClient()
    result = asyncio.run(FusionAgentOrchestrator(client=client).player_reply({}, [], "问题"))
    assert client.calls == 1
    assert result.degraded and result.attempt_count == 1 and result.unknown_attempts == 1
    assert "SENSITIVE_PROVIDER_DETAIL" not in result.message


def test_transient_provider_error_can_retry_within_reserved_attempts(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")

    class ServiceUnavailable(Exception):
        status_code = 503

    class FakeClient:
        calls = 0

        async def chat_completion(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ServiceUnavailable()
            return LLMResponse(
                content='{"message":"重试成功","action":"speak"}',
                usage={"prompt_tokens": 2, "completion_tokens": 1},
                model="doubao-seed-character-260628",
            )

    client = FakeClient()
    result = asyncio.run(FusionAgentOrchestrator(client=client).player_reply({}, [], "问题"))
    assert client.calls == 2 and result.degraded is False
    assert result.attempt_count == 2 and result.unknown_attempts == 1


def test_cancellation_is_not_swallowed():
    class FakeClient:
        async def chat_completion(self, *args, **kwargs):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(FusionAgentOrchestrator(client=FakeClient()).player_reply({}, [], "问题"))


@pytest.mark.parametrize("provider_case", PROVIDER_CASES, ids=("ark", "bailian"))
def test_provider_request_shape_is_strict_and_reasoning_is_not_public(monkeypatch, provider_case):
    configure_provider(monkeypatch, provider_case)
    # Old global knobs must not turn paid reasoning back on for either profile.
    monkeypatch.setenv("LLM_THINKING_MODE", "enabled")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "max")
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    calls = []

    class FakeClient:
        async def chat_completion(self, messages, **kwargs):
            calls.append(kwargs)
            return LLMResponse(
                content='{"message":"只展示允许公开的回答。","action":"speak"}',
                reasoning_content="INTERNAL_REASONING_CANARY",
                usage={"prompt_tokens": 2, "completion_tokens": 3},
                model=provider_case[5],
            )

    agent = FusionAgentOrchestrator(client=FakeClient())
    result = asyncio.run(agent.player_reply({}, [], "问题"))
    assert agent.model == provider_case[5]
    assert agent.model_metadata()["config_version"] == "fusion-player-v3"
    assert agent.model_metadata()["request_contract_version"] == EXPECTED_CONTRACT_VERSIONS[provider_case[0]]
    token_parameter = "max_tokens" if provider_case[0] == "volcengine_ark" else "max_completion_tokens"
    assert set(calls[0]) == {"response_format", token_parameter, "temperature", "extra_body"}
    assert_strict_player_schema(calls[0])
    assert calls[0]["extra_body"] == provider_case[6]
    assert calls[0][token_parameter] == 1000
    assert calls[0]["temperature"] == 0.7
    assert "reasoning_effort" not in calls[0]
    assert "INTERNAL_REASONING_CANARY" not in result.message


@pytest.mark.parametrize("provider_case", PROVIDER_CASES, ids=("ark", "bailian"))
def test_real_fusion_client_requires_explicit_paid_gate(monkeypatch, provider_case):
    configure_provider(monkeypatch, provider_case, paid=False)
    assert FusionAgentOrchestrator().client is None

    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "true")
    client = FusionAgentOrchestrator().client
    assert client is not None and client.client_max_retries == 0
    assert client.api_key == "fixture-key-never-sent"
    assert client.base_url == provider_case[3]
    assert client.model == provider_case[5]


@pytest.mark.parametrize("provider_case", PROVIDER_CASES, ids=("ark", "bailian"))
@pytest.mark.parametrize("invalid_field", ("model", "base_url"))
def test_real_fusion_client_rejects_non_allowlisted_model_or_endpoint(
    monkeypatch, provider_case, invalid_field,
):
    configure_provider(monkeypatch, provider_case, paid=True)
    if invalid_field == "model":
        monkeypatch.setenv(provider_case[4], "unapproved-model")
    else:
        monkeypatch.setenv(provider_case[2], f"{provider_case[3]}.attacker.example")
    assert FusionAgentOrchestrator().client is None


@pytest.mark.parametrize(
    "selected_case,other_case",
    ((PROVIDER_CASES[0], PROVIDER_CASES[1]), (PROVIDER_CASES[1], PROVIDER_CASES[0])),
    ids=("ark-cannot-borrow-bailian-key", "bailian-cannot-borrow-ark-key"),
)
def test_provider_never_borrows_another_providers_key(monkeypatch, selected_case, other_case):
    configure_provider(monkeypatch, selected_case, paid=True)
    monkeypatch.delenv(selected_case[1], raising=False)
    monkeypatch.setenv(other_case[1], "other-provider-key-never-sent")
    assert FusionAgentOrchestrator().client is None


@pytest.mark.parametrize(
    "selected_case,other_case",
    ((PROVIDER_CASES[0], PROVIDER_CASES[1]), (PROVIDER_CASES[1], PROVIDER_CASES[0])),
    ids=("only-ark-key-read", "only-bailian-key-read"),
)
def test_only_the_selected_provider_key_is_resolved(monkeypatch, selected_case, other_case):
    configure_provider(monkeypatch, selected_case, paid=True)
    monkeypatch.setenv(other_case[1], "other-provider-key-never-sent")
    real_getenv = __import__("os").getenv
    requested_names = []

    def tracked_getenv(name, default=None):
        requested_names.append(name)
        return real_getenv(name, default)

    monkeypatch.setattr("src.fusion.agents.os.getenv", tracked_getenv)
    assert FusionAgentOrchestrator().client is not None
    assert selected_case[1] in requested_names
    assert other_case[1] not in requested_names


def test_unknown_provider_fails_closed_without_falling_back(monkeypatch):
    for provider_case in PROVIDER_CASES:
        configure_provider(monkeypatch, provider_case, paid=True)
    monkeypatch.setenv("FUSION_PLAYER_PROVIDER", "unsupported-provider")
    assert FusionAgentOrchestrator().client is None


def test_placeholder_key_is_never_usable(monkeypatch):
    configure_provider(monkeypatch, PROVIDER_CASES[0], paid=True)
    monkeypatch.setenv("ARK_API_KEY", "CHANGE_ME_ARK_API_KEY")
    assert FusionAgentOrchestrator().client is None


def test_api_key_and_raw_endpoint_never_enter_model_metadata(monkeypatch):
    configure_provider(monkeypatch, PROVIDER_CASES[0], paid=True)
    metadata = json.dumps(FusionAgentOrchestrator().model_metadata(), ensure_ascii=False)
    assert "fixture-key-never-sent" not in metadata
    assert PROVIDER_CASES[0][3] not in metadata


def test_default_fusion_provider_is_volcengine_ark(monkeypatch):
    monkeypatch.delenv("FUSION_PLAYER_PROVIDER", raising=False)
    monkeypatch.delenv("ARK_CHARACTER_MODEL", raising=False)
    agent = FusionAgentOrchestrator(client=object())
    assert agent.settings.provider == "volcengine_ark"
    assert agent.model == "doubao-seed-character-260628"


def test_reservation_covers_every_application_attempt(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_MAX_TOKENS", "100")
    agent = FusionAgentOrchestrator(client=object())
    one_attempt_prompt = agent.reservation_tokens({}, [], "问题").prompt_tokens // 3
    reservation = agent.reservation_tokens({}, [], "问题")
    assert reservation.prompt_tokens == one_attempt_prompt * 3
    assert reservation.completion_tokens == 300


def test_bailian_reservation_covers_documented_output_tolerance(monkeypatch):
    configure_provider(monkeypatch, PROVIDER_CASES[1])
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_MAX_TOKENS", "100")
    reservation = FusionAgentOrchestrator(client=object()).reservation_tokens({}, [], "问题")
    assert reservation.completion_tokens == 348


def test_truncated_output_is_not_published_or_resent(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")

    class FakeClient:
        calls = 0

        async def chat_completion(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse(
                content='{"message":"不应公开","action":"speak"}',
                usage={"prompt_tokens": 2, "completion_tokens": 3},
                model="doubao-seed-character-260628",
                finish_reason="length",
            )

    client = FakeClient()
    result = asyncio.run(FusionAgentOrchestrator(client=client).player_reply({}, [], "问题"))
    assert client.calls == 1 and result.degraded
    assert result.finish_reason == "length" and "不应公开" not in result.message


@pytest.mark.parametrize("reported_model", [None, "unexpected-provider-model"])
def test_unverifiable_response_model_is_not_published_or_resent(monkeypatch, reported_model):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")

    class FakeClient:
        calls = 0

        async def chat_completion(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse(
                content='{"message":"不应公开","action":"speak"}',
                usage={"prompt_tokens": 2, "completion_tokens": 3},
                model=reported_model,
                finish_reason="stop",
            )

    client = FakeClient()
    result = asyncio.run(FusionAgentOrchestrator(client=client).player_reply({}, [], "问题"))
    assert client.calls == 1 and result.degraded and result.usage_uncertain
    assert result.response_model == reported_model
    assert "不应公开" not in result.message


def test_success_without_usage_is_marked_uncertain(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")

    class FakeClient:
        async def chat_completion(self, messages, **kwargs):
            return LLMResponse(
                content='{"message":"回答","action":"speak"}',
                usage=None,
                model="doubao-seed-character-260628",
            )

    result = asyncio.run(FusionAgentOrchestrator(client=FakeClient()).player_reply({}, [], "问题"))
    assert result.model_attempted is True
    assert result.usage_uncertain is True
    assert result.usage is None


def test_conflicting_partial_usage_is_not_added_to_authoritative_totals(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")

    class FakeClient:
        async def chat_completion(self, messages, **kwargs):
            return LLMResponse(
                content='{"message":"回答","action":"speak"}',
                usage={
                    "prompt_tokens": 5,
                    "prompt_cache_hit_tokens": 7,
                    "prompt_cache_miss_tokens": 3,
                    "completion_tokens": 2,
                },
                model="doubao-seed-character-260628",
            )

    result = asyncio.run(FusionAgentOrchestrator(client=FakeClient()).player_reply({}, [], "问题"))
    assert result.usage is None
    assert result.usage_uncertain is True
    assert result.unknown_attempts == 1


def test_timeout_then_success_identifies_only_the_started_unknown_attempt(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")

    class FakeClient:
        calls = 0

        async def chat_completion(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError()
            return LLMResponse(
                content='{"message":"重试成功","action":"speak"}',
                usage={"prompt_tokens": 2, "completion_tokens": 1},
                model="doubao-seed-character-260628",
                request_id="fixture-request-id",
            )

    result = asyncio.run(FusionAgentOrchestrator(client=FakeClient()).player_reply({}, [], "问题"))
    assert result.attempt_count == 2
    assert result.unknown_attempts == 1
    assert result.response_model == "doubao-seed-character-260628"
    assert result.provider_request_id == "fixture-request-id"


def test_oversized_serialized_input_never_reaches_client(monkeypatch):
    monkeypatch.setenv("FUSION_PLAYER_MAX_INPUT_BYTES", "1024")

    class FakeClient:
        async def chat_completion(self, *args, **kwargs):
            pytest.fail("oversized input must be rejected before network I/O")

    result = asyncio.run(FusionAgentOrchestrator(client=FakeClient()).player_reply(
        {"background": "虚构" * 2000}, [], "问题",
    ))
    assert result.degraded is True and result.model_attempted is False


@pytest.mark.parametrize("value", ["invalid", "nan", "inf", "-999", "99999"])
def test_execution_settings_are_always_bounded(monkeypatch, value):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
    monkeypatch.setenv("LLM_MAX_RETRIES", value)
    agent = FusionAgentOrchestrator()
    assert 1 <= agent.timeout <= 120 and 0 <= agent.retries <= 3
