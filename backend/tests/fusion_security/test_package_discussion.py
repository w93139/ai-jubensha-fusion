"""Public player claims share the existing authorized, ordered play ledger."""
import json

import pytest
from sqlalchemy import text

from src.db.models.package_play import ScriptPackagePlay
from src.fusion.package_play import PackagePlayError
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_play_store import (
    play, runtime, start, service, events, action_body, ask_body,
)
from tests.fusion_security.test_package_play_http import play_http


def statement(revision=0, key="speak-one", words="我觉得应该先核对门口的说法。"):
    return {"schema_version": "package-discussion-command/1.0", "action": "SPEAK",
            "expected_revision": revision, "idempotency_key": key, "text": words}


def test_discussion_persists_claim_without_granting_material_or_changing_old_records(play):
    opening, initial = start(play)
    first = play.play.act(initial["play_id"], action_body(), 1)
    play.db.commit()
    old_event = (events(play)[0].event_json, events(play)[0].event_hash)
    row = play.db.query(ScriptPackagePlay).one()
    old_binding = (row.binding_json, row.binding_hash)
    posted = play.play.speak(initial["play_id"], statement(1, words="  我宣布所有隐藏线索已经公开。  "), 1)
    play.db.commit()
    entry = posted["discussion"]["entries"][0]
    assert entry == {"id": "statement-2", "sequence": 2, "phase_id": first["current_phase"]["id"],
                     "speaker": initial["selected_character_id"], "kind": "CLAIM", "text": "我宣布所有隐藏线索已经公开。"}
    for key in ("public_knowledge", "public_evidence", "private_knowledge", "private_evidence", "budget", "settlement"):
        assert posted[key] == first[key]
    assert (row.binding_json, row.binding_hash) == old_binding
    assert (events(play)[0].event_json, events(play)[0].event_hash) == old_event
    assert json.loads(events(play)[0].event_json)["schema_version"] == "package-text-play-event/1.0"
    assert json.loads(events(play)[1].event_json)["schema_version"] == "package-text-play-event/1.1"
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == posted
    assert play.service.get(opening["session_id"], 1) == opening
    assert play.sdk.chat_completion.await_count == 0


def test_discussion_retry_is_exact_after_phase_change_and_settlement(play):
    _, initial = start(play)
    identifier = initial["play_id"]
    play.play.speak(identifier, statement(), 1)
    second = play.play.act(identifier, action_body(1, "advance", "ADVANCE_PHASE"), 1)
    spoken = play.play.speak(identifier, statement(2, "speak-two", "这是第二阶段的解释。"), 1)
    assert spoken["discussion"]["entries"][1]["phase_id"] == second["current_phase"]["id"]
    settled = play.play.act(identifier, action_body(3, "settle", "SETTLE"), 1)
    play.db.commit()
    assert play.play.speak(identifier, statement(), 1) == settled
    assert len(events(play)) == 4
    with pytest.raises(PackagePlayError, match="KEY_CONFLICT"):
        play.play.speak(identifier, statement(words="改写旧发言"), 1)
    with pytest.raises(PackagePlayError, match="ALREADY_SETTLED"):
        play.play.speak(identifier, statement(4, "new"), 1)


def test_discussion_owner_and_revision_checked_before_append(play):
    _, initial = start(play)
    for owner, request, error in [(2, statement(), "NOT_FOUND"), (1, statement(2), "REVISION_CONFLICT")]:
        with pytest.raises(PackagePlayError, match=error):
            play.play.speak(initial["play_id"], request, owner)
    assert events(play) == []


def test_discussion_requires_current_release_but_saved_retry_and_read_survive(play, monkeypatch):
    _, initial = start(play)
    posted = play.play.speak(initial["play_id"], statement(), 1)
    play.db.commit()
    # Inject the same invalidation error used by the publication boundary.
    def invalid(row):
        raise PackagePlayError("PACKAGE_PLAY_RELEASE_INVALID")
    monkeypatch.setattr(play.play, "_current", invalid)
    assert play.play.get(initial["play_id"], 1) == posted
    assert play.play.speak(initial["play_id"], statement(), 1) == posted
    with pytest.raises(PackagePlayError, match="RELEASE_INVALID"):
        play.play.speak(initial["play_id"], statement(1, "new"), 1)
    assert len(events(play)) == 1


