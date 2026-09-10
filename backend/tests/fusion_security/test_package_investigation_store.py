"""Real SQLite text-play services with fictional, action-gated v1.2 packages.

Approval is an explicit publisher test seam. Source and human-review semantics
are covered by their own suites; this module never invokes a model provider.
"""
import asyncio
from copy import deepcopy
import json

import pytest
from sqlalchemy import update

from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.fusion.package_play import PackagePlayError
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from tests.fusion_security.test_package_investigation import investigation_package
from tests.fusion_security.test_package_play_store import (
    action_body, ask_body, events, native_play, play, response, service, start,
)
from tests.fusion_security.test_package_flow import native_flow
from tests.fusion_security.test_package_runtime import opening_package, publish, request, runtime
from tests.fusion_security.test_script_packages import fictional_package_v11


def investigation_document(version="investigation-v1"):
    package = investigation_package(opening_package(version, factory=fictional_package_v11))
    package["mechanics"]["phase_budgets"][1]["advance_policy"] = "REQUIRE_EXHAUSTED"
    # An eligible AI reward is separate from the existing KEEP_PRIVATE reward.
    package["evidence"].append({
        "id": "b-action-card", "text": "B_ACTION_REWARD_SENTINEL", "visibility": "CHARACTER_PRIVATE",
        "character_id": "b", "disclosure": "MAY_SHARE",
        "release": {"phase_id": "opening", "required_action_ids": ["open-case"]},
        "sources": deepcopy(package["introduction"]["sources"]),
    })
    assert validate_package(package)["valid"], validate_package(package)
    return package


def perform_body(identifier="find-key", revision=0, key=None):
    return {"action": "PERFORM_ACTION", "target": {"action_id": identifier},
            "expected_revision": revision, "idempotency_key": key or identifier}


def perform(play, initial, identifier, revision=0, key=None):
    return play.play.act(initial["play_id"], perform_body(identifier, revision, key), 1)


def ids(view, collection):
    return {item["id"] for item in view[collection]}


def test_package_investigation_store_points_rewards_phase_budget_and_settlement(play):
    opening, initial = start(play, investigation_document())
    assert opening["supports_rules_preview"] is False
    assert "clock" not in ids(opening, "private_evidence")
    assert initial["mechanics"]["remaining_points"] == 3 and not initial["can_advance"]
    assert ids(initial["mechanics"], "available_actions") == {"find-key", "free-check"}
    assert "B_ACTION_REWARD_SENTINEL" not in canonical_json(initial)
    binding = json.loads(play.db.query(ScriptPackagePlay).one().binding_json)
    assert binding["rules_contract"] == "package-play-rules/1.1"
    key = perform(play, initial, "find-key")
    assert key["mechanics"]["spent_points"] == 1 and key["mechanics"]["remaining_points"] == 2
    assert "key" in ids(key, "public_evidence") and "clock" not in ids(key, "private_evidence")
    assert "open-case" in ids(key["mechanics"], "available_actions")
    reward = perform(play, initial, "open-case", 1)
    assert reward["mechanics"]["remaining_points"] == 0 and reward["can_advance"]
    assert "clock" in ids(reward, "private_evidence") and "clock" not in ids(reward, "public_evidence")
    assert "gated-public" not in ids(reward, "public_knowledge")
    assert all(secret not in canonical_json(reward) for secret in (
        "ACTION_GRANTED_OTHER_PRIVATE", "B_ACTION_REWARD_SENTINEL", "FUTURE_ACTION_MATERIAL", "SYSTEM_TRUTH_SENTINEL"))
    shared = play.play.act(initial["play_id"], action_body(2), 1)
    assert "gated-public" in ids(shared, "public_knowledge")
    assert next(item for item in shared["public_knowledge"] if item["id"] == "public-claim")["kind"] == "CLAIM"
    assert shared["mechanics"]["spent_points"] == 3
    advanced = play.play.act(initial["play_id"], action_body(3, "advance", "ADVANCE_PHASE"), 1)
    assert advanced["phase_complete"] and advanced["mechanics"]["initial_points"] == 1
    assert advanced["mechanics"]["remaining_points"] == 1 and advanced["mechanics"]["spent_points"] == 0
    assert "a-later" in ids(advanced, "private_knowledge") and "record" in ids(advanced, "public_evidence")
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_PHASE_BUDGET_REMAINS$"):
        play.play.act(initial["play_id"], action_body(4, "settle", "SETTLE"), 1)
    perform(play, initial, "next-search", 4)
    settled = play.play.act(initial["play_id"], action_body(5, "settle", "SETTLE"), 1)
    play.db.commit()
    assert settled["settled"] and settled["revision"] == 6
    assert settled["mechanics"]["remaining_points"] == 0 and settled["mechanics"]["available_actions"] == []
    assert [item["id"] for item in settled["settlement"]["truths"]] == ["answer"]
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == settled
    assert play.service.get(opening["session_id"], 1) == opening
    assert len(events(play)) == 6 and play.sdk.chat_completion.await_count == 0


