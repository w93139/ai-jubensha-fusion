"""Offline safety checks; these tests never connect to PostgreSQL."""
import io
import os
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest

from scripts import test_package_play_postgres as runner


SAFE_URL = "postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox"
SCHEMA = runner.SCHEMA_PREFIX + "a" * 32


def environment(url=SAFE_URL):
    return {runner.TEST_DATABASE_ENV: url, runner.CONFIRM_ENV: runner.REQUIRED_CONFIRMATION}


def test_package_play_postgres_guard_exact_target_and_hidden_credentials():
    target = runner.validate_environment(environment(SAFE_URL.replace("fusion_it@", "fusion_it:NEVER_LOG_THIS@")))
    assert target.safe_label == "127.0.0.1:55432/fusion_pg_it_sandbox"
    assert "NEVER_LOG_THIS" not in target.safe_label
    with pytest.raises(runner.UnsafeTestDatabase) as caught:
        runner.validate_environment(environment(SAFE_URL.replace("fusion_it@", "fusion_it:NEVER_LOG_THIS@").replace("55432", "5432")))
    assert "NEVER_LOG_THIS" not in str(caught.value)


@pytest.mark.parametrize("values", [{}, {runner.TEST_DATABASE_ENV: SAFE_URL},
                                   environment() | {runner.CONFIRM_ENV: "CREATE_EPHEMERAL_SCHEMA"}])
def test_package_play_postgres_guard_requires_its_own_explicit_confirmation(values):
    with pytest.raises(runner.UnsafeTestDatabase):
        runner.validate_environment(values)


@pytest.mark.parametrize("url", [
    "sqlite:///private.sqlite", SAFE_URL.replace("127.0.0.1", "localhost"),
    SAFE_URL.replace("127.0.0.1", "127.0.0.1."), SAFE_URL.replace("127.0.0.1", "192.0.2.1"),
    SAFE_URL.replace("55432", "5432"), SAFE_URL.replace("fusion_pg_it_sandbox", "postgres"),
    SAFE_URL.replace("fusion_it@", "postgres@"), SAFE_URL.replace("fusion_it@", "FUSION_IT@"),
    SAFE_URL.replace("fusion_pg_it_sandbox", "FUSION_PG_IT_SANDBOX"),
    SAFE_URL + "?options=-csearch_path%3Dpublic", SAFE_URL + "?hostaddr=192.0.2.1",
])
def test_package_play_postgres_guard_rejects_every_target_escape(url):
    with pytest.raises(runner.UnsafeTestDatabase):
        runner.validate_environment(environment(url))


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSFILE", "PGPASSWORD"])
def test_package_play_postgres_guard_rejects_libpq_environment_redirects_without_echo(name):
    with pytest.raises(runner.UnsafeTestDatabase) as caught:
        runner.validate_environment(environment() | {name: "PRIVATE_ENV_SENTINEL"})
    assert "PRIVATE_ENV_SENTINEL" not in str(caught.value)


def test_package_play_postgres_guard_search_path_has_no_public_fallback():
    options = runner.database_connect_args(SCHEMA)
    assert options["hostaddr"] == "127.0.0.1" and options["connect_timeout"] == 5
    assert f"search_path={SCHEMA} " in options["options"]
    assert "public" not in options["options"]
    assert "search_path=pg_catalog " in runner.database_connect_args()["options"]


@pytest.mark.parametrize("name", ["public", "package_play_it_", SCHEMA + "x", SCHEMA + '";DROP SCHEMA public;--',
                                  "fusion_budget_it_" + "a" * 32, SCHEMA.upper(), None])
def test_package_play_postgres_guard_rejects_unowned_schema_names_before_sql(name):
    connection = Mock()
    with pytest.raises(runner.UnsafeTestDatabase):
        runner.drop_owned_schema(connection, name=name, oid=1, owner="fusion_it")
    assert connection.mock_calls == []


@pytest.mark.parametrize("identity", [(2, "fusion_it"), (1, "another_owner")])
def test_package_play_postgres_guard_cleanup_refuses_replaced_schema(identity):
    connection = Mock()
    connection.execute.return_value.one_or_none.return_value = identity
    with pytest.raises(runner.UnsafeTestDatabase):
        runner.drop_owned_schema(connection, name=SCHEMA, oid=1, owner="fusion_it")
    assert len(connection.execute.call_args_list) == 1
    assert "DROP" not in str(connection.execute.call_args.args[0])


def test_package_play_postgres_guard_cleanup_drops_only_exact_created_identity():
    connection = Mock()
    connection.execute.return_value.one_or_none.return_value = (1, "fusion_it")
    runner.drop_owned_schema(connection, name=SCHEMA, oid=1, owner="fusion_it")
    assert str(connection.execute.call_args_list[-1].args[0]) == f'DROP SCHEMA "{SCHEMA}" CASCADE'
    assert len(connection.execute.call_args_list) == 2


