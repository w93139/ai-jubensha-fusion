"""Deterministic per-game token and cost accounting for Fusion model calls.

The model is never trusted to decide whether a call is affordable.  A caller
reserves the worst-case amount before network I/O, then settles that reservation
from provider usage (or conservatively keeps the maximum when usage is unknown).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from typing import Any, Mapping, cast

from src.fusion.providers import DEFAULT_PLAYER_PROVIDER, get_player_provider_profile


ZERO = Decimal("0")


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_int(value: Any, default: int = 0, minimum: int = 0, maximum: int = 1_000_000_000) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(parsed, maximum))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    # An explicitly malformed hard limit must not silently expand to a default.
    return _safe_int(value, minimum, minimum, maximum)


def _safe_decimal(value: Any, default: Decimal = ZERO) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    if not parsed.is_finite() or parsed < ZERO:
        return default
    return parsed


def env_flag(name: str, default: bool = False) -> bool:
    """Only explicit true values enable paid/network behavior."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_provider_usage(value: Any) -> "UsageAmount | None":
    """Return one strictly validated provider-usage interpretation.

    Cache hits are cheaper than ordinary input, so contradictory cache fields
    must never be accepted by choosing whichever number is most convenient.  A
    malformed explicit prompt total may fall back to DeepSeek's two valid cache
    counters, but conflicting valid totals make the whole attempt unknown.
    """
    if not isinstance(value, Mapping):
        return None
    raw_completion = value.get("completion_tokens")
    if not _is_nonnegative_int(raw_completion):
        return None
    completion_tokens = cast(int, raw_completion)

    raw_prompt = value.get("prompt_tokens")
    hit_present = "prompt_cache_hit_tokens" in value
    miss_present = "prompt_cache_miss_tokens" in value
    cache_hit = value.get("prompt_cache_hit_tokens")
    cache_miss = value.get("prompt_cache_miss_tokens")
    if hit_present != miss_present:
        return None
    if hit_present and not _is_nonnegative_int(cache_hit):
        return None
    if miss_present and not _is_nonnegative_int(cache_miss):
        return None

    derived_prompt = None
    if hit_present and miss_present:
        derived_prompt = cast(int, cache_hit) + cast(int, cache_miss)
    if _is_nonnegative_int(raw_prompt):
        prompt = cast(int, raw_prompt)
        if derived_prompt is not None and derived_prompt != prompt:
            return None
    elif derived_prompt is not None:
        prompt = derived_prompt
    else:
        return None

    cached_candidates: list[int] = []
    if hit_present:
        cached_candidates.append(cast(int, cache_hit))
    if "cached_prompt_tokens" in value:
        cached_prompt = value.get("cached_prompt_tokens")
        if not _is_nonnegative_int(cached_prompt):
            return None
        cached_candidates.append(cast(int, cached_prompt))
    details = value.get("prompt_tokens_details")
    if details is not None:
        if not isinstance(details, Mapping) or not _is_nonnegative_int(details.get("cached_tokens")):
            return None
        cached_candidates.append(cast(int, details["cached_tokens"]))
    if cached_candidates and any(item != cached_candidates[0] for item in cached_candidates[1:]):
        return None
    cached = cached_candidates[0] if cached_candidates else 0
    if cached > prompt:
        return None

    reasoning = 0
    completion_details = value.get("completion_tokens_details")
    if completion_details is not None:
        if not isinstance(completion_details, Mapping) or not _is_nonnegative_int(
            completion_details.get("reasoning_tokens"),
        ):
            return None
        reasoning = cast(int, completion_details["reasoning_tokens"])
        if reasoning > completion_tokens:
            return None
    if "total_tokens" in value:
        total = value.get("total_tokens")
        if not _is_nonnegative_int(total) or total != prompt + completion_tokens:
            return None
    return UsageAmount(
        prompt_tokens=prompt,
        completion_tokens=completion_tokens,
        cached_prompt_tokens=cached,
        reasoning_tokens=reasoning,
    )


def provider_usage_is_complete(value: Any) -> bool:
    """True only when strict normalization produces one unambiguous total."""
    return normalize_provider_usage(value) is not None


def usage_metadata_is_valid(value: Any) -> bool:
    """Validate persisted v2 ledger amounts without coercing corruption to zero."""
    if not isinstance(value, Mapping):
        return False
    required = ("prompt_tokens", "completion_tokens", "cached_prompt_tokens", "cost_cny")
    if any(name not in value for name in required):
        return False
    if not all(_is_nonnegative_int(value[name]) for name in required[:3]):
        return False
    if value["cached_prompt_tokens"] > value["prompt_tokens"]:
        return False
    reasoning = value.get("reasoning_tokens", 0)
    if not _is_nonnegative_int(reasoning) or reasoning > value["completion_tokens"]:
        return False
    if isinstance(value["cost_cny"], bool):
        return False
    try:
        cost = Decimal(str(value["cost_cny"]))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return cost.is_finite() and cost >= ZERO


