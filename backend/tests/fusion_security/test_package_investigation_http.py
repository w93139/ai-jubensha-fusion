"""Authenticated v1.2 routes using real services and isolated SQLite sessions."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.routes import package_play_routes as routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.models.package_play import ScriptPackagePlayEvent
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_investigation_store import investigation_document, perform_body
from tests.fusion_security.test_package_play_store import action_body, play, service
from tests.fusion_security.test_package_runtime import publish, request, runtime


BASE = "/api/fusion/package-plays"
OWNER = {"Authorization": "Bearer owner"}


@pytest.fixture
def investigation_http(play):
    publish(play, investigation_document())
    opening = play.service.create(request(), 1)
    play.db.commit()
    users = {
        "owner": SimpleNamespace(id=1, is_active=True, is_admin=False),
        "other": SimpleNamespace(id=2, is_active=True, is_admin=False),
        "admin": SimpleNamespace(id=2, is_active=True, is_admin=True),
        "disabled": SimpleNamespace(id=2, is_active=False, is_admin=False),
    }

    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return users.get(token)

    def real_service():
        with play.factory() as db:
            try:
                yield service(play, db)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    app = FastAPI()
    app.add_middleware(Auth)
    app.include_router(routes.router)
    app.dependency_overrides[routes.play_service] = real_service
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, play=play, opening=opening)


def create(case):
    response = case.client.post(BASE, json={"opening_session_id": case.opening["session_id"],
                                         "idempotency_key": "http-investigation"}, headers=OWNER)
    assert response.status_code == 201 and response.headers["Cache-Control"] == "no-store"
    return response.json()["data"]


def act(case, initial, body):
    return case.client.post(BASE + "/" + initial["play_id"] + "/actions", json=body, headers=OWNER)


def count_events(case):
    with case.play.factory() as db:
        return db.query(ScriptPackagePlayEvent).count()


def test_package_investigation_http_real_debit_unlock_and_explicit_end(investigation_http):
    case = investigation_http
    initial = create(case)
    assert initial["mechanics"]["remaining_points"] == 3 and not initial["can_advance"]
    assert not case.opening["supports_rules_preview"]
    blocked = act(case, initial, action_body(0, "advance", "ADVANCE_PHASE"))
    assert blocked.status_code == 409 and blocked.json() == {"detail": "PACKAGE_PLAY_PHASE_BUDGET_REMAINS"}
    assert count_events(case) == 0
    first = act(case, initial, perform_body()).json()["data"]
    assert first["mechanics"]["remaining_points"] == 2
    assert any(item["id"] == "key" for item in first["public_evidence"])
    second = act(case, initial, perform_body("open-case", 1)).json()["data"]
    assert second["mechanics"]["remaining_points"] == 0 and second["can_advance"]
    assert any(item["id"] == "clock" for item in second["private_evidence"])
    for secret in ("ACTION_GRANTED_OTHER_PRIVATE", "B_ACTION_REWARD_SENTINEL", "SYSTEM_TRUTH_SENTINEL"):
        assert secret not in canonical_json(second)
    ending = act(case, initial, action_body(2, "advance", "ADVANCE_PHASE")).json()["data"]
    assert ending["phase_complete"] and not ending["mechanics"]["can_finish_phase"]
    blocked = act(case, initial, action_body(3, "settle", "SETTLE"))
    assert blocked.status_code == 409 and blocked.json() == {"detail": "PACKAGE_PLAY_PHASE_BUDGET_REMAINS"}
    assert act(case, initial, perform_body("next-search", 3)).status_code == 200
    response = act(case, initial, action_body(4, "settle", "SETTLE"))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    settled = response.json()["data"]
    assert settled["settled"] and settled["revision"] == 5
    assert [item["id"] for item in settled["settlement"]["truths"]] == ["answer"]
    reloaded = case.client.get(BASE + "/" + initial["play_id"], headers=OWNER)
    assert reloaded.json()["data"] == settled and reloaded.headers["Cache-Control"] == "no-store"
    assert count_events(case) == 5 and case.play.sdk.chat_completion.await_count == 0


@pytest.mark.parametrize("patch", [
    {"target": {"action_id": "find-key", "cost": 0}},
    {"target": {"action_id": "find-key", "reward": "PRIVATE_REWARD_SENTINEL"}},
    {"target": {"collection": "evidence", "id": "clock"}},
    {"actor_character_id": "b"},
    {"expected_revision": True},
])
def test_package_investigation_http_rejects_client_authority_without_writes(investigation_http, patch):
    case = investigation_http
    initial = create(case)
    response = act(case, initial, perform_body() | patch)
    assert response.status_code == 422 and response.json() == {"detail": "PACKAGE_PLAY_REQUEST_INVALID"}
    assert response.headers["Cache-Control"] == "no-store" and "PRIVATE_REWARD" not in response.text
    assert count_events(case) == 0


@pytest.mark.parametrize("token,status", [(None, 401), ("disabled", 403), ("other", 404), ("admin", 404)])
def test_package_investigation_http_owner_only_for_real_reads_and_actions(investigation_http, token, status):
    case = investigation_http
    initial = create(case)
    headers = {"Authorization": "Bearer " + token} if token else {}
    url = BASE + "/" + initial["play_id"]
    for response in (case.client.get(url, headers=headers),
                     case.client.post(url + "/actions", json=perform_body(), headers=headers)):
        assert response.status_code == status
        assert all(secret not in response.text for secret in ("PRIVATE_a", "PUBLIC_SHARED_KEY", "find-key", "clock"))
    assert count_events(case) == 0


def test_package_investigation_http_safe_unavailable_retry_and_revocation(investigation_http):
    case = investigation_http
    initial = create(case)
    unknown = act(case, initial, perform_body("PRIVATE_UNKNOWN_ACTION_SENTINEL"))
    assert unknown.status_code == 409 and unknown.json() == {"detail": "PACKAGE_PLAY_ACTION_NOT_AVAILABLE"}
    locked = act(case, initial, perform_body("open-case"))
    assert locked.status_code == 409 and locked.json() == unknown.json()
    assert count_events(case) == 0
    first = act(case, initial, perform_body()).json()["data"]
    assert act(case, initial, perform_body()).json()["data"] == first
    stale = act(case, initial, perform_body("open-case", 0))
    assert stale.status_code == 409 and stale.json() == {"detail": "PACKAGE_PLAY_REVISION_CONFLICT"}
    case.play.publisher.current.clear()
    revoked = act(case, initial, perform_body("open-case", 1))
    assert revoked.status_code == 409 and revoked.json() == {"detail": "PACKAGE_PLAY_RELEASE_UNAVAILABLE"}
    assert "PRIVATE_PUBLISHER" not in revoked.text
    assert act(case, initial, perform_body()).json()["data"] == first
    assert case.client.get(BASE + "/" + initial["play_id"], headers=OWNER).json()["data"] == first
    assert count_events(case) == 1 and case.play.sdk.chat_completion.await_count == 0
