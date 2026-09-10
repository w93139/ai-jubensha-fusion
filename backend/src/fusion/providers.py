"""Allowlisted cloud-model profiles for the Fusion player dialogue task.

Profiles contain configuration *names*, never credentials.  The caller resolves
only the selected profile so a request cannot silently borrow another provider's
key or fall back across providers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_PLAYER_PROVIDER = "volcengine_ark"


def _player_response_format() -> dict[str, Any]:
    """Return a fresh strict schema accepted by both selected providers."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "fusion_player_reply",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "action": {"type": "string", "enum": ["speak"]},
                },
                "required": ["message", "action"],
                "additionalProperties": False,
            },
        },
    }


@dataclass(frozen=True)
class PlayerProviderProfile:
    """One exact provider/model/API contract approved for Fusion dialogue."""

    name: str
    api_key_env: str
    base_url_env: str
    model_env: str
    default_base_url: str
    default_model: str
    allowed_base_urls: frozenset[str]
    allowed_models: frozenset[str]
    pricing_env_prefix: str
    request_contract_version: str

    def request_params(self, max_output_tokens: int, temperature: float) -> dict[str, Any]:
        common: dict[str, Any] = {
            "response_format": _player_response_format(),
            "temperature": temperature,
        }
        if self.name == "volcengine_ark":
            # Doubao Character enables thinking by default.  Player dialogue must
            # not emit internal reasoning or pay for an unbounded thought chain.
            common["max_tokens"] = max_output_tokens
            common["extra_body"] = {"thinking": {"type": "disabled"}}
        elif self.name == "aliyun_bailian":
            # Bailian's current OpenAI-compatible contract uses
            # max_completion_tokens.  preserve_thinking=False also prevents
            # historical reasoning from being sent and billed again.
            common["max_completion_tokens"] = max_output_tokens
            common["extra_body"] = {
                "enable_thinking": False,
                "preserve_thinking": False,
            }
        else:  # Defensive: registry entries must always have an explicit contract.
            raise ValueError("unsupported Fusion player provider")
        return common

    def reserved_completion_tokens(self, max_output_tokens: int) -> int:
        """Cover provider-side output-limit tolerance before the call."""
        # Bailian documents that actual output can exceed the requested maximum
        # by up to 10 tokens.  Six extra tokens keep the reservation arithmetic
        # simple while still covering that tolerance conservatively.
        return max_output_tokens + (16 if self.name == "aliyun_bailian" else 0)


PLAYER_PROVIDER_PROFILES: dict[str, PlayerProviderProfile] = {
    "volcengine_ark": PlayerProviderProfile(
        name="volcengine_ark",
        api_key_env="ARK_API_KEY",
        base_url_env="ARK_BASE_URL",
        model_env="ARK_CHARACTER_MODEL",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-seed-character-260628",
        allowed_base_urls=frozenset({"https://ark.cn-beijing.volces.com/api/v3"}),
        allowed_models=frozenset({"doubao-seed-character-260628"}),
        pricing_env_prefix="ARK_CHARACTER",
        request_contract_version="ark-chat-character-v2",
    ),
    "aliyun_bailian": PlayerProviderProfile(
        name="aliyun_bailian",
        api_key_env="DASHSCOPE_API_KEY",
        base_url_env="DASHSCOPE_BASE_URL",
        model_env="DASHSCOPE_QWEN_MODEL",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        # Pin the dated release so an alias cannot silently change mid-production.
        default_model="qwen3.7-flash-2026-07-15",
        allowed_base_urls=frozenset({"https://dashscope.aliyuncs.com/compatible-mode/v1"}),
        allowed_models=frozenset({"qwen3.7-flash-2026-07-15"}),
        pricing_env_prefix="DASHSCOPE_QWEN",
        request_contract_version="bailian-chat-qwen37-v2",
    ),
}


def get_player_provider_profile(name: str) -> PlayerProviderProfile | None:
    """Resolve an exact profile; unknown values never fall back to a default."""
    return PLAYER_PROVIDER_PROFILES.get(name)
