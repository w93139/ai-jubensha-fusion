"""Offline regression tests; no application startup, dotenv or database engine.

Run with --confcutdir=tests/fusion_security to avoid the legacy conftest's
production application import. Production modules are loaded with package
initializers bypassed and authentication/database integration stubbed.
"""

import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


BACKEND = Path(__file__).resolve().parents[2]
SRC = BACKEND / "src"


@pytest.fixture
def isolated_modules(monkeypatch):
    original_names = set(sys.modules)
    # Do not execute src.db.__init__ (it imports the real connection manager),
    # src.schemas.__init__, or any application/configuration initializer.
    for package in ("src", "src.core", "src.db", "src.db.models", "src.api",
                    "src.api.routes", "src.db.repositories", "src.schemas", "src.services"):
        module = ModuleType(package)
        module.__path__ = [str(BACKEND.joinpath(*package.split(".")))]
        monkeypatch.setitem(sys.modules, package, module)

    def forbidden(*args, **kwargs):
        pytest.fail("A security unit test attempted real configuration or database access")

    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = forbidden
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    try:
        yield monkeypatch, forbidden
    finally:
        for name in set(sys.modules) - original_names:
            if name == "src" or name.startswith("src."):
                sys.modules.pop(name, None)


@pytest.fixture
def auth_module(isolated_modules):
    monkeypatch, forbidden = isolated_modules
    session = ModuleType("src.db.session")
    session.get_db_session = forbidden
    auth = ModuleType("src.services.auth_service")
    auth.AuthService = SimpleNamespace(get_user_from_token=forbidden)
    users = ModuleType("src.db.models.user")
    users.User = SimpleNamespace
    for module in (session, auth, users):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return importlib.import_module("src.core.auth_middleware")


