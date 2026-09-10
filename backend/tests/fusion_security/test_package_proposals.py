"""Fictional role goals, real isolated persistence and one-shot mock SDK."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import pytest

from src.db.models.package_play import ScriptPackagePlay
from src.fusion.package_play import PackagePlayError
from src.fusion.package_proposal_model import PackageProposalModel, validate_proposal
from src.fusion.package_role_model import PackageRoleModelError
from src.fusion.package_validation import canonical_json, content_hash
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_package_play_store import play, runtime, start, service, events, ask_body
from tests.fusion_security.test_package_investigation_store import investigation_document, perform_body
from tests.fusion_security.test_package_discussion import statement
from tests.fusion_security.test_package_play_http import play_http


def command(revision=0, key="proposal-one", character="b"):
    return {"schema_version": "package-investigation-command/1.0", "action": "PROPOSE",
            "expected_revision": revision, "idempotency_key": key, "character_id": character}


def answer(action="find-key", basis=None, **kwargs):
    return LLMResponse(content=canonical_json({"action_id": action, "public_basis": basis or []}),
        model="doubao-seed-character-260628", finish_reason="stop",
        usage={"prompt_tokens": 180, "completion_tokens": 20}, **kwargs)


def setup(play):
    document = investigation_document()
    document["knowledge"].append({"id": "b-goal", "text": "你的当前目标是保护信件，优先核对共享标记。OWN_GOAL_SECRET",
        "kind": "FACT", "visibility": "CHARACTER_PRIVATE", "character_id": "b", "disclosure": "KEEP_PRIVATE",
        "sources": deepcopy(document["introduction"]["sources"]), "release": {"phase_id": "opening"}})
    play.sdk.chat_completion.return_value = answer()
    return start(play, document)[1]


def projected(play, initial):
    row = play.play._row(initial["play_id"], 1)
    package, binding = play.play._resolve(row)
    state = play.play._replay(row, package, binding)
    return play.play._proposal_context(state, binding, "b")


def test_role_goal_projection_is_private_phase_filtered_and_choices_are_jointly_visible(play):
    initial = setup(play)
    context = projected(play, initial)
    raw = canonical_json(context)
    assert "OWN_GOAL_SECRET" in raw
    for hidden in ("PRIVATE_a_SENTINEL", "SYSTEM_TRUTH_SENTINEL", "ACTION_GRANTED_OTHER_PRIVATE", "FUTURE_ACTION_MATERIAL", "B_ACTION_REWARD_SENTINEL", "sources"):
        assert hidden not in raw
    assert {item["id"] for item in context["options"]} == {"find-key", "free-check"}
    assert "OWN_GOAL_SECRET" not in canonical_json(initial)
    assert "OWN_GOAL_SECRET" not in canonical_json(play.play.get(initial["play_id"], 1))
    assert "OWN_GOAL_SECRET" not in canonical_json(play.play._replay(
        play.play._row(initial["play_id"], 1), *play.play._resolve(play.play._row(initial["play_id"], 1))).engine.role_context("b"))


def test_proposal_saves_once_after_durable_reservation_without_spending_investigation_points(play):
    initial = setup(play)
    original_binding = play.db.query(ScriptPackagePlay).one().binding_json
    async def choose(messages, **params):
        assert not play.db.in_transaction()
        with play.factory() as db:
            stored = service(play, db).get(initial["play_id"], 1)
            assert stored["revision"] == 1 and stored["budget"]["reserved_tokens"] > 0
        return answer("free-check")
    play.sdk.chat_completion.side_effect = choose
    result = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert result["last_ai_status"] == "OK" and result["revision"] == 2
    proposal = result["investigation_proposals"]["entries"][0]
    assert proposal["action"]["id"] == "free-check" and proposal["kind"] == "CLAIM"
    assert result["mechanics"] == initial["mechanics"] and result["public_knowledge"] == initial["public_knowledge"]
    assert "OWN_GOAL_SECRET" not in canonical_json(result)
    assert play.db.query(ScriptPackagePlay).one().binding_json == original_binding
    assert all(json.loads(item.event_json)["schema_version"] == "package-text-play-event/1.2" for item in events(play))
    assert result["budget"]["used_tokens"] == 200
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == result
    assert asyncio.run(play.play.propose(initial["play_id"], command(), 1)) == result
    assert play.sdk.chat_completion.await_count == 1


def test_mocked_choice_can_change_after_public_claim_while_claim_remains_unverified(play):
    initial = setup(play)
    async def choose(messages, **params):
        context = json.loads(messages[1].content)["context"]
        human = [item for item in context["discussion"] if item["speaker"] == "a"]
        return answer("free-check" if human else "find-key", [{"collection": "discussion", "id": human[-1]["id"]}] if human else [])
    play.sdk.chat_completion.side_effect = choose
    first = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    play.play.speak(initial["play_id"], statement(2, words="我听说共享标记需要核对。"), 1)
    play.db.commit()
    second = asyncio.run(play.play.propose(initial["play_id"], command(3, "second"), 1))
    entries = second["investigation_proposals"]["entries"]
    assert first["investigation_proposals"]["entries"][0]["action"]["id"] == "find-key"
    assert entries[1]["action"]["id"] == "free-check"
    assert entries[1]["basis"][0]["kind"] == "CLAIM" and entries[1]["basis"][0]["speaker"] == "a"
    assert second["public_knowledge"] == initial["public_knowledge"]
    assert play.sdk.chat_completion.await_count == 2  # Routing test, not model intelligence evidence.


@pytest.mark.parametrize("action,basis", [("open-case", []), ("not-a-place", []),
    ("find-key", [{"collection": "knowledge", "id": "b-goal"}]),
    ("find-key", [{"collection": "discussion", "id": "statement-999"}]),
    ("find-key", [{"collection": "knowledge", "id": "b-private"}])])
def test_model_cannot_publish_private_basis_or_choose_hidden_actions(play, action, basis):
    initial = setup(play)
    play.sdk.chat_completion.return_value = answer(action, basis)
    result = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert result["last_ai_status"] == "INVALID"
    assert result["investigation_proposals"]["entries"] == []
    assert result["budget"]["used_tokens"] == 200
    assert result["mechanics"] == initial["mechanics"]


@pytest.mark.parametrize("change", ["speak", "investigate", "release"])
def test_new_events_or_invalid_release_make_late_proposal_stale(play, change):
    initial = setup(play)
    async def late(messages, **params):
        if change == "speak":
            play.play.speak(initial["play_id"], statement(1), 1)
            play.db.commit()
        elif change == "investigate":
            play.play.act(initial["play_id"], perform_body("find-key", 1), 1)
            play.db.commit()
        else:
            play.publisher.current.clear()
        return answer()
    play.sdk.chat_completion.side_effect = late
    result = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert result["last_ai_status"] == "STALE" and result["investigation_proposals"]["entries"] == []
    assert result["budget"]["used_tokens"] == 200


def test_unknown_and_expired_proposals_never_resend_and_share_busy_state_with_ask(play):
    initial = setup(play)
    _, prepared = play.play._begin(initial["play_id"], command(), 1)
    assert prepared
    repeated = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert repeated["pending_ai"]
    with pytest.raises(PackagePlayError, match="AI_BUSY"):
        asyncio.run(play.play.ask(initial["play_id"], ask_body(1), 1))
    play.clock[0] += 400
    expired = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert expired["last_ai_status"] == "EXPIRED"
    assert expired["budget"]["used_tokens"] > 0 and not expired["pending_ai"]
    assert play.sdk.chat_completion.await_count == 0
    play.sdk.chat_completion.side_effect = TimeoutError("PRIVATE_ERROR")
    unknown = asyncio.run(play.play.propose(initial["play_id"], command(2, "new"), 1))
    assert unknown["last_ai_status"] == "UNKNOWN"
    assert asyncio.run(play.play.propose(initial["play_id"], command(2, "new"), 1)) == unknown
    assert play.sdk.chat_completion.await_count == 1


def test_proposal_owner_target_revision_and_config_are_checked_without_model_calls(play):
    initial = setup(play)
    for owner, body, error in [(2, command(), "NOT_FOUND"), (1, command(character="a"), "CHARACTER_INVALID"),
        (1, command(character="unknown"), "CHARACTER_NOT_FOUND"), (1, command(7), "REVISION_CONFLICT")]:
        with pytest.raises(PackagePlayError, match=error):
            asyncio.run(play.play.propose(initial["play_id"], body, owner))
    play.play.proposal_model.settings = replace(play.model.settings, temperature=1)
    with pytest.raises(PackagePlayError, match="AI_UNAVAILABLE"):
        asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert events(play) == [] and play.sdk.chat_completion.await_count == 0


def test_proposal_size_budget_includes_schema_and_rejects_oversized_context_without_sdk(play):
    initial = setup(play)
    context = projected(play, initial)
    model = play.play.proposal_model
    prepared = model.prepare(context)
    expected = len(canonical_json(prepared["messages"]).encode()) + len(canonical_json(prepared["params"]["response_format"]).encode()) + 4096
    assert prepared["input_tokens"] == expected
    assert prepared["params"]["extra_body"] == {"thinking": {"type": "disabled"}}
    altered = deepcopy(prepared)
    altered["input_tokens"] -= 1
    rejected = asyncio.run(model.call(altered))
    assert rejected["model_attempted"] is False
    model.settings = replace(model.settings, max_input_bytes=1024)
    with pytest.raises(PackageRoleModelError, match="INPUT_TOO_LARGE"):
        model.prepare(context)
    assert play.sdk.chat_completion.await_count == 0


@pytest.mark.parametrize("patch", [{"statement": "未经授权的台词"}, {"private_goal": "secret"},
    {"action_id": "find-key", "public_basis": [{"collection": "truth", "id": "answer"}]}])
def test_proposal_contract_rejects_prose_and_extra_fields(play, patch):
    initial = setup(play)
    with pytest.raises(ValueError):
        validate_proposal({"action_id": "find-key", "public_basis": [], **patch}, projected(play, initial))


def test_proposal_transport_requires_stop_and_known_usage(play):
    initial = setup(play)
    response = answer()
    response.finish_reason = None
    play.sdk.chat_completion.return_value = response
    result = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert result["last_ai_status"] == "INVALID"
    assert result["investigation_proposals"]["entries"] == []


def test_proposal_http_authenticated_bounded_explicit_dispatch(play_http):
    client, svc = play_http
    svc.propose = AsyncMock(return_value={"revision": 2})
    url = "/api/fusion/package-plays/play-x/proposals"
    assert client.post(url, json=command()).status_code == 401
    for extra in ({"context": {}}, {"owner_user_id": 1}, {"public_basis": []}):
        assert client.post(url, json={**command(), **extra}, headers={"Authorization": "Bearer player"}).status_code == 422
    assert svc.propose.await_count == 0
    result = client.post(url, json=command(), headers={"Authorization": "Bearer player"})
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    assert svc.propose.await_args.args[2] == 2 and svc.propose.await_args.args[1].model_dump() == command()


def test_legacy_package_cannot_gain_investigation_options_or_dispatch(play):
    _, initial = start(play)
    assert initial["investigation_proposals"]["character_ids"] == []
    with pytest.raises(PackagePlayError, match="PROPOSAL_UNSUPPORTED"):
        asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert events(play) == [] and play.sdk.chat_completion.await_count == 0


def test_proposal_budget_rejection_keeps_the_ledger_empty(play):
    play.play.policy = replace(play.policy, token_limit=1)
    initial = setup(play)
    with pytest.raises(PackagePlayError, match="BUDGET_EXCEEDED"):
        asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    assert events(play) == [] and play.sdk.chat_completion.await_count == 0


def test_changed_proposal_config_keeps_history_readable_but_blocks_new_calls(play):
    initial = setup(play)
    result = asyncio.run(play.play.propose(initial["play_id"], command(), 1))
    play.play.proposal_model.settings = replace(play.model.settings, temperature=1)
    reread = play.play.get(initial["play_id"], 1)
    assert reread["investigation_proposals"]["entries"] == result["investigation_proposals"]["entries"]
    assert reread["investigation_proposals"]["reason"] == "CONFIG_CHANGED"
    with pytest.raises(PackagePlayError, match="AI_UNAVAILABLE"):
        asyncio.run(play.play.propose(initial["play_id"], command(2, "new"), 1))
    assert play.sdk.chat_completion.await_count == 1
