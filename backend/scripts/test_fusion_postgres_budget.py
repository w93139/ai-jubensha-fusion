"""Run the explicit PostgreSQL Fusion budget-reservation integration test.

This entry point deliberately does not load ``.env``.  It accepts only a
dedicated test URL, rejects remote or production-looking targets, disables all
model calls, and runs one exact pytest module that creates an ephemeral schema.

Usage from the repository root::

    FUSION_POSTGRES_TEST_CONFIRM=CREATE_EPHEMERAL_SCHEMA \
    FUSION_POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox' \
    backend/.venv/bin/python backend/scripts/test_fusion_postgres_budget.py
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Mapping
from unittest.mock import patch

from sqlalchemy.engine import URL, make_url


BACKEND = Path(__file__).resolve().parents[1]
TEST_DATABASE_ENV = "FUSION_POSTGRES_TEST_DATABASE_URL"
CONFIRM_ENV = "FUSION_POSTGRES_TEST_CONFIRM"
REQUIRED_CONFIRMATION = "CREATE_EPHEMERAL_SCHEMA"
REQUIRED_TEST_HOST = "127.0.0.1"
REQUIRED_TEST_PORT = 55432
REQUIRED_TEST_DATABASE = "fusion_pg_it_sandbox"
REQUIRED_TEST_USER = "fusion_it"


class UnsafeTestDatabase(ValueError):
    """Raised before any connection when a target is not clearly disposable."""


@dataclass(frozen=True)
class ValidatedTestDatabase:
    url: URL
    host: str
    port: int
    database: str

    @property
    def safe_label(self) -> str:
        """A log label that never contains username or password."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}/{self.database}"


def validate_test_database_environment(
    environ: Mapping[str, str] | None = None,
) -> ValidatedTestDatabase:
    """Validate a local, project-specific PostgreSQL test target.

    The URL itself is intentionally never included in an exception.  This keeps
    credentials out of terminal logs when a user mistypes a target.
    """
    values = os.environ if environ is None else environ
    if values.get(CONFIRM_ENV) != REQUIRED_CONFIRMATION:
        raise UnsafeTestDatabase(
            f"refusing database access: set {CONFIRM_ENV}={REQUIRED_CONFIRMATION}",
        )
    raw_url = values.get(TEST_DATABASE_ENV, "").strip()
    if not raw_url:
        raise UnsafeTestDatabase(
            f"refusing database access: {TEST_DATABASE_ENV} must be provided explicitly",
        )
    try:
        url = make_url(raw_url)
    except Exception as error:
        raise UnsafeTestDatabase("refusing database access: invalid test database URL") from error

    if url.drivername != "postgresql+psycopg":
        raise UnsafeTestDatabase(
            "refusing database access: only postgresql+psycopg test URLs are allowed",
        )
    host = (url.host or "").lower().rstrip(".")
    if host != REQUIRED_TEST_HOST:
        raise UnsafeTestDatabase(
            f"refusing database access: the PostgreSQL test host must be {REQUIRED_TEST_HOST}",
        )
    database = (url.database or "").lower()
    if database != REQUIRED_TEST_DATABASE:
        raise UnsafeTestDatabase(
            f"refusing database access: the database must be {REQUIRED_TEST_DATABASE}",
        )
    if (url.username or "").lower() != REQUIRED_TEST_USER:
        raise UnsafeTestDatabase(
            f"refusing database access: the database user must be {REQUIRED_TEST_USER}",
        )
    if url.query:
        # libpq query options can redirect the connection or alter search_path.
        # The harness owns all connection options, so reject rather than merge.
        raise UnsafeTestDatabase(
            "refusing database access: query parameters are not allowed in the test URL",
        )
    try:
        port = url.port or 5432
    except ValueError as error:
        raise UnsafeTestDatabase("refusing database access: invalid PostgreSQL port") from error
    if port != REQUIRED_TEST_PORT:
        raise UnsafeTestDatabase(
            f"refusing database access: the PostgreSQL test port must be {REQUIRED_TEST_PORT}",
        )
    return ValidatedTestDatabase(
        url=url.set(drivername="postgresql+psycopg"),
        host=host,
        port=port,
        database=database,
    )


def _disable_non_database_network_features() -> None:
    """Force this subprocess into a database-only, non-billable configuration."""
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["ENABLE_PAID_MODEL_CALLS"] = "false"
    os.environ["GAME_TOKEN_BUDGET"] = "20"
    os.environ["GAME_COST_BUDGET_CNY"] = "0"
    os.environ["FUSION_PLAYER_PROVIDER"] = "volcengine_ark"
    os.environ["ARK_CHARACTER_MODEL"] = "doubao-seed-character-260628"
    os.environ["ARK_CHARACTER_INPUT_COST_PER_MILLION"] = "0"
    os.environ["ARK_CHARACTER_CACHED_INPUT_COST_PER_MILLION"] = "0"
    os.environ["ARK_CHARACTER_OUTPUT_COST_PER_MILLION"] = "0"
    os.environ["ARK_CHARACTER_PRICING_VERSION"] = ""
    os.environ["LLM_MAX_RETRIES"] = "0"
    for name in (
        "ARK_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "TTS_API_KEY",
        "MINIMAX_GROUP_ID",
        "REDIS_URL",
    ):
        os.environ.pop(name, None)


def main() -> int:
    try:
        target = validate_test_database_environment()
    except UnsafeTestDatabase as error:
        print(str(error), file=sys.stderr)
        return 2

    _disable_non_database_network_features()
    sys.path.insert(0, str(BACKEND))
    sys.dont_write_bytecode = True
    print(f"Safe PostgreSQL integration target: {target.safe_label}")
    print("Paid model calls are forced off; only a random ephemeral schema will be modified.")

    import pytest

    suite = BACKEND / "tests" / "postgres_integration"
    test_file = suite / "test_budget_reservation.py"
    with patch("dotenv.load_dotenv", return_value=False):
        return pytest.main([
            str(test_file),
            f"--confcutdir={suite}",
            "-p", "no:cacheprovider",
            "--maxfail=1",
            "-q",
            *sys.argv[1:],
        ])


if __name__ == "__main__":
    raise SystemExit(main())
