"""Fusion 角色模型适配层；只产出候选发言/行动，不写游戏状态。"""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.fusion.budget import UsageAmount, env_flag, estimate_text_tokens, normalize_provider_usage
from src.fusion.providers import DEFAULT_PLAYER_PROVIDER, PlayerProviderProfile, get_player_provider_profile
from src.services.llm_service import BaseLLMService, LLMMessage, OpenAILLMService


PLAYER_PROMPT = (Path(__file__).parent / "prompts" / "player.md").read_text(encoding="utf-8")
PLAYER_PROMPT_VERSION = f"sha256:{sha256(PLAYER_PROMPT.encode()).hexdigest()}"
MODEL_CONFIG_VERSION = "fusion-player-v3"
RETRYABLE_PROVIDER_STATUS_CODES = {429, 500, 502, 503, 504}


def bounded_setting(name: str, default: float, minimum: float, maximum: float) -> float:
    """无效配置回到有界默认值，不在已领取任务后因配置解析崩溃。"""
    try:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value):
            value = default
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def provider_error_is_retryable(error: Exception) -> bool:
    """Retry only transport failures and transient provider status codes."""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code in RETRYABLE_PROVIDER_STATUS_CODES
    return isinstance(error, (TimeoutError, ConnectionError)) or error.__class__.__name__ in {
        "APIConnectionError", "APITimeoutError",
    }


@dataclass(frozen=True)
class PlayerModelSettings:
    """Validated settings for the real-time character dialogue task."""

    provider: str
    model: str
    timeout_seconds: float
    retries: int
    max_output_tokens: int
    max_input_bytes: int
    thinking_mode: Literal["disabled"]
    temperature: float
    paid_calls_enabled: bool

    @classmethod
    def from_env(cls) -> "PlayerModelSettings":
        raw_provider = os.getenv("FUSION_PLAYER_PROVIDER")
        provider = DEFAULT_PLAYER_PROVIDER if raw_provider is None else raw_provider.strip().lower()
        profile = get_player_provider_profile(provider)
        if profile is None:
            model = ""
        else:
            raw_model = os.getenv(profile.model_env)
            model = profile.default_model if raw_model is None else raw_model.strip()
        return cls(
            provider=provider,
            model=model,
            timeout_seconds=bounded_setting("LLM_TIMEOUT_SECONDS", 25, 1, 120),
            retries=int(bounded_setting("LLM_MAX_RETRIES", 2, 0, 3)),
            max_output_tokens=int(bounded_setting("LLM_MAX_TOKENS", 1000, 64, 4096)),
            # The selected prices are the <=32K tier.  A byte upper bound plus
            # the reservation envelope keeps every request safely in that tier.
            max_input_bytes=int(bounded_setting("FUSION_PLAYER_MAX_INPUT_BYTES", 20_000, 1024, 24_000)),
            thinking_mode="disabled",
            temperature=bounded_setting("LLM_TEMPERATURE", 0.7, 0, 2),
            paid_calls_enabled=env_flag("ENABLE_PAID_MODEL_CALLS", False),
        )


