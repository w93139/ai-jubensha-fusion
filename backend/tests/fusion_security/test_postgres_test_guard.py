"""Offline checks for the opt-in PostgreSQL integration-test safety gate."""
from __future__ import annotations

import os

import pytest

from scripts.test_fusion_postgres_budget import (
    CONFIRM_ENV,
    REQUIRED_CONFIRMATION,
    TEST_DATABASE_ENV,
    UnsafeTestDatabase,
    _disable_non_database_network_features,
    validate_test_database_environment,
)


SAFE_URL = "postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox"


def environment(url: str = SAFE_URL, confirmation: str = REQUIRED_CONFIRMATION) -> dict[str, str]:
    return {TEST_DATABASE_ENV: url, CONFIRM_ENV: confirmation}


def test_exact_disposable_target_is_accepted_without_exposing_credentials() -> None:
    target = validate_test_database_environment(environment(
        "postgresql+psycopg://fusion_it:secret-never-log@127.0.0.1:55432/"
        "fusion_pg_it_sandbox",
    ))
    assert target.safe_label == "127.0.0.1:55432/fusion_pg_it_sandbox"
    assert "secret-never-log" not in target.safe_label


@pytest.mark.parametrize("values", [
    {},
    {TEST_DATABASE_ENV: SAFE_URL},
    environment(confirmation="yes"),
])
def test_missing_or_inexact_confirmation_is_rejected(values: dict[str, str]) -> None:
    with pytest.raises(UnsafeTestDatabase):
        validate_test_database_environment(values)


@pytest.mark.parametrize("url", [
    "sqlite:///fusion_pg_it_sandbox",
    "postgresql://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox",
    "postgresql+psycopg://fusion_it@database.example:55432/fusion_pg_it_sandbox",
    "postgresql+psycopg://fusion_it@localhost:55432/fusion_pg_it_sandbox",
    "postgresql+psycopg://fusion_it@127.0.0.1:5432/fusion_pg_it_sandbox",
    "postgresql+psycopg://postgres@127.0.0.1:55432/fusion_pg_it_sandbox",
    "postgresql+psycopg://fusion_it@127.0.0.1:55432/postgres",
    "postgresql+psycopg://fusion_it@127.0.0.1:55432/jubensha_db",
    "postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox?host=evil",
    "postgresql+asyncpg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox",
])
def test_every_target_deviation_is_rejected(url: str) -> None:
    with pytest.raises(UnsafeTestDatabase):
        validate_test_database_environment(environment(url))


def test_rejection_never_echoes_password() -> None:
    password = "never-print-this-password"
    with pytest.raises(UnsafeTestDatabase) as captured:
        validate_test_database_environment(environment(
            f"postgresql+psycopg://fusion_it:{password}@127.0.0.1:55432/postgres",
        ))
    assert password not in str(captured.value)


def test_database_runner_forces_every_paid_model_path_off(monkeypatch) -> None:
    forced_names = (
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTHON_DOTENV_DISABLED",
        "ENABLE_PAID_MODEL_CALLS",
        "GAME_TOKEN_BUDGET",
        "GAME_COST_BUDGET_CNY",
        "FUSION_PLAYER_PROVIDER",
        "ARK_CHARACTER_MODEL",
        "ARK_CHARACTER_INPUT_COST_PER_MILLION",
        "ARK_CHARACTER_CACHED_INPUT_COST_PER_MILLION",
        "ARK_CHARACTER_OUTPUT_COST_PER_MILLION",
        "ARK_CHARACTER_PRICING_VERSION",
        "LLM_MAX_RETRIES",
    )
    removed_names = (
        "ARK_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "TTS_API_KEY",
        "MINIMAX_GROUP_ID",
        "REDIS_URL",
    )
    for name in forced_names:
        monkeypatch.setenv(name, "unsafe-original-value")
    for name in removed_names:
        monkeypatch.setenv(name, "must-be-removed")

    _disable_non_database_network_features()

    assert os.environ["ENABLE_PAID_MODEL_CALLS"] == "false"
    assert os.environ["GAME_TOKEN_BUDGET"] == "20"
    assert all(name not in os.environ for name in removed_names)