def test_package_investigation_store_same_key_replays_without_debit_and_conflicting_key_fails(play):
    _, initial = start(play, investigation_document())
    first = perform(play, initial, "find-key")
    play.db.commit()
    assert perform(play, initial, "find-key") == first
    second = perform(play, initial, "open-case", 1)
    play.db.commit()
    play.publisher.current.clear()
    # Existing text-play requests return the current authorized historical view.
    assert perform(play, initial, "find-key") == second
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_KEY_CONFLICT$"):
        perform(play, initial, "free-check", key="find-key")
    assert len(events(play)) == 2 and second["mechanics"]["spent_points"] == 3


@pytest.mark.parametrize("identifier", ["open-case", "next-search", "PRIVATE_UNKNOWN_ACTION_SENTINEL"])
def test_package_investigation_store_locked_or_unknown_action_never_mutates(play, identifier):
    _, initial = start(play, investigation_document())
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$") as error:
        perform(play, initial, identifier)
    assert "PRIVATE" not in str(error.value)
    assert events(play) == [] and play.play.get(initial["play_id"], 1) == initial


def test_package_investigation_store_private_key_is_not_public_permission(play):
    document = investigation_document()
    key = next(item for item in document["evidence"] if item["id"] == "key")
    key.update(visibility="CHARACTER_PRIVATE", character_id="a", disclosure="MAY_SHARE")
    _, initial = start(play, document)
    held = perform(play, initial, "find-key")
    assert "key" in ids(held, "private_evidence") and "open-case" not in ids(held["mechanics"], "available_actions")
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        perform(play, initial, "open-case", 1)
    shared = play.play.act(initial["play_id"], action_body(1, "share-key", target={"collection": "evidence", "id": "key"}), 1)
    assert "open-case" in ids(shared["mechanics"], "available_actions")
    assert perform(play, initial, "open-case", 2)["mechanics"]["remaining_points"] == 0


def test_package_investigation_store_free_action_is_once_and_wrong_role_cannot_spend(play):
    document = investigation_document()
    publish(play, document)
    opening = play.service.create(request(character="b"), 1)
    initial = play.play.create({"opening_session_id": opening["session_id"], "idempotency_key": "b-play"}, 1)
    play.db.commit()
    perform(play, initial, "find-key")
    free = perform(play, initial, "free-check", 1)
    assert free["mechanics"]["remaining_points"] == 2
    assert "open-case" not in canonical_json(free)
    for identifier in ("open-case", "free-check"):
        with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
            perform(play, initial, identifier, 2, "repeat-" + identifier)
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_NOT_FOUND$"):
        play.play.act(initial["play_id"], perform_body("free-check", 2), 2)
    assert play.play.get(initial["play_id"], 1) == free and len(events(play)) == 2


def test_package_investigation_store_native_transaction_rollback_restores_points_and_retry(native_play):
    _, initial = start(native_play, investigation_document())
    perform(native_play, initial, "find-key")
    native_play.db.rollback()
    with native_play.factory() as db:
        reopened = service(native_play, db)
        assert reopened.get(initial["play_id"], 1) == initial
        assert db.query(ScriptPackagePlayEvent).count() == 0
        result = reopened.act(initial["play_id"], perform_body(), 1)
        db.commit()
    assert result["revision"] == 1 and result["mechanics"]["remaining_points"] == 2


def test_package_investigation_store_stale_revision_and_revoked_release_preserve_committed_action(play):
    _, initial = start(play, investigation_document())
    key = perform(play, initial, "find-key")
    play.db.commit()
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_REVISION_CONFLICT$"):
        perform(play, initial, "open-case", 0)
    play.db.rollback()
    play.publisher.current.clear()
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_RELEASE_UNAVAILABLE$"):
        perform(play, initial, "open-case", 1)
    assert play.play.get(initial["play_id"], 1) == key
    assert perform(play, initial, "find-key") == key and len(events(play)) == 1