@pytest.fixture
def auth_client(auth_module):
    identities = {
        "player": SimpleNamespace(username="player", is_active=True, is_admin=False),
        "admin": SimpleNamespace(username="admin", is_active=True, is_admin=True),
        "disabled-admin": SimpleNamespace(username="admin", is_active=False, is_admin=True),
    }

    class OfflineAuthMiddleware(auth_module.UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return identities.get(token)

    app = FastAPI()
    app.add_middleware(OfflineAuthMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def protected_handler(path: str, request: Request):
        return {"reached_handler": True}

    with TestClient(app) as client:
        yield client


EDITOR_PATHS = [
    "/api/characters/7/characters",
    "/api/characters/7/characters/8",
    "/api/evidence/7/evidence/8",
    "/api/locations/7/locations/8",
    "/api/script-editor/script/7/editing-context",
    "/api/script-editor/execute-instruction",
    "/api/scripts/7",
    "/api/scripts/7/info",
    "/api/scripts/7/status",
    "/api/scripts/complete",
    "/api/scripts/",
    "/api/scripts",
    "/api/scripts/public/private-details",
    "/api/scripts/search-extra",
]

LEGACY_DEBUG_PATHS = [
    "/api/users/game-history",
    "/api/users/game-history/",
    "/api/users/game-history/game-7",
    "/api/users/game-history/game-7/events",
    "/api/users/game-history/game-7/resume",
    "/api/game",
    "/api/game/status",
    "/api/game/start",
    "/api/game/reset",
    "/api/game/sessions",
]


@pytest.mark.parametrize("path", EDITOR_PATHS + LEGACY_DEBUG_PATHS)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_players_cannot_reach_legacy_editor_or_debug_handlers(auth_client, path, method):
    response = auth_client.request(method, path, headers={"Authorization": "Bearer player"})
    assert response.status_code == 403
    assert "reached_handler" not in response.json()


@pytest.mark.parametrize("token,expected", [(None, 401), ("invalid", 401), ("disabled-admin", 403)])
def test_editor_requires_active_admin(auth_client, token, expected):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert auth_client.get("/api/characters/7/characters", headers=headers).status_code == expected


@pytest.mark.parametrize("path", EDITOR_PATHS[:12] + LEGACY_DEBUG_PATHS)
def test_admin_keeps_editor_and_legacy_debug_access(auth_client, path):
    assert auth_client.get(path, headers={"Authorization": "Bearer admin"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/scripts/public", "/api/scripts/search", "/api/scripts/public/", "/api/scripts/search/"])
def test_only_exact_catalog_get_is_public(auth_client, path):
    assert auth_client.get(path).status_code == 200
    assert auth_client.post(path, headers={"Authorization": "Bearer player"}).status_code == 403


@pytest.mark.parametrize("path", ["/api/fusion/scripts", "/api/fusion/sessions/game-7", "/api/fusion/sessions/game-7/events"])
def test_fusion_player_routes_remain_authenticated_not_admin(auth_client, path):
    assert auth_client.get(path).status_code == 401
    assert auth_client.get(path, headers={"Authorization": "Bearer player"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/users/profile", "/api/users/game-history-extra"])
def test_legacy_history_admin_rule_does_not_capture_other_user_routes(auth_client, path):
    assert auth_client.get(path, headers={"Authorization": "Bearer player"}).status_code == 200


@pytest.fixture
def repository_module(isolated_modules):
    return importlib.import_module("src.db.repositories.script_repository")


@pytest.mark.parametrize("operation,changes", [
    (operation, changes)
    for operation in ("create_script", "create_complete_script", "update_script_info",
                      "update_complete_script", "update_script_status", "update_script_info_fields")
    for changes in ({"status": "PUBLISHED"}, {"is_public": True})
    if operation != "update_script_status" or "status" in changes
])
def test_repository_denies_legacy_publication_before_any_db_access(repository_module, operation, changes):
    policy = importlib.import_module("src.core.script_authoring_policy")
    db = Mock()
    repo = repository_module.ScriptRepository(db)
    info = SimpleNamespace(model_dump=lambda **kwargs: changes)
    script = SimpleNamespace(info=info)
    calls = {
        "create_script": lambda: repo.create_script(info),
        "create_complete_script": lambda: repo.create_complete_script(script),
        "update_script_info": lambda: repo.update_script_info(7, info),
        "update_complete_script": lambda: repo.update_complete_script(7, script),
        "update_script_status": lambda: repo.update_script_status(7, repository_module.ScriptStatus.PUBLISHED),
        "update_script_info_fields": lambda: repo.update_script_info_fields(7, changes),
    }
    with pytest.raises(policy.LegacyPublicationDenied):
        calls[operation]()
    db.query.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_nonpublic_metadata_remains_editable(isolated_modules):
    policy = importlib.import_module("src.core.script_authoring_policy")
    policy.reject_legacy_publication({"title": "synthetic fixture", "status": "DRAFT", "is_public": False})
    policy.reject_legacy_publication({"status": "REVIEW"})


def test_review_state_roundtrips_through_api_schema(repository_module):
    schema = repository_module.ScriptInfo.model_validate({"status": "REVIEW"})
    assert schema.model_dump()["status"] == "REVIEW"


@pytest.fixture
def script_routes(auth_module, repository_module, isolated_modules):
    monkeypatch, _ = isolated_modules
    integration = ModuleType("src.core.container_integration")
    for name in ("get_script_repo_depends", "get_script_editor_svc_depends", "get_script_generation_svc_depends"):
        setattr(integration, name, lambda: Depends(lambda: None))
    generation = ModuleType("src.services.script_generation_service")
    generation.ScriptGenerationService = type("ScriptGenerationService", (), {})
    monkeypatch.setitem(sys.modules, integration.__name__, integration)
    monkeypatch.setitem(sys.modules, generation.__name__, generation)
    return importlib.import_module("src.api.routes.script_routes")


@pytest.mark.parametrize("operation", ["create_complete_script", "update_script", "update_script_info", "update_script_status"])
def test_routes_report_publication_conflict_without_repository_calls(script_routes, operation):
    info = script_routes.ScriptInfo(status=script_routes.ScriptStatus.PUBLISHED, is_public=True)
    script = script_routes.Script(info=info)
    user = SimpleNamespace(username="admin", is_admin=True, is_active=True)
    repo = Mock()
    calls = {
        "create_complete_script": lambda: script_routes.create_complete_script(script, user, repo),
        "update_script": lambda: script_routes.update_script(7, script, user, repo),
        "update_script_info": lambda: script_routes.update_script_info(7, info, Mock(), user, repo),
        "update_script_status": lambda: script_routes.update_script_status(7, script_routes.ScriptStatus.PUBLISHED, user, repo),
    }
    with pytest.raises(HTTPException) as raised:
        asyncio.run(calls[operation]())
    assert raised.value.status_code == 409
    assert repo.mock_calls == []


@pytest.mark.parametrize("search", [False, True])
def test_public_queries_filter_before_count_and_pagination(repository_module, search):
    db = Mock()
    query = Mock()
    db.query.return_value = query
    for method in ("filter", "order_by", "offset", "limit"):
        getattr(query, method).return_value = query
    query.count.return_value = 0
    query.all.return_value = []
    repo = repository_module.ScriptRepository(db)
    if search:
        result = repo.search_scripts("synthetic")
    else:
        result = repo.get_scripts_list(public_only=True)
    clauses = [str(clause.compile(compile_kwargs={"literal_binds": True}))
               for call in query.filter.call_args_list for clause in call.args]
    assert "scripts.status = 'PUBLISHED'" in clauses
    assert "scripts.is_public IS true" in clauses
    assert result.total == 0
    query.count.assert_called_once()
    assert [call[0] for call in query.mock_calls].index("filter") < [call[0] for call in query.mock_calls].index("count")


def test_catalog_route_requests_public_filter():
    # The repository default intentionally remains useful to the admin list;
    # verify the public route explicitly opts into the stricter query.
    import ast
    tree = ast.parse((SRC / "api/routes/script_routes.py").read_text())
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_public_scripts")
    calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "get_scripts_list"]
    assert len(calls) == 1
    assert any(keyword.arg == "public_only" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value is True for keyword in calls[0].keywords)