@dataclass(frozen=True)
class UsageAmount:
    """Sanitized usage plus its conservative CNY estimate."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    cost_cny: Decimal = ZERO
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "UsageAmount") -> "UsageAmount":
        return UsageAmount(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens + other.cached_prompt_tokens,
            cost_cny=self.cost_cny + other.cost_cny,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_metadata(self) -> dict[str, int | str]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            # JSON numbers round through binary float.  A decimal string keeps the
            # receipt authoritative while the legacy session Float stays a summary.
            "cost_cny": str(self.cost_cny),
            "reasoning_tokens": self.reasoning_tokens,
        }

    @classmethod
    def from_metadata(cls, value: Any) -> "UsageAmount":
        if not isinstance(value, Mapping):
            return cls()
        prompt = _safe_int(value.get("prompt_tokens"))
        completion = _safe_int(value.get("completion_tokens"))
        cached = min(prompt, _safe_int(value.get("cached_prompt_tokens")))
        reasoning = min(completion, _safe_int(value.get("reasoning_tokens")))
        return cls(prompt, completion, cached, _safe_decimal(value.get("cost_cny")), reasoning)

    @classmethod
    def from_provider_usage(cls, value: Any) -> "UsageAmount":
        """Backward-compatible empty value for an incomplete provider receipt."""
        return normalize_provider_usage(value) or cls()


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class BudgetPolicy:
    """A fail-closed paid-call policy shared by all AI roles in one game."""

    token_limit: int
    cost_limit_cny: Decimal | None
    input_rate_cny: Decimal
    cached_input_rate_cny: Decimal
    output_rate_cny: Decimal
    paid_calls_enabled: bool
    pricing_version: str = ""

    @classmethod
    def from_env(cls, provider: str | None = None) -> "BudgetPolicy":
        """Load only the selected provider's prices; unknown providers get zeros."""
        selected = provider
        if selected is None:
            raw_provider = os.getenv("FUSION_PLAYER_PROVIDER")
            selected = DEFAULT_PLAYER_PROVIDER if raw_provider is None else raw_provider.strip().lower()
        profile = get_player_provider_profile(selected)
        prefix = profile.pricing_env_prefix if profile else "UNSUPPORTED_FUSION_PROVIDER"
        input_rate = _safe_decimal(os.getenv(f"{prefix}_INPUT_COST_PER_MILLION", "0"))
        cached_rate_raw = os.getenv(f"{prefix}_CACHED_INPUT_COST_PER_MILLION")
        # Missing cache pricing is conservatively billed as an ordinary cache miss.
        cached_rate = input_rate if cached_rate_raw is None else _safe_decimal(cached_rate_raw)
        cost_limit = _safe_decimal(os.getenv("GAME_COST_BUDGET_CNY", "0"))
        return cls(
            token_limit=_env_int("GAME_TOKEN_BUDGET", 30_000, 0, 1_000_000_000),
            cost_limit_cny=cost_limit if cost_limit > ZERO else None,
            input_rate_cny=input_rate,
            cached_input_rate_cny=cached_rate,
            output_rate_cny=_safe_decimal(os.getenv(f"{prefix}_OUTPUT_COST_PER_MILLION", "0")),
            paid_calls_enabled=env_flag("ENABLE_PAID_MODEL_CALLS", False),
            pricing_version=os.getenv(f"{prefix}_PRICING_VERSION", "").strip()[:100],
        )

    def to_snapshot(self) -> dict[str, str | int | bool | None]:
        return {
            "schema_version": 1,
            "token_limit": self.token_limit,
            "cost_limit_cny": str(self.cost_limit_cny) if self.cost_limit_cny is not None else None,
            "input_rate_cny": str(self.input_rate_cny),
            "cached_input_rate_cny": str(self.cached_input_rate_cny),
            "output_rate_cny": str(self.output_rate_cny),
            "paid_calls_enabled": self.paid_calls_enabled,
            "pricing_version": self.pricing_version,
        }

    @classmethod
    def from_snapshot(cls, value: Any) -> "BudgetPolicy | None":
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            return None
        cost_limit = _safe_decimal(value.get("cost_limit_cny"))
        return cls(
            token_limit=_safe_int(value.get("token_limit"), maximum=1_000_000_000),
            cost_limit_cny=cost_limit if cost_limit > ZERO else None,
            input_rate_cny=_safe_decimal(value.get("input_rate_cny")),
            cached_input_rate_cny=_safe_decimal(value.get("cached_input_rate_cny")),
            output_rate_cny=_safe_decimal(value.get("output_rate_cny")),
            paid_calls_enabled=value.get("paid_calls_enabled") is True,
            pricing_version=str(value.get("pricing_version") or "")[:100],
        )

    @property
    def rates_configured(self) -> bool:
        return (
            self.input_rate_cny > ZERO
            and self.cached_input_rate_cny > ZERO
            and self.output_rate_cny > ZERO
        )

    def price(self, prompt_tokens: int, completion_tokens: int, cached_prompt_tokens: int = 0) -> Decimal:
        prompt = _safe_int(prompt_tokens)
        completion = _safe_int(completion_tokens)
        cached = min(prompt, _safe_int(cached_prompt_tokens))
        uncached = prompt - cached
        return (
            Decimal(uncached) * self.input_rate_cny
            + Decimal(cached) * self.cached_input_rate_cny
            + Decimal(completion) * self.output_rate_cny
        ) / Decimal(1_000_000)

    def amount(self, prompt_tokens: int, completion_tokens: int, cached_prompt_tokens: int = 0,
               reasoning_tokens: int = 0) -> UsageAmount:
        prompt = _safe_int(prompt_tokens)
        completion = _safe_int(completion_tokens)
        cached = min(prompt, _safe_int(cached_prompt_tokens))
        reasoning = min(completion, _safe_int(reasoning_tokens))
        return UsageAmount(prompt, completion, cached, self.price(prompt, completion, cached), reasoning)

    def reported_amount(self, usage: Any) -> UsageAmount:
        normalized = UsageAmount.from_provider_usage(usage)
        return self.amount(
            normalized.prompt_tokens,
            normalized.completion_tokens,
            normalized.cached_prompt_tokens,
            normalized.reasoning_tokens,
        )

    def decide(self, actual: UsageAmount, active: UsageAmount, requested: UsageAmount) -> BudgetDecision:
        if actual.total_tokens + active.total_tokens + requested.total_tokens > self.token_limit:
            return BudgetDecision(False, "TOKEN_BUDGET")
        if self.paid_calls_enabled:
            if self.cost_limit_cny is None:
                return BudgetDecision(False, "COST_LIMIT_REQUIRED")
            if not self.rates_configured:
                return BudgetDecision(False, "COST_RATES_REQUIRED")
            if not self.pricing_version or self.pricing_version.upper().startswith("CHANGE_ME"):
                return BudgetDecision(False, "PRICING_VERSION_REQUIRED")
            if actual.cost_cny + active.cost_cny + requested.cost_cny > self.cost_limit_cny:
                return BudgetDecision(False, "COST_BUDGET")
        return BudgetDecision(True)

    def settle(self, reservation: UsageAmount, provider_usage: Any, usage_uncertain: bool,
               unknown_attempts: int | None = None, max_attempts: int | None = None) -> UsageAmount:
        reported = self.reported_amount(provider_usage)
        if not usage_uncertain:
            return reported
        if unknown_attempts is not None and max_attempts and max_attempts > 0:
            unknown_count = max(0, min(int(unknown_attempts), max_attempts))
            slot_prompt = (reservation.prompt_tokens + max_attempts - 1) // max_attempts
            slot_completion = (reservation.completion_tokens + max_attempts - 1) // max_attempts
            unknown = self.amount(slot_prompt * unknown_count, slot_completion * unknown_count)
            return reported + unknown
        # A timeout/network failure can happen after the provider accepted the work.
        # Keep the worst case in the game ledger until provider-side reconciliation.
        prompt = max(reservation.prompt_tokens, reported.prompt_tokens)
        completion = max(reservation.completion_tokens, reported.completion_tokens)
        cost = max(reservation.cost_cny, self.price(prompt, completion, 0), reported.cost_cny)
        return UsageAmount(prompt, completion, 0, cost)


def estimate_text_tokens(text: str) -> int:
    """Conservative content bound used only for pre-call reservation.

    DeepSeek's public tokenizer utility is still described as an estimate.  Using
    at least one token per UTF-8 byte is deliberately much larger than the normal
    Chinese/English ratio; the caller adds a separate chat-template envelope.
    """
    if not text:
        return 0
    byte_count = len(text.encode("utf-8"))
    return max(1, byte_count)