def test_package_investigation_store_ai_context_excludes_rules_and_unearned_rewards(play):
    _, initial = start(play, investigation_document())
    contexts = []
    async def completion(messages, **params):
        contexts.append(json.loads(messages[1].content)["context"])
        return response()
    play.sdk.chat_completion.side_effect = completion
    first = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert first["last_ai_status"] == "OK" and first["mechanics"]["spent_points"] == 0
    perform(play, initial, "find-key", 2)
    perform(play, initial, "open-case", 3)
    second = asyncio.run(play.play.ask(initial["play_id"], ask_body(4, "ask-after-search"), 1))
    assert second["last_ai_status"] == "OK" and second["mechanics"]["spent_points"] == 3
    assert "B_ACTION_REWARD_SENTINEL" not in canonical_json(contexts[0])
    assert "B_ACTION_REWARD_SENTINEL" in canonical_json(contexts[1])
    for context in contexts:
        assert set(context) == {"character", "current_phase", "materials"}
        raw = canonical_json(context)
        for forbidden in ("mechanics", "find-key", "open-case", "next-search", "cost", "remaining_points",
                          "ACTION_GRANTED_OTHER_PRIVATE", "FUTURE_ACTION_MATERIAL", "PRIVATE_a_SENTINEL",
                          "SYSTEM_TRUTH_SENTINEL", "sources", "original_source_ids"):
            assert forbidden not in raw
    assert second["budget"]["used_tokens"] == 220 and play.sdk.chat_completion.await_count == 2


def test_package_investigation_store_legacy_and_new_rules_bindings_replay_without_rehash(play):
    _, legacy = start(play, opening_package("old-v11", factory=fictional_package_v11))
    legacy = play.play.act(legacy["play_id"], action_body(), 1)
    play.db.commit()
    release = publish(play, investigation_document("new-v12"))
    opening = play.service.create(request(release["id"], key="new-opening"), 1)
    current = play.play.create({"opening_session_id": opening["session_id"], "idempotency_key": "new-play"}, 1)
    current = perform(play, current, "find-key")
    play.db.commit()
    rows = play.db.query(ScriptPackagePlay).order_by(ScriptPackagePlay.id).all()
    before = [(row.binding_json, row.binding_hash) for row in rows]
    before_events = [(row.event_json, row.event_hash, row.state_hash) for row in events(play)]
    assert [json.loads(row.binding_json)["rules_contract"] for row in rows] == ["package-play-rules/1.0", "package-play-rules/1.1"]
    with play.factory() as db:
        assert service(play, db).get(legacy["play_id"], 1) == legacy
        assert service(play, db).get(current["play_id"], 1) == current
        assert "mechanics" not in legacy and current["mechanics"]["spent_points"] == 1
    assert before == [(row.binding_json, row.binding_hash) for row in play.db.query(ScriptPackagePlay).order_by(ScriptPackagePlay.id).populate_existing()]
    assert before_events == [(row.event_json, row.event_hash, row.state_hash) for row in events(play)]
    assert all(content_hash(json.loads(raw)) == digest for raw, digest in before)


def test_package_investigation_store_rehashed_history_does_not_grant_locked_action(play):
    _, initial = start(play, investigation_document())
    perform(play, initial, "find-key")
    play.db.commit()
    row = events(play)[0]
    request = json.loads(row.request_json)
    request["target"]["action_id"] = "open-case"
    payload = json.loads(row.event_json)
    payload["request_hash"] = content_hash(request)
    play.db.execute(update(ScriptPackagePlayEvent).where(ScriptPackagePlayEvent.id == row.id).values(
        request_json=canonical_json(request), request_hash=content_hash(request),
        event_json=canonical_json(payload), event_hash=content_hash(payload)))
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_HISTORY_INVALID$"):
        play.play.get(initial["play_id"], 1)
    assert play.sdk.chat_completion.await_count == 0


def test_package_investigation_store_rule_contract_cannot_downgrade_even_with_new_hash(play):
    _, initial = start(play, investigation_document())
    row = play.db.query(ScriptPackagePlay).one()
    binding = json.loads(row.binding_json)
    binding["rules_contract"] = "package-play-rules/1.0"
    play.db.execute(update(ScriptPackagePlay).where(ScriptPackagePlay.id == row.id).values(
        binding_json=canonical_json(binding), binding_hash=content_hash(binding)))
    with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_SNAPSHOT_INVALID$"):
        play.play.get(initial["play_id"], 1)


def test_package_investigation_store_search_during_model_wait_spends_points_but_stales_reply(play):
    _, initial = start(play, investigation_document())
    async def completion(messages, **params):
        with play.factory.begin() as db:
            changed = service(play, db).act(initial["play_id"], perform_body(revision=1), 1)
            assert changed["revision"] == 2 and changed["mechanics"]["remaining_points"] == 2
        return response()
    play.sdk.chat_completion.side_effect = completion
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert result["revision"] == 3 and result["last_ai_status"] == "STALE"
    assert result["dialogue"] == [] and result["mechanics"]["spent_points"] == 1
    assert result["budget"]["used_tokens"] == 110 and result["budget"]["reserved_tokens"] == 0
    assert "key" in ids(result, "public_evidence") and "memory-b" not in ids(result, "public_knowledge")
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == result
    assert [event.kind for event in events(play)] == ["AI_REQUEST", "ACTION", "AI_RESULT"]
    assert play.sdk.chat_completion.await_count == 1
