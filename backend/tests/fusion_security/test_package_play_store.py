"""Isolated text-play persistence, real role adapter and fictional SDK replies."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect

from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.fusion.agents import PlayerModelSettings
from src.fusion.budget import BudgetPolicy, UsageAmount
from src.fusion.package_play import PackagePlayError, PackagePlayService
from src.fusion.package_play_rules import PackagePlayRules, RULES_CONTRACT
from src.fusion.package_role_model import PackageRoleModel
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_package_runtime import opening_package, publish, request, runtime
from tests.fusion_security.test_package_flow import native_flow


MODEL = "doubao-seed-character-260628"


def response(refs=None, usage=None, **changes):
    return LLMResponse(content=canonical_json({"refs": refs if refs is not None else [{"collection": "knowledge", "id": "memory-b"}]}),
                       usage={"prompt_tokens": 100, "completion_tokens": 10} if usage is None else usage,
                       model=MODEL, finish_reason="stop", **changes)


def ask_body(revision=0, key="ask-one", character="b", question="你知道什么？"):
    return {"expected_revision": revision, "idempotency_key": key, "character_id": character, "question": question}


def action_body(revision=0, key="share-one", action="SHARE_MATERIAL", target=None):
    body = {"expected_revision": revision, "idempotency_key": key, "action": action}
    if action == "SHARE_MATERIAL":
        body["target"] = target or {"collection": "evidence", "id": "clock"}
    return body


@pytest.fixture
def play(runtime):
    return prepare_store(runtime)


@pytest.fixture
def native_play(native_flow):
    return prepare_store(native_flow)


def prepare_store(runtime):
    for model in (ScriptPackagePlay, ScriptPackagePlayEvent):
        model.__table__.create(runtime.db.get_bind())
    settings = PlayerModelSettings(provider="volcengine_ark", model=MODEL, timeout_seconds=2,
                                   retries=0, max_output_tokens=64, max_input_bytes=24000,
                                   thinking_mode="disabled", temperature=0, paid_calls_enabled=True)
    runtime.sdk = SimpleNamespace(chat_completion=AsyncMock(return_value=response()))
    runtime.model = PackageRoleModel(runtime.sdk, settings)
    assert runtime.model.available
    runtime.policy = BudgetPolicy(token_limit=1_000_000, cost_limit_cny=Decimal("10"),
                                  input_rate_cny=Decimal("1"), cached_input_rate_cny=Decimal("0.1"),
                                  output_rate_cny=Decimal("2"), paid_calls_enabled=True,
                                  pricing_version="synthetic-test/1")
    runtime.clock = [1000]
    runtime.play = PackagePlayService(runtime.db, runtime.publisher, runtime.model, runtime.policy, lambda: runtime.clock[0])
    return runtime


def service(play, db):
    return PackagePlayService(db, play.publisher, play.model, play.policy, lambda: play.clock[0])


def start(play, document=None):
    publish(play, document)
    opening = play.service.create(request(), 1)
    play.db.commit()
    initial = play.play.create({"opening_session_id": opening["session_id"], "idempotency_key": "start-play"}, 1)
    play.db.commit()
    return opening, initial


def events(play):
    return play.db.query(ScriptPackagePlayEvent).order_by(ScriptPackagePlayEvent.revision).populate_existing().all()


def test_package_play_store_create_share_advance_settle_preserves_old_opening(play):
    opening, initial = start(play)
    assert initial["revision"] == 0 and initial["status"] == "TEXT_PLAY" and initial["runtime_ready"] is False
    assert initial["settlement"] is None and "SYSTEM_TRUTH_SENTINEL" not in canonical_json(initial)
    binding = json.loads(play.db.query(ScriptPackagePlay).one().binding_json)
    assert binding["rules_contract"] == RULES_CONTRACT
    assert play.play.find_for_opening(opening["session_id"], 1) == initial
    shared = play.play.act(initial["play_id"], action_body(), 1)
    assert any(item["id"] == "gated-public" for item in shared["public_knowledge"])
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_SETTLEMENT_NOT_READY"):
        play.play.act(initial["play_id"], action_body(1, "too-soon", "SETTLE"), 1)
    advanced = play.play.act(initial["play_id"], action_body(1, "advance", "ADVANCE_PHASE"), 1)
    assert advanced["phase_complete"] and not advanced["settled"] and advanced["settlement"] is None
    assert "SYSTEM_TRUTH_SENTINEL" not in canonical_json(advanced)
    settled = play.play.act(initial["play_id"], action_body(2, "settle", "SETTLE"), 1)
    play.db.commit()
    assert settled["status"] == "SETTLED" and settled["revision"] == 3
    assert [item["id"] for item in settled["settlement"]["truths"]] == ["answer"]
    assert "SYSTEM_TRUTH_SENTINEL" in canonical_json(settled)
    assert play.service.get(opening["session_id"], 1) == opening
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == settled
    assert play.sdk.chat_completion.await_count == 0


def test_package_play_store_known_reply_commit_before_network_and_exact_retry(play):
    _, initial = start(play)
    observed = []
    async def completion(messages, **params):
        observed.append(play.db.in_transaction())
        with play.factory() as db:
            stored = db.query(ScriptPackagePlayEvent).one()
            assert stored.kind == "AI_REQUEST"
            assert json.loads(stored.event_json)["data"]["reservation"]["prompt_tokens"] > 0
        context = json.loads(messages[1].content)["context"]
        raw = canonical_json(context)
        assert "PRIVATE_b_SENTINEL" in raw
        for forbidden in ("PRIVATE_a_SENTINEL", "SYSTEM_TRUTH_SENTINEL", "SETTLEMENT_PRIVATE_SENTINEL", "sources", "relative_path"):
            assert forbidden not in raw
        return response()
    play.sdk.chat_completion.side_effect = completion
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(question="  你知道什么？\n"), 1))
    assert observed == [False] and result["revision"] == 2 and result["last_ai_status"] == "OK"
    assert not result["pending_ai"] and result["budget"]["reserved_tokens"] == 0
    assert result["budget"]["used_tokens"] == 110
    assert Decimal(result["budget"]["used_cost_cny"]) == Decimal("0.00012")
    assert "PRIVATE_b_SENTINEL" in result["dialogue"][0]["text"]
    assert any(item.get("shared_by_character_id") == "b" for item in result["public_knowledge"])
    assert asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1)) == result
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == result
    assert play.sdk.chat_completion.await_count == 1
    saved = events(play)
    assert [item.kind for item in saved] == ["AI_REQUEST", "AI_RESULT"]
    assert saved[1].previous_event_hash == saved[0].event_hash
    raw = "".join(item.event_json for item in saved)
    assert all(item not in raw for item in ("PRIVATE_b_SENTINEL", "PRIVATE_a_SENTINEL", "messages", "allowed_refs", "sources"))


def test_package_play_store_other_role_share_unlocks_cross_role_material(play):
    package = opening_package()
    refs = deepcopy(package["knowledge"][0]["sources"])
    package["evidence"].extend([
        {"id": "b-card", "text": "B_PRIVATE_CARD", "visibility": "CHARACTER_PRIVATE", "character_id": "b",
         "release": {"phase_id": "opening"}, "disclosure": "MUST_SHARE", "sources": refs},
        {"id": "public-after-b", "text": "PUBLIC_AFTER_B", "visibility": "PUBLIC", "character_id": None,
         "release": {"phase_id": "opening", "required_public_evidence_ids": ["b-card"]}, "disclosure": "PUBLIC", "sources": refs},
    ])
    package["knowledge"].append({"id": "human-after-b", "text": "HUMAN_AFTER_B", "kind": "INFERENCE",
        "visibility": "CHARACTER_PRIVATE", "character_id": "a", "release": {"phase_id": "opening", "required_public_evidence_ids": ["public-after-b"]},
        "disclosure": "KEEP_PRIVATE", "sources": refs})
    assert validate_package(package)["valid"]
    _, initial = start(play, package)
    assert "B_PRIVATE_CARD" not in canonical_json(initial) and "HUMAN_AFTER_B" not in canonical_json(initial)
    play.sdk.chat_completion.return_value = response(refs=[{"collection": "evidence", "id": "b-card"}])
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert {item["id"] for item in result["public_evidence"]} >= {"b-card", "public-after-b"}
    assert next(item for item in result["private_knowledge"] if item["id"] == "human-after-b")["can_share"] is False
    assert "B_PRIVATE_CARD" in result["dialogue"][0]["text"]


@pytest.mark.parametrize("mode", ["transport", "missing_usage", "invalid_usage"])
def test_package_play_store_unknown_usage_conservatively_charges_once(play, mode):
    _, initial = start(play)
    if mode == "transport":
        play.sdk.chat_completion.side_effect = TimeoutError("PRIVATE_PROVIDER_ERROR")
    elif mode == "missing_usage":
        reply = response()
        reply.usage = None
        play.sdk.chat_completion.return_value = reply
    else:
        play.sdk.chat_completion.return_value = response(usage={"prompt_tokens": -1, "completion_tokens": 10})
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    reservation = json.loads(events(play)[0].event_json)["data"]["reservation"]
    assert result["last_ai_status"] == "UNKNOWN" and result["dialogue"] == []
    assert result["budget"]["used_tokens"] == reservation["prompt_tokens"] + reservation["completion_tokens"]
    assert Decimal(result["budget"]["used_cost_cny"]) == Decimal(reservation["cost_cny"])
    assert result["budget"]["reserved_tokens"] == 0 and "PRIVATE_PROVIDER_ERROR" not in canonical_json(result)
    assert asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1)) == result
    assert play.sdk.chat_completion.await_count == 1


def test_package_play_store_known_invalid_output_keeps_actual_fee_without_payload(play):
    _, initial = start(play)
    play.sdk.chat_completion.return_value = LLMResponse(content='{"refs":[],"text":"MALICIOUS_MODEL_BODY"}',
                    usage={"prompt_tokens": 100, "completion_tokens": 10}, model=MODEL, finish_reason="stop")
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert result["last_ai_status"] == "INVALID" and result["dialogue"] == []
    assert result["budget"]["used_tokens"] == 110
    assert "MALICIOUS_MODEL_BODY" not in canonical_json(result) + "".join(item.event_json for item in events(play))


def test_package_play_store_budget_blocks_before_reservation_and_model_call(play):
    play.policy = replace(play.policy, token_limit=1)
    play.play = service(play, play.db)
    _, initial = start(play)
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_BUDGET_EXCEEDED"):
        asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert events(play) == [] and play.sdk.chat_completion.await_count == 0


def test_package_play_store_unknown_charge_consumes_budget_for_future_questions(play):
    package = opening_package()
    prepared = play.model.prepare(PackagePlayRules(package, "a").role_context("b"), ask_body()["question"])
    play.policy = replace(play.policy, token_limit=prepared["input_tokens"] + prepared["output_tokens"])
    play.play = service(play, play.db)
    _, initial = start(play, package)
    play.sdk.chat_completion.side_effect = TimeoutError("SYNTHETIC")
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_BUDGET_EXCEEDED"):
        asyncio.run(play.play.ask(initial["play_id"], ask_body(result["revision"], "ask-two"), 1))
    assert len(events(play)) == 2 and play.sdk.chat_completion.await_count == 1


@pytest.mark.parametrize("during", ["advance", "settle", "publication"])
def test_package_play_store_late_reply_is_accounted_without_new_knowledge(play, during):
    _, initial = start(play)
    if during == "settle":
        initial = play.play.act(initial["play_id"], action_body(0, "before-ask", "ADVANCE_PHASE"), 1)
        play.db.commit()
    body = ask_body(initial["revision"])
    async def completion(*args, **kwargs):
        assert not play.db.in_transaction()
        if during == "publication":
            play.publisher.current.clear()
        else:
            with play.factory.begin() as db:
                other = service(play, db)
                pending = other.get(initial["play_id"], 1)
                assert pending["pending_ai"]
                other.act(initial["play_id"], action_body(pending["revision"], "concurrent", "SETTLE" if during == "settle" else "ADVANCE_PHASE"), 1)
        return response()
    play.sdk.chat_completion.side_effect = completion
    result = asyncio.run(play.play.ask(initial["play_id"], body, 1))
    assert result["last_ai_status"] == "STALE" and result["dialogue"] == []
    assert result["budget"]["used_tokens"] == 110 and result["budget"]["reserved_tokens"] == 0
    assert not any(item.get("shared_by_character_id") == "b" for item in result["public_knowledge"])
    assert asyncio.run(play.play.ask(initial["play_id"], body, 1)) == result
    assert play.sdk.chat_completion.await_count == 1


def test_package_play_store_inflight_retry_and_other_question_never_dispatch_again(play):
    _, initial = start(play)
    async def completion(*args, **kwargs):
        with play.factory() as db:
            other = service(play, db)
            repeated = await other.ask(initial["play_id"], ask_body(), 1)
            assert repeated["pending_ai"] and repeated["revision"] == 1
            with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_AI_BUSY"):
                await other.ask(initial["play_id"], ask_body(1, "other"), 1)
            db.rollback()
        return response()
    play.sdk.chat_completion.side_effect = completion
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert result["last_ai_status"] == "OK" and len(events(play)) == 2
    assert play.sdk.chat_completion.await_count == 1


def test_package_play_store_cancelled_await_preserves_reservation_then_expiry_never_resends(play):
    _, initial = start(play)
    play.sdk.chat_completion.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    with play.factory() as db:
        pending = service(play, db).get(initial["play_id"], 1)
        assert pending["pending_ai"] and pending["budget"]["reserved_tokens"] > 0
    reservation = json.loads(events(play)[0].event_json)["data"]
    play.clock[0] = reservation["expires_at"] + 1
    play.publisher.current.clear()
    before = play.play.get(initial["play_id"], 1)
    assert before["pending_ai"] is False and before["budget"]["reserved_tokens"] > 0
    assert len(events(play)) == 1  # GET never appends a cleanup event.
    expired = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert expired["last_ai_status"] == "EXPIRED" and expired["dialogue"] == []
    assert expired["budget"]["reserved_tokens"] == 0
    assert Decimal(expired["budget"]["used_cost_cny"]) == Decimal(reservation["reservation"]["cost_cny"])
    assert asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1)) == expired
    assert play.sdk.chat_completion.await_count == 1 and len(events(play)) == 2


def test_package_play_store_owner_key_and_strict_null_requests(play):
    opening, initial = start(play)
    for call in (lambda: play.play.get(initial["play_id"], 2),
                 lambda: play.play.act(initial["play_id"], action_body(), 2),
                 lambda: asyncio.run(play.play.ask(initial["play_id"], ask_body(), 2)),
                 lambda: play.play.find_for_opening(opening["session_id"], 2)):
        with pytest.raises(PackagePlayError) as caught:
            call()
        assert caught.value.status_code == 404
    for body in (None, action_body(action="ADVANCE_PHASE") | {"target": None}, {"owner_user_id": 2}):
        with pytest.raises(PackagePlayError) as caught:
            play.play.act(initial["play_id"], body, 1)
        assert caught.value.status_code == 422
    for body in (None, ask_body() | {"context": "PRIVATE_PAYLOAD"}, ask_body(question="\ud800")):
        with pytest.raises(PackagePlayError) as caught:
            asyncio.run(play.play.ask(initial["play_id"], body, 1))
        assert caught.value.status_code == 422
    with pytest.raises(PackagePlayError) as caught:
        asyncio.run(play.play.ask(initial["play_id"], ask_body(character="a"), 1))
    assert caught.value.status_code == 422 and play.sdk.chat_completion.await_count == 0
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_KEY_CONFLICT"):
        asyncio.run(play.play.ask(initial["play_id"], ask_body(question="不同问题"), 1))
    assert play.play.get(initial["play_id"], 1) == result


@pytest.mark.parametrize("field,value", [("binding_hash", "0" * 64), ("selected_character_id", "b"),
                                        ("binding_json", '{"PRIVATE_TAMPER":true}')])
def test_package_play_store_cached_binding_corruption_fails_closed(play, field, value):
    _, initial = start(play)
    cached = play.db.query(ScriptPackagePlay).one()
    play.db.execute(update(ScriptPackagePlay).values({field: value}).execution_options(synchronize_session=False))
    assert getattr(cached, field) != value
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_SNAPSHOT_INVALID"):
        play.play.get(initial["play_id"], 1)


@pytest.mark.parametrize("field,value", [("event_hash", "0" * 64), ("state_hash", "0" * 64),
                                        ("request_hash", "0" * 64), ("previous_event_hash", "0" * 64)])
def test_package_play_store_cached_event_corruption_fails_closed(play, field, value):
    _, initial = start(play)
    asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    cached = events(play)[1]
    play.db.execute(update(ScriptPackagePlayEvent).where(ScriptPackagePlayEvent.id == cached.id).values(
        {field: value}).execution_options(synchronize_session=False))
    assert getattr(cached, field) != value
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_HISTORY_INVALID"):
        play.play.get(initial["play_id"], 1)


@pytest.mark.parametrize("field,value", [("prompt_tokens", -1), ("completion_tokens", True),
                                        ("cached_prompt_tokens", 999), ("cost_cny", "-1")])
def test_package_play_store_rehashed_negative_or_invalid_usage_still_rejected(play, field, value):
    _, initial = start(play)
    asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    last = events(play)[1]
    payload = json.loads(last.event_json)
    payload["data"]["usage"][field] = value
    play.db.execute(update(ScriptPackagePlayEvent).where(ScriptPackagePlayEvent.id == last.id).values(
        event_json=canonical_json(payload), event_hash=content_hash(payload)).execution_options(synchronize_session=False))
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_HISTORY_INVALID"):
        play.play.get(initial["play_id"], 1)


def test_package_play_store_history_gaps_or_bad_tail_cannot_hide_behind_retries(play):
    _, initial = start(play)
    asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    play.db.execute(text("UPDATE script_package_play_events SET state_hash=:hash WHERE revision=2"), {"hash": "0" * 64})
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_HISTORY_INVALID"):
        asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    play.db.rollback()
    play.db.execute(text("DELETE FROM script_package_play_events WHERE revision=1"))
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_HISTORY_INVALID"):
        play.play.get(initial["play_id"], 1)
    assert play.sdk.chat_completion.await_count == 1


def test_package_play_store_known_extreme_usage_is_not_silently_clamped(play):
    _, initial = start(play)
    count = 1_000_000_123
    play.sdk.chat_completion.return_value = response(usage={"prompt_tokens": count, "completion_tokens": 10})
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert result["last_ai_status"] == "INVALID" and result["dialogue"] == []
    assert result["budget"]["used_tokens"] == count + 10
    assert Decimal(result["budget"]["used_cost_cny"]) == (Decimal(count) + Decimal(20)) / Decimal(1_000_000)
    assert play.play.get(initial["play_id"], 1) == result


def test_package_play_store_adapter_no_dispatch_failure_records_zero_known_cost(play):
    _, initial = start(play)
    original = play.model.call
    async def became_unavailable(prepared):
        play.model.available = False
        play.model.unavailable_reason = "SYNTHETIC_DISABLED"
        return await original(prepared)
    play.model.call = became_unavailable
    result = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    assert result["last_ai_status"] == "INVALID" and result["budget"]["used_tokens"] == 0
    assert Decimal(result["budget"]["used_cost_cny"]) == 0 and result["budget"]["reserved_tokens"] == 0
    assert play.sdk.chat_completion.await_count == 0


def test_package_play_store_result_timestamp_cannot_precede_reservation_even_with_rehashed_receipt(play):
    _, initial = start(play)
    play.sdk.chat_completion.side_effect = TimeoutError("SYNTHETIC")
    asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    first, last = events(play)
    payload = json.loads(last.event_json)
    payload["data"]["received_at"] = json.loads(first.event_json)["data"]["issued_at"] - 1
    play.db.execute(update(ScriptPackagePlayEvent).where(ScriptPackagePlayEvent.id == last.id).values(
        event_json=canonical_json(payload), event_hash=content_hash(payload)).execution_options(synchronize_session=False))
    with pytest.raises(PackagePlayError, match="PACKAGE_PLAY_HISTORY_INVALID"):
        play.play.get(initial["play_id"], 1)


def test_package_play_store_native_sqlite_outer_rollback_leaves_no_partial_create_or_action(native_play):
    play = native_play
    publish(play)
    opening = play.service.create(request(), 1)
    play.db.commit()
    body = {"opening_session_id": opening["session_id"], "idempotency_key": "native-create"}
    play.play.create(body, 1)
    play.db.rollback()
    with play.factory() as db:
        assert db.query(ScriptPackagePlay).count() == 0
    initial = play.play.create(body, 1)
    play.db.commit()
    play.play.act(initial["play_id"], action_body(), 1)
    play.db.rollback()
    with play.factory() as db:
        assert db.query(ScriptPackagePlayEvent).count() == 0
        assert service(play, db).get(initial["play_id"], 1) == initial


def test_package_play_store_native_predispatch_crash_reservation_survives_and_never_resends(native_play):
    play = native_play
    _, initial = start(play)
    # Simulate process loss after the reservation commit, before any SDK call.
    repeated, prepared = play.play._begin(initial["play_id"], ask_body(), 1)
    assert repeated is None and prepared is not None and not play.db.in_transaction()
    play.db.rollback()
    with play.factory() as db:
        other = service(play, db)
        pending = other.get(initial["play_id"], 1)
        assert pending["pending_ai"] and pending["budget"]["reserved_tokens"] > 0
        assert asyncio.run(other.ask(initial["play_id"], ask_body(), 1)) == pending
    play.clock[0] += 301
    expired = asyncio.run(play.play.ask(initial["play_id"], ask_body(), 1))
    play.db.rollback()
    with play.factory() as db:
        assert service(play, db).get(initial["play_id"], 1) == expired
        assert db.query(ScriptPackagePlayEvent).count() == 2
    assert expired["last_ai_status"] == "EXPIRED" and play.sdk.chat_completion.await_count == 0


def test_package_play_store_postgres_row_fence_compiles_without_live_connection(play):
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
                         execute=Mock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 1)))
    instance = service(play, db)
    instance._lock(ScriptPackagePlay, "play_id", "play-" + "a" * 32)
    statement = db.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgres_dialect())
    assert "WHERE script_package_plays.play_id =" in str(compiled)
    assert str(compiled).endswith("FOR UPDATE")
    assert list(compiled.params.values()) == ["play-" + "a" * 32]