class PlayerReply(BaseModel):
    """确定性输出契约，不将任意对象/数组强制转换成公开台词。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=1000)
    action: Literal["speak"]

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value or "<think" in value.lower() or "</think" in value.lower():
            raise ValueError("公开发言为空或包含内部思考标签")
        return value


@dataclass
class AgentTurn:
    message: str
    action: str = "speak"
    usage: dict | None = None
    degraded: bool = False
    usage_uncertain: bool = False
    model_attempted: bool = False
    attempt_count: int = 0
    response_model: str | None = None
    unknown_attempts: int = 0
    provider_request_id: str | None = None
    finish_reason: str | None = None


class FusionAgentOrchestrator:
    """角色发言适配器；格式检查不是语义 Audit，也不是四个独立 Agent。"""

    def __init__(self, client: BaseLLMService | None = None):
        self.settings = PlayerModelSettings.from_env()
        self.profile: PlayerProviderProfile | None = get_player_provider_profile(self.settings.provider)
        if self.profile is None:
            base_url = ""
        else:
            raw_base_url = os.getenv(self.profile.base_url_env)
            base_url = (self.profile.default_base_url if raw_base_url is None else raw_base_url.strip()).rstrip("/")
        self.endpoint_fingerprint = sha256(base_url.encode()).hexdigest() if base_url else None
        self.model = self.settings.model
        self.timeout = self.settings.timeout_seconds
        self.retries = self.settings.retries
        self.client = client
        profile = self.profile
        if client is None and profile is not None and self._profile_is_callable(base_url):
            # Read exactly one key, and only when all non-secret gates are valid.
            api_key = os.getenv(profile.api_key_env, "").strip()
            usable_key = bool(api_key and not api_key.upper().startswith("CHANGE_ME"))
            if usable_key:
                self.client = OpenAILLMService(
                    api_key=api_key,
                    base_url=base_url,
                    model=self.model,
                    # The application owns the bounded retry loop and reservation.
                    client_max_retries=0,
                )

    def _profile_is_callable(self, base_url: str) -> bool:
        return bool(
            self.profile
            and self.settings.paid_calls_enabled
            and self.settings.model in self.profile.allowed_models
            and base_url in self.profile.allowed_base_urls
        )

    def model_metadata(self) -> dict[str, str | int | float | None]:
        return {
            "config_version": MODEL_CONFIG_VERSION,
            "provider": self.settings.provider,
            "model": self.settings.model,
            "endpoint_fingerprint": self.endpoint_fingerprint,
            "prompt_version": PLAYER_PROMPT_VERSION,
            "thinking_mode": self.settings.thinking_mode,
            "request_contract_version": self.profile.request_contract_version if self.profile else None,
            "temperature": self.settings.temperature,
            "max_output_tokens": self.settings.max_output_tokens,
            "max_input_bytes": self.settings.max_input_bytes,
            "timeout_seconds": self.settings.timeout_seconds,
            "max_attempts": self.settings.retries + 1,
        }

    @staticmethod
    def _messages(role_context: dict, visible_events: list[dict], question: str) -> list[LLMMessage]:
        return [
            LLMMessage("system", PLAYER_PROMPT),
            LLMMessage("user", json.dumps({
                "role": role_context,
                "visible_events": visible_events[-30:],
                "question": question,
            }, ensure_ascii=False)),
        ]

    def reservation_tokens(self, role_context: dict, visible_events: list[dict], question: str) -> UsageAmount:
        """Reserve every configured attempt before the first provider request."""
        messages = self._messages(role_context, visible_events, question)
        # The exact provider tokenizer can change slightly.  Reserve one token per
        # UTF-8 byte plus a large fixed chat-template/special-token envelope.
        per_attempt_prompt = sum(estimate_text_tokens(item.content) for item in messages) + 4096
        max_attempts = self.settings.retries + 1
        per_attempt_completion = (
            self.profile.reserved_completion_tokens(self.settings.max_output_tokens)
            if self.profile else self.settings.max_output_tokens
        )
        return UsageAmount(
            prompt_tokens=per_attempt_prompt * max_attempts,
            completion_tokens=per_attempt_completion * max_attempts,
        )

    def _call_params(self) -> dict:
        if self.profile is None:
            raise ValueError("unsupported Fusion player provider")
        return self.profile.request_params(self.settings.max_output_tokens, self.settings.temperature)

    @staticmethod
    def _usage_payload(amount: UsageAmount, seen: bool) -> dict | None:
        if not seen:
            return None
        payload: dict[str, object] = {
            "prompt_tokens": amount.prompt_tokens,
            "completion_tokens": amount.completion_tokens,
        }
        if amount.cached_prompt_tokens:
            payload["cached_prompt_tokens"] = amount.cached_prompt_tokens
        if amount.reasoning_tokens:
            payload["completion_tokens_details"] = {"reasoning_tokens": amount.reasoning_tokens}
        return payload

    async def player_reply(self, role_context: dict, visible_events: list[dict], question: str) -> AgentTurn:
        if not self.client or self.profile is None:
            return AgentTurn("我需要再梳理一下现有线索，暂时不能下结论。", degraded=True)
        messages = self._messages(role_context, visible_events, question)
        if sum(len(item.content.encode("utf-8")) for item in messages) > self.settings.max_input_bytes:
            return AgentTurn("当前可见信息较多，我需要先整理后再回答。", degraded=True)
        usage = UsageAmount()
        usage_seen = False
        unknown_attempts = 0
        attempt_count = 0
        response_model = None
        provider_request_id = None
        finish_reason = None
        accounting_uncertain = False
        for attempt in range(self.retries + 1):
            attempt_count += 1
            try:
                result = await asyncio.wait_for(
                    self.client.chat_completion(messages, **self._call_params()),
                    timeout=self.timeout,
                )
            except Exception as error:
                # The provider may have accepted a timed-out request.  The service
                # keeps the full reservation until provider-side reconciliation.
                unknown_attempts += 1
                if attempt == self.retries or not provider_error_is_retryable(error):
                    break
                continue
            normalized_usage = normalize_provider_usage(result.usage)
            if normalized_usage is None:
                unknown_attempts += 1
            else:
                usage = usage + normalized_usage
                usage_seen = True
            if isinstance(result.model, str):
                response_model = result.model[:100]
            if isinstance(result.request_id, str):
                provider_request_id = result.request_id[:100]
            if isinstance(result.finish_reason, str):
                finish_reason = result.finish_reason[:50]
            # A provider/model switch could have a different price and behavior.
            # Charge conservatively and never publish that response.
            if response_model not in self.profile.allowed_models:
                accounting_uncertain = True
                break
            # Only a normal stop (or an omitted compatibility field) may become
            # public dialogue.  Truncated/filtered output is not retried because
            # doing so would resend the same commercial context and incur a new fee.
            if finish_reason not in (None, "stop"):
                break
            try:
                parsed = PlayerReply.model_validate_json(result.content)
                return AgentTurn(
                    parsed.message,
                    usage=self._usage_payload(usage, usage_seen),
                    usage_uncertain=unknown_attempts > 0,
                    model_attempted=True,
                    attempt_count=attempt_count,
                    response_model=response_model,
                    unknown_attempts=unknown_attempts,
                    provider_request_id=provider_request_id,
                    finish_reason=finish_reason,
                )
            except Exception:
                # Output validation failures have known usage when the provider
                # supplied it.  Do not resend commercial context for a malformed
                # reply; raw responses and validation details are never logged.
                break
        return AgentTurn(
            "这个问题我现在无法确认，但我愿意继续配合调查。",
            usage=self._usage_payload(usage, usage_seen),
            degraded=True,
            usage_uncertain=unknown_attempts > 0 or accounting_uncertain,
            model_attempted=True,
            attempt_count=attempt_count,
            response_model=response_model,
            unknown_attempts=unknown_attempts,
            provider_request_id=provider_request_id,
            finish_reason=finish_reason,
        )

    @staticmethod
    def summarize(events: list[dict], limit: int = 20) -> list[dict]:
        """Summary 职责：发送模型前限制长期上下文，不改变原始事件。"""
        return events[-limit:]