def test_package_play_postgres_guard_connection_failure_before_schema_creation_never_cleans(monkeypatch):
    monkeypatch.setattr(runner, "validate_environment", lambda: runner.validate_test_database_environment(
        {runner.TEST_DATABASE_ENV: SAFE_URL, runner.BUDGET_CONFIRM_ENV: runner.BUDGET_CONFIRMATION}))
    bootstrap = MagicMock()
    bootstrap.begin.side_effect = RuntimeError("synthetic connection failure")
    monkeypatch.setattr(runner, "create_engine", Mock(return_value=bootstrap))
    cleanup = Mock()
    monkeypatch.setattr(runner, "drop_owned_schema", cleanup)
    with pytest.raises(RuntimeError, match="synthetic connection failure"), runner.isolated_schema():
        raise AssertionError("unreachable")
    cleanup.assert_not_called()
    bootstrap.dispose.assert_called_once()


def test_package_play_postgres_guard_later_setup_failure_cleans_owned_schema(monkeypatch):
    monkeypatch.setattr(runner, "validate_environment", lambda: runner.validate_test_database_environment(
        {runner.TEST_DATABASE_ENV: SAFE_URL, runner.BUDGET_CONFIRM_ENV: runner.BUDGET_CONFIRMATION}))
    bootstrap = MagicMock()
    monkeypatch.setattr(runner, "create_engine", Mock(side_effect=[bootstrap, RuntimeError("synthetic engine failure")]))
    monkeypatch.setattr(runner, "_check_server", Mock())
    monkeypatch.setattr(runner, "_schema_identity", Mock(side_effect=[(42, "fusion_it"), None]))
    cleanup = Mock()
    monkeypatch.setattr(runner, "drop_owned_schema", cleanup)
    with pytest.raises(RuntimeError, match="synthetic engine failure"), runner.isolated_schema():
        raise AssertionError("unreachable")
    assert cleanup.call_count == 1
    assert cleanup.call_args.kwargs["oid"] == 42 and cleanup.call_args.kwargs["owner"] == "fusion_it"
    assert runner.validate_schema_name(cleanup.call_args.kwargs["name"])
    bootstrap.dispose.assert_called_once()


@pytest.mark.parametrize("args", [["backend/tests"], ["-k", "anything"], ["--override-ini=pythonpath=.."],
                                   ["--confcutdir=/"], ["--verbose", "--verbose"]])
def test_package_play_postgres_guard_arbitrary_pytest_escape_options_are_refused(args):
    with pytest.raises(runner.UnsafeTestDatabase):
        runner.runner_options(args)


def test_package_play_postgres_guard_only_database_python_socket_is_permitted(monkeypatch):
    connect = Mock(return_value=None)
    monkeypatch.setattr(socket.socket, "connect", connect)
    with socket.socket() as sock, runner.database_only_network():
        for address in (("192.0.2.1", 55432), ("127.0.0.1", 443), ("localhost", 55432), "/tmp/other.sock"):
            with pytest.raises(AssertionError, match="non-database"):
                sock.connect(address)
        sock.connect(("127.0.0.1", 55432))
    assert connect.call_count == 1


def test_package_play_postgres_guard_runner_selects_one_module_and_disables_overrides(monkeypatch):
    monkeypatch.setattr(os, "environ", environment() | {"PYTEST_ADDOPTS": "--confcutdir=/", "PYTEST_PLUGINS": "unexpected", "ARK_API_KEY": "SYNTHETIC_SECRET"})
    pytest_main = Mock(return_value=0)
    monkeypatch.setattr(pytest, "main", pytest_main)
    assert runner.main(["--collect-only"]) == 0
    arguments = pytest_main.call_args.args[0]
    assert arguments[0] == str(runner.BACKEND / "tests/postgres_integration/test_package_play_postgres.py")
    assert "--collect-only" in arguments and "--tb=short" in arguments
    assert os.environ["ENABLE_PAID_MODEL_CALLS"] == "false"
    assert all(name not in os.environ for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "ARK_API_KEY"))


def test_package_play_postgres_guard_migration_chain_compiles_offline():
    from tests.postgres_integration.test_package_play_postgres import migration_modules
    modules = migration_modules()
    assert [module.revision for module in modules] == [
        "j0d1e2f3a4b5", "k1d2e3f4a5b6", "l2e3f4a5b6c7", "m3f4a5b6c7d8", "n4a5b6c7d8e9", "o5b6c7d8e9f0"]
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        for module in modules:
            module.upgrade()
        for module in reversed(modules):
            module.downgrade()
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 13 and sql.count("DROP TABLE") == 13
    assert "ALTER TABLE" not in sql and "public." not in sql
    assert "ck_package_play_event_kind" in sql and "uq_package_play_event_key" in sql
