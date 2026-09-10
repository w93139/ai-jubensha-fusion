"""Offline tests for deterministic model-call reservation and settlement."""
from decimal import Decimal

import pytest

from src.fusion.budget import BudgetPolicy, UsageAmount, provider_usage_is_complete


PROVIDER_RATE_CASES = (
    (
        "volcengine_ark",
        "ARK_CHARACTER",
        (Decimal("0.8"), Decimal("0.16"), Decimal("2")),
        "ark-character-fixture",
    ),
    (
        "aliyun_bailian",
        "DASHSCOPE_QWEN",
        (Decimal("0.2"), Decimal("0.04"), Decimal("0.8")),
        "dashscope-qwen-fixture",
    ),
)


def configure_provider_rates(monkeypatch) -> None:
    for _, prefix, rates, version in PROVIDER_RATE_CASES:
        monkeypatch.setenv(f"{prefix}_INPUT_COST_PER_MILLION", str(rates[0]))
        monkeypatch.setenv(f"{prefix}_CACHED_INPUT_COST_PER_MILLION", str(rates[1]))
        monkeypatch.setenv(f"{prefix}_OUTPUT_COST_PER_MILLION", str(rates[2]))
        monkeypatch.setenv(f"{prefix}_PRICING_VERSION", version)
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "true")
    monkeypatch.setenv("GAME_COST_BUDGET_CNY", "10")


def policy(**overrides) -> BudgetPolicy:
    values = {
        "token_limit": 1000,
        "cost_limit_cny": Decimal("1"),
        "input_rate_cny": Decimal("3"),
        "cached_input_rate_cny": Decimal("0.1"),
        "output_rate_cny": Decimal("9"),
        "paid_calls_enabled": True,
        "pricing_version": "fixture-2026-09-04",
    }
    values.update(overrides)
    return BudgetPolicy(**values)


def test_cache_tokens_are_not_double_charged():
    amount = policy().reported_amount({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 40},
    })
    assert amount.cached_prompt_tokens == 40
    assert amount.cost_cny == Decimal("364") / Decimal(1_000_000)


def test_paid_calls_fail_closed_without_limit_rates_or_pricing_version():
    requested = UsageAmount(prompt_tokens=10, completion_tokens=5)
    cases = [
        (policy(cost_limit_cny=None), "COST_LIMIT_REQUIRED"),
        (policy(input_rate_cny=Decimal("0")), "COST_RATES_REQUIRED"),
        (policy(cached_input_rate_cny=Decimal("0")), "COST_RATES_REQUIRED"),
        (policy(output_rate_cny=Decimal("0")), "COST_RATES_REQUIRED"),
        (policy(pricing_version=""), "PRICING_VERSION_REQUIRED"),
    ]
    for configured, reason in cases:
        assert configured.decide(UsageAmount(), UsageAmount(), requested).reason == reason


def test_active_reservation_is_included_before_new_call():
    configured = policy(token_limit=100, paid_calls_enabled=False)
    decision = configured.decide(
        UsageAmount(prompt_tokens=20),
        UsageAmount(prompt_tokens=50),
        UsageAmount(prompt_tokens=31),
    )
    assert decision.allowed is False and decision.reason == "TOKEN_BUDGET"


def test_unknown_usage_keeps_full_reservation_for_reconciliation():
    configured = policy()
    reservation = configured.amount(120, 40)
    settled = configured.settle(
        reservation,
        {"prompt_tokens": 10, "completion_tokens": 4},
        usage_uncertain=True,
    )
    assert settled.prompt_tokens == 120
    assert settled.completion_tokens == 40
    assert settled.cached_prompt_tokens == 0
    assert settled.cost_cny == reservation.cost_cny


def test_missing_or_malformed_provider_usage_is_not_complete():
    assert provider_usage_is_complete(None) is False
    assert provider_usage_is_complete({}) is False
    assert provider_usage_is_complete({"prompt_tokens": True, "completion_tokens": 1}) is False
    assert provider_usage_is_complete({"prompt_tokens": -1, "completion_tokens": 1}) is False
    assert provider_usage_is_complete({"prompt_tokens": 2, "completion_tokens": 1}) is True