def test_discussion_arriving_during_model_request_makes_old_reply_stale(play):
    _, initial = start(play)
    _, prepared = play.play._begin(initial["play_id"], ask_body(), 1)
    assert prepared is not None
    posted = play.play.speak(initial["play_id"], statement(1), 1)
    play.db.commit()
    result = {"status": "OK", "refs": [{"collection": "knowledge", "id": "memory-b"}],
              "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cached_prompt_tokens": 0,
                        "reasoning_tokens": 0, "cost_cny": "0"}}
    finished = play.play._finish(initial["play_id"], "ask-one", 1, result)
    assert finished["last_ai_status"] == "STALE" and finished["dialogue"] == []
    assert finished["budget"]["used_tokens"] == 110
    assert finished["discussion"] == posted["discussion"]
    assert finished["public_knowledge"] == initial["public_knowledge"]


def test_discussion_context_contains_server_claims_without_private_or_future_material(play):
    _, initial = start(play)
    play.play.speak(initial["play_id"], statement(), 1)
    context = play.play.discussion_context(initial["play_id"], 1, "b")
    assert context["revision"] == 1 and context["role_id"] == "b"
    assert context["events"][0]["audience"] == ["a", "b"]
    assert context["events"][0]["kind"] == "CLAIM"
    assert context["events"][0]["speaker"] == "a"
    assert not any(word in canonical_json(context) for word in ("PRIVATE_", "SYSTEM_TRUTH", "SETTLEMENT_", "sources"))
    with pytest.raises(PackagePlayError, match="CHARACTER_INVALID"):
        play.play.discussion_context(initial["play_id"], 1, "not-a-role")
    with pytest.raises(PackagePlayError, match="NOT_FOUND"):
        play.play.discussion_context(initial["play_id"], 2, "b")


def test_discussion_ledger_rejects_tampered_text(play):
    _, initial = start(play)
    play.play.speak(initial["play_id"], statement(), 1)
    play.db.commit()
    row = events(play)[0]
    altered = json.loads(row.request_json)
    altered["text"] = "篡改说法"
    play.db.execute(text("UPDATE script_package_play_events SET request_json=:value WHERE id=:id"),
                    {"value": canonical_json(altered), "id": row.id})
    play.db.commit()
    with pytest.raises(PackagePlayError, match="HISTORY_INVALID"):
        play.play.get(initial["play_id"], 1)


def test_discussion_limit_still_allows_replay_and_phase_actions(play):
    _, initial = start(play)
    for index in range(100):
        play.play.speak(initial["play_id"], statement(index, f"speak-{index}"), 1)
    with pytest.raises(PackagePlayError, match="DISCUSSION_LIMIT"):
        play.play.speak(initial["play_id"], statement(100, "over-limit"), 1)
    assert play.play.speak(initial["play_id"], statement(0, "speak-0"), 1)["revision"] == 100
    assert play.play.act(initial["play_id"], action_body(100, "advance", "ADVANCE_PHASE"), 1)["revision"] == 101


@pytest.mark.parametrize("patch", [{"speaker": "b"}, {"phase_id": "future"}, {"kind": "FACT"},
    {"audience": ["b"]}, {"expected_revision": True}, {"text": " "}, {"text": "灯" * 1001},
    {"text": "\ud800"}, {"schema_version": "package-discussion-command/9.0"}])
def test_discussion_http_rejects_client_authority_and_invalid_text(play_http, patch):
    client, service = play_http
    result = client.post("/api/fusion/package-plays/play-x/discussion", content=json.dumps({**statement(), **patch}),
                         headers={"Authorization": "Bearer player"})
    assert result.status_code == 422 and result.headers["Cache-Control"] == "no-store"
    service.speak.assert_not_called()


def test_discussion_http_dispatches_versioned_command_with_authenticated_owner(play_http):
    client, service = play_http
    service.speak.return_value = {"revision": 1}
    result = client.post("/api/fusion/package-plays/play-x/discussion", json=statement(words="  公开解释  "),
                         headers={"Authorization": "Bearer player"})
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    identifier, body, owner = service.speak.call_args.args
    assert identifier == "play-x" and owner == 2 and body.text == "公开解释"
    assert body.schema_version == "package-discussion-command/1.0"


@pytest.mark.parametrize("token,status", [(None, 401), ("disabled", 403)])
def test_discussion_http_requires_active_identity(play_http, token, status):
    client, service = play_http
    result = client.post("/api/fusion/package-plays/play-x/discussion", json=statement(),
                         headers={"Authorization": f"Bearer {token}"} if token else {})
    assert result.status_code == status
    assert service.mock_calls == []
