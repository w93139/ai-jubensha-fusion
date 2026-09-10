from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import URL

from src.core import environment


def test_environment_loader_targets_repository_root_without_override(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    loader = Mock(return_value=True)
    monkeypatch.setattr(environment, "load_dotenv", loader)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)

    assert environment.load_project_environment(env_file) is True
    loader.assert_called_once_with(dotenv_path=env_file, override=False)
    assert environment.REPOSITORY_ENV_FILE == Path(__file__).resolve().parents[3] / ".env"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Y"])
def test_environment_loader_respects_disabled_flag(monkeypatch, value):
    loader = Mock()
    monkeypatch.setattr(environment, "load_dotenv", loader)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", value)

    assert environment.load_project_environment() is False
    loader.assert_not_called()


def test_database_manager_builds_structured_url_for_special_characters(monkeypatch):
    from src.db import session as session_module

    database_config = SimpleNamespace(
        username="local-user",
        password="p@ss:/?#[]% word",
        host="127.0.0.1",
        port=55432,
        database="local-db",
        pool_size=3,
    )
    create_engine = Mock(return_value=Mock())
    monkeypatch.setattr(session_module, "get_database_config", lambda: database_config)
    monkeypatch.setattr(session_module, "create_engine", create_engine)
    monkeypatch.setattr(session_module, "sessionmaker", Mock(return_value=Mock()))

    session_module.DatabaseManager().initialize()

    database_url = create_engine.call_args.args[0]
    assert isinstance(database_url, URL)
    assert database_url.username == database_config.username
    assert database_url.password == database_config.password
    assert database_url.database == database_config.database


def test_startup_propagates_database_initialization_failure(monkeypatch):
    from src.core.startup import initialize_application

    dependency_container = ModuleType("src.core.dependency_container")
    setattr(dependency_container, "configure_services", Mock())
    db_session = ModuleType("src.db.session")
    setattr(db_session, "init_database", Mock(side_effect=RuntimeError("database unavailable")))
    setattr(db_session, "get_db_session", Mock())
    config_module = ModuleType("src.core.config")
    setattr(config_module, "config", SimpleNamespace(allow_anonymous_access=False))

    monkeypatch.setitem(sys.modules, dependency_container.__name__, dependency_container)
    monkeypatch.setitem(sys.modules, db_session.__name__, db_session)
    monkeypatch.setitem(sys.modules, config_module.__name__, config_module)

    with pytest.raises(RuntimeError, match="database unavailable"):
        initialize_application()