def test_invalid_prompt_total_can_only_fall_back_to_consistent_cache_totals():
    fallback = {
        "prompt_tokens": -1,
        "prompt_cache_hit_tokens": 7,
        "prompt_cache_miss_tokens": 3,
        "completion_tokens": 2,
    }
    assert provider_usage_is_complete(fallback) is True
    assert UsageAmount.from_provider_usage(fallback) == UsageAmount(
        prompt_tokens=10,
        completion_tokens=2,
        cached_prompt_tokens=7,
    )

    conflict = dict(fallback, prompt_tokens=5)
    assert provider_usage_is_complete(conflict) is False
    assert UsageAmount.from_provider_usage(conflict) == UsageAmount()


@pytest.mark.parametrize("single_counter", [
    {"prompt_cache_hit_tokens": 2},
    {"prompt_cache_miss_tokens": 7},
])
def test_split_cache_counters_must_arrive_as_a_consistent_pair(single_counter):
    usage = {"prompt_tokens": 5, "completion_tokens": 2, **single_counter}
    assert provider_usage_is_complete(usage) is False
    assert UsageAmount.from_provider_usage(usage) == UsageAmount()


@pytest.mark.parametrize("total", [100, True, -1])
def test_provider_total_tokens_must_match_the_component_totals(total):
    usage = {
        "prompt_tokens": 5,
        "prompt_cache_hit_tokens": 2,
        "prompt_cache_miss_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": total,
    }
    assert provider_usage_is_complete(usage) is False
    assert UsageAmount.from_provider_usage(usage) == UsageAmount()


def test_matching_provider_total_tokens_is_accepted():
    usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    assert provider_usage_is_complete(usage) is True
    assert UsageAmount.from_provider_usage(usage).total_tokens == 7


def test_snapshot_round_trip_keeps_decimal_budget():
    original = policy(cost_limit_cny=Decimal("2.75"))
    restored = BudgetPolicy.from_snapshot(original.to_snapshot())
    assert restored == original


@pytest.mark.parametrize("provider,prefix,rates,version", PROVIDER_RATE_CASES, ids=("ark", "bailian"))
def test_environment_rates_are_bound_to_the_selected_provider(
    monkeypatch, provider, prefix, rates, version,
):
    configure_provider_rates(monkeypatch)
    configured = BudgetPolicy.from_env(provider)
    assert (
        configured.input_rate_cny,
        configured.cached_input_rate_cny,
        configured.output_rate_cny,
    ) == rates
    assert configured.pricing_version == version
    assert configured.price(1_000_000, 1_000_000) == rates[0] + rates[2]
    assert configured.price(1_000_000, 1_000_000, 1_000_000) == rates[1] + rates[2]


def test_unknown_provider_cannot_inherit_another_providers_rates(monkeypatch):
    configure_provider_rates(monkeypatch)
    configured = BudgetPolicy.from_env("unsupported-provider")
    assert configured.rates_configured is False
    assert configured.pricing_version == ""
    decision = configured.decide(UsageAmount(), UsageAmount(), UsageAmount(prompt_tokens=1))
    assert decision.allowed is False and decision.reason == "COST_RATES_REQUIRED"


def test_missing_cached_rate_is_conservatively_charged_as_uncached(monkeypatch):
    configure_provider_rates(monkeypatch)
    monkeypatch.delenv("ARK_CHARACTER_CACHED_INPUT_COST_PER_MILLION", raising=False)
    configured = BudgetPolicy.from_env("volcengine_ark")
    assert configured.cached_input_rate_cny == configured.input_rate_cny == Decimal("0.8")


def test_malformed_explicit_token_limit_fails_closed(monkeypatch):
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "not-a-number")
    assert BudgetPolicy.from_env("volcengine_ark").token_limit == 0
