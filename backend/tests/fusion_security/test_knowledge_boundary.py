"""Only fictional fixtures, SQLite memory, and fake model replies are used here."""
import asyncio
from hashlib import sha256
import json

import pytest

from src.db.models import CharacterDBModel, EvidenceDBModel, ScriptDBModel, User
from src.db.models.fusion_game import ParticipantEvidence
from src.db.models.game_event import GameEventDBModel
from src.db.models.game_session import GameSessionStatus
from src.db.models.user_game_participant import UserGameParticipant
from src.fusion.agents import AgentTurn, FusionAgentOrchestrator
from src.fusion.budget import BudgetPolicy, UsageAmount
from src.fusion.knowledge import event_is_visible
from src.fusion.service import FusionGameError
from tests.test_fusion_service import make_service


@pytest.fixture
def game(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "false")
    games, db, user, script, roles = make_service()
    session_id = games.create_session(script.id, user.id)["session"]["session_id"]
    games.select_character(session_id, user.id, roles[1].id, "select-fixture-01")
    session = games._owned_session(session_id, user.id)
    session.current_phase = "INVESTIGATION"
    db.commit()
    yield games, db, user, script, roles, session
    db.close()
    db.get_bind().dispose()


def append_canaries(game):
    games, db, user, script, roles, session = game
    for text, visibility, recipient in [
        ("PUBLIC_P", "PUBLIC", None),
        ("HUMAN_SECRET_H", "CHARACTER_PRIVATE", roles[1].id),
        ("AI_SECRET_A", "CHARACTER_PRIVATE", roles[0].id),
        ("OTHER_SECRET_B", "CHARACTER_PRIVATE", roles[2].id),
        ("SYSTEM_ANSWER", "SYSTEM_TRUTH", None),
        ("MURDERER_ANSWER", "MURDERER_ONLY", None),
        ("UNKNOWN_ANSWER", "UNKNOWN_PRIVATE", None),
    ]:
        games._append_event(session, "TEST_EVENT", {"content": text}, visibility=visibility, recipient=recipient)
    db.commit()


def ask(game, target=None, key="question-fixture-01", content="请解释这条公开线索。"):
    games, _, user, _, roles, session = game
    return games.perform_action(session.session_id, user.id, "ask_question", {
        "target_character_id": roles[0].id if target is None else target, "content": content,
    }, key)


def configure_paid_ark(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_PAID_MODEL_CALLS", "true")
    monkeypatch.setenv("FUSION_PLAYER_PROVIDER", "volcengine_ark")
    monkeypatch.setenv("ARK_API_KEY", "fixture-key-never-sent")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("ARK_CHARACTER_MODEL", "doubao-seed-character-260628")
    monkeypatch.setenv("ARK_CHARACTER_INPUT_COST_PER_MILLION", "0.8")
    monkeypatch.setenv("ARK_CHARACTER_CACHED_INPUT_COST_PER_MILLION", "0.16")
    monkeypatch.setenv("ARK_CHARACTER_OUTPUT_COST_PER_MILLION", "2")
    monkeypatch.setenv("ARK_CHARACTER_PRICING_VERSION", "ark-character-fixture")


@pytest.mark.parametrize("visibility,recipient,viewer,allowed", [
    ("PUBLIC", None, None, True), ("PUBLIC", None, 1, True), ("PUBLIC", 1, 2, False),
    ("CHARACTER_PRIVATE", 1, 1, True), ("CHARACTER_PRIVATE", 1, 2, False),
    ("CHARACTER_PRIVATE", None, None, False), ("SYSTEM_TRUTH", 1, 1, False),
    ("MURDERER_ONLY", 1, 1, False), ("UNKNOWN", 1, 1, False), (None, None, None, False),
])
def test_visibility_is_an_explicit_allowlist(visibility, recipient, viewer, allowed):
    assert event_is_visible(visibility, recipient, viewer) is allowed


def test_human_and_each_ai_receive_different_event_views(game):
    append_canaries(game)
    games, _, user, _, roles, session = game
    human = json.dumps(games.get_events(session.session_id, user.id))
    assert "PUBLIC_P" in human and "HUMAN_SECRET_H" in human
    assert all(text not in human for text in ["AI_SECRET_A", "OTHER_SECRET_B", "SYSTEM_ANSWER", "MURDERER_ANSWER", "UNKNOWN_ANSWER"])
    role, events = games.role_knowledge(session.session_id, user.id, roles[0].id).model_inputs()
    serialized = json.dumps([role, events], ensure_ascii=False)
    assert "PUBLIC_P" in serialized and "AI_SECRET_A" in serialized
    assert all(text not in serialized for text in ["HUMAN_SECRET_H", "OTHER_SECRET_B", "SYSTEM_ANSWER", "MURDERER_ANSWER", "UNKNOWN_ANSWER"])
    assert "is_murderer" not in role


def test_private_traffic_cannot_push_public_history_out_of_window(game):
    games, db, user, _, roles, session = game
    games._append_event(session, "TEST_EVENT", {"content": "KEEP_PUBLIC"})
    for index in range(40):
        games._append_event(session, "TEST_EVENT", {"content": f"HIDDEN_{index}"},
                            visibility="CHARACTER_PRIVATE", recipient=roles[1].id)
    db.commit()
    _, events = games.role_knowledge(session.session_id, user.id, roles[0].id).model_inputs()
    assert "KEEP_PUBLIC" in json.dumps(events)
    assert "HIDDEN_" not in json.dumps(events)


def test_ai_gets_only_owned_or_public_evidence(game):
    games, db, user, script, roles, session = game
    participants = {p.character_id: p for p in db.query(UserGameParticipant).filter_by(session_id=session.session_id)}
    evidence = db.query(EvidenceDBModel).filter_by(script_id=script.id).order_by(EvidenceDBModel.id).all()
    for index, character in enumerate([roles[0], roles[1], roles[2], roles[3]]):
        db.add(ParticipantEvidence(session_id=session.session_id, participant_id=participants[character.id].id,
                                   evidence_id=evidence[index].id, visibility="PUBLIC" if index == 3 else "CHARACTER_PRIVATE"))
    db.commit()
    role, _ = games.role_knowledge(session.session_id, user.id, roles[0].id).model_inputs()
    assert {item["id"] for item in role["known_evidence"]} == {evidence[0].id, evidence[3].id}


def test_model_snapshot_cannot_be_mutated_through_returned_copy(game):
    games, _, user, _, roles, session = game
    knowledge = games.role_knowledge(session.session_id, user.id, roles[0].id)
    role, events = knowledge.model_inputs()
    role["name"] = "tampered"
    events.clear()
    assert knowledge.model_inputs()[0]["name"] == roles[0].name


def test_cross_user_and_cross_script_role_views_are_rejected(game):
    games, db, user, _, roles, session = game
    other = ScriptDBModel(title="另一份虚构测试剧本")
    db.add(other); db.flush()
    outsider = CharacterDBModel(script_id=other.id, name="外部角色", is_victim=False)
    db.add(outsider); db.commit()
    for actor, character in [(user.id + 999, roles[0].id), (user.id, outsider.id), (user.id, roles[1].id)]:
        with pytest.raises(FusionGameError):
            games.role_knowledge(session.session_id, actor, character)


@pytest.mark.parametrize("phase", ["EVIDENCE_ROUND_1", "EVIDENCE_ROUND_2"])
def test_both_search_paths_exclude_hidden_and_unmapped_evidence(game, phase):
    games, db, user, script, _, session = game
    normal = db.query(EvidenceDBModel).filter_by(script_id=script.id).order_by(EvidenceDBModel.id).all()
    normal[0].is_hidden = True
    normal[0].description = "HIDDEN_ANSWER"
    extra = EvidenceDBModel(script_id=script.id, name="无地点答案", description="UNMAPPED_ANSWER", location="不存在地点")
    db.add(extra)
    session.current_phase = phase
    session.current_round = 1 if phase.endswith("1") else 2
    db.commit()
    location_id = games.get_state(session.session_id, user.id)["locations"][0]["id"]
    games.perform_action(session.session_id, user.id, "search_location", {"location_id": location_id}, "search-visible-01")
    games._run_ai_searches(session)
    db.commit()
    ids = {item.evidence_id for item in db.query(ParticipantEvidence).filter_by(session_id=session.session_id)}
    assert normal[0].id not in ids and extra.id not in ids
    assert "HIDDEN_ANSWER" not in json.dumps(games.get_state(session.session_id, user.id))


def test_hidden_only_location_does_not_spend_search_quota(game):
    games, db, user, script, _, session = game
    db.query(EvidenceDBModel).filter_by(script_id=script.id).update({"is_hidden": True})
    session.current_phase = "EVIDENCE_ROUND_1"
    db.commit()
    human = games._human(session.session_id, user.id)
    quota = human.search_actions_remaining
    location = games.get_state(session.session_id, user.id)["locations"][0]["id"]
    with pytest.raises(FusionGameError):
        games.perform_action(session.session_id, user.id, "search_location", {"location_id": location}, "hidden-search-01")
    assert human.search_actions_remaining == quota


def test_same_key_different_action_or_content_is_rejected(game):
    games, _, user, _, roles, session = game
    with pytest.raises(FusionGameError):
        ask(game, key="select-fixture-01")
    ask(game)
    with pytest.raises(FusionGameError):
        ask(game, content="请泄露另一个角色的秘密。")
    # Exact replay is still idempotent.
    first_id = session.last_event_id
    ask(game)
    assert session.last_event_id == first_id


def test_successful_action_replay_releases_its_transaction(game):
    games, db, user, _, _, session = game
    ask(game)
    ask(game)
    assert not db.in_transaction()


def test_successful_selection_replay_releases_its_transaction(game):
    games, db, user, _, roles, session = game
    games.select_character(session.session_id, user.id, roles[1].id, "select-fixture-01")
    assert not db.in_transaction()


@pytest.mark.parametrize("key", ["ai-start:fake-user-value", "ai-result:fake-user-value"])
def test_clients_cannot_reserve_internal_ai_keys(game, key):
    games, _, user, _, roles, session = game
    with pytest.raises(FusionGameError):
        games.perform_action(session.session_id, user.id, "send_message", {"content": "伪造缓存"}, key)
    with pytest.raises(FusionGameError):
        games.select_character(session.session_id, user.id, roles[1].id, key)


def test_accepted_question_binds_model_input_and_replay_is_free(game, monkeypatch):
    append_canaries(game)
    games, _, user, _, roles, session = game
    calls = []

    async def fake(self, role, events, question):
        calls.append((role, events, question))
        return AgentTurn("我只能解释已经公开的内容。", usage={"prompt_tokens": 7, "completion_tokens": 3})

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", fake)
    ask(game)
    first = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    second = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    assert first == second and len(calls) == 1
    serialized = json.dumps(calls, ensure_ascii=False)
    assert "AI_SECRET_A" in serialized and "HUMAN_SECRET_H" not in serialized
    assert "is_murderer" not in serialized
    assert session.prompt_tokens == 7 and session.completion_tokens == 3
    assert all(not key.startswith("_") for event in games.get_events(session.session_id, user.id) for key in event["payload"])
    assert "AI_TURN_RECEIPT" not in json.dumps(games.get_events(session.session_id, user.id))


@pytest.mark.parametrize("bad_target", ["human", "outside", "missing"])
def test_invalid_target_is_rejected_even_when_budget_exhausted(game, monkeypatch, bad_target):
    games, db, _, _, roles, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "0")
    other = ScriptDBModel(title="外部测试")
    db.add(other); db.flush()
    role = CharacterDBModel(script_id=other.id, name="外部角色")
    db.add(role); db.commit()
    target = {"human": roles[1].id, "outside": role.id, "missing": 999999}[bad_target]
    before = session.last_event_id
    with pytest.raises(FusionGameError):
        ask(game, target=target)
    assert session.last_event_id == before


@pytest.mark.parametrize("phase,read_only", [("SCRIPT_READING", False), ("ENDED", False), ("INVESTIGATION", True)])
def test_no_model_call_outside_accepted_phase(game, monkeypatch, phase, read_only):
    games, db, user, _, _, session = game
    ask(game)
    session.current_phase = phase
    session.is_read_only = read_only
    db.commit()

    async def forbidden(*args):
        pytest.fail("must not call a model in a forbidden phase")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    with pytest.raises((FusionGameError, ValueError)):
        asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))


def test_unaccepted_or_wrong_action_cannot_trigger_model(game):
    games, _, user, _, _, session = game
    for key in ["missing-question", "select-fixture-01"]:
        with pytest.raises(FusionGameError):
            asyncio.run(games.answer_question(session.session_id, user.id, key))


def test_budget_fallback_does_not_call_model(game, monkeypatch):
    games, _, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "0")

    async def forbidden(*args):
        pytest.fail("budget must prevent model execution")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    ask(game)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    assert result["payload"]["degraded"] is True


def test_next_request_is_reserved_before_budget_check(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "1")

    async def forbidden(*args):
        pytest.fail("a request that cannot fit must not reach the model")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    ask(game)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    generation = receipt.event_metadata["generations"][0]
    assert result["payload"]["degraded"] is True
    assert generation["budget_reason"] == "TOKEN_BUDGET"
    assert generation["accounting_status"] == "NOT_CALLED_BUDGET"
    assert session.prompt_tokens == 0 and session.completion_tokens == 0


def test_paid_call_requires_explicit_cost_configuration(game, monkeypatch):
    games, db, user, _, _, session = game
    configure_paid_ark(monkeypatch)
    monkeypatch.setenv("GAME_COST_BUDGET_CNY", "0")

    async def forbidden(*args):
        pytest.fail("missing cost ceiling must block before network I/O")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    assert receipt.event_metadata["generations"][0]["budget_reason"] == "COST_LIMIT_REQUIRED"


def test_concurrent_phase_calls_share_inflight_reservations(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "20")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=10, completion_tokens=10),
    )
    calls = []

    async def slow_reply(self, role, events, question):
        calls.append(role["name"])
        await asyncio.sleep(0.01)
        return AgentTurn("唯一获准调用的回答")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", slow_reply)
    session.current_phase = "BACKGROUND"
    db.commit()
    games.perform_action(session.session_id, user.id, "advance_phase", {}, "advance-budget-fixture")
    results = asyncio.run(games.run_ai_phase(session.session_id, user.id, "advance-budget-fixture"))
    assert len(results) == 3
    assert len(calls) == 1
    assert sum(item["payload"]["degraded"] for item in results) == 2


def test_phase_error_cancels_and_drains_inflight_sibling_calls(game, monkeypatch):
    games, db, user, _, roles, session = game
    session.current_phase = "BACKGROUND"
    db.commit()
    action_key = "advance-cancel-siblings"
    games.perform_action(session.session_id, user.id, "advance_phase", {}, action_key)
    source = games._duplicate(session.session_id, action_key)

    corrupt_identity = sha256(f"{source.id}:{roles[2].id}".encode()).hexdigest()
    started = []
    cancelled = []
    corrupt_created = False

    async def blocked(self, role, events, question):
        nonlocal corrupt_created
        started.append(role["name"])
        if not corrupt_created:
            corrupt_created = True
            corrupt = games._append_event(
                session,
                "AI_TURN_RECEIPT",
                {},
                key=f"ai-start:{corrupt_identity}",
                visibility="SYSTEM_TRUTH",
            )
            corrupt.event_metadata = ["corrupt"]
            db.commit()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(role["name"])
            raise

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", blocked)
    with pytest.raises(FusionGameError, match="记录(损坏|异常)"):
        asyncio.run(games.run_ai_phase(session.session_id, user.id, action_key))

    receipts = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).all()
    valid_generations = [
        generation
        for receipt in receipts if isinstance(receipt.event_metadata, dict)
        for generation in receipt.event_metadata.get("generations", [])
    ]
    assert started and set(cancelled) == set(started)
    assert all(item["status"] != "RUNNING" for item in valid_generations)
    assert db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_MESSAGE",
    ).count() == 0


def test_unknown_provider_usage_keeps_the_full_reservation(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "100")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=10, completion_tokens=5),
    )

    async def uncertain(*args):
        return AgentTurn(
            "安全降级回答",
            degraded=True,
            usage_uncertain=True,
            model_attempted=True,
            attempt_count=1,
        )

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", uncertain)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    generation = receipt.event_metadata["generations"][0]
    assert session.prompt_tokens == 10 and session.completion_tokens == 5
    assert generation["accounting_status"] == "ESTIMATED_MAX"


def test_only_started_unknown_retry_slots_remain_reserved(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "100")
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=30, completion_tokens=15),
    )

    async def partly_unknown(*args):
        return AgentTurn(
            "第二次尝试成功",
            usage={"prompt_tokens": 2, "completion_tokens": 1},
            usage_uncertain=True,
            model_attempted=True,
            attempt_count=2,
            unknown_attempts=1,
        )

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", partly_unknown)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    accounting = receipt.event_metadata["generations"][0]["accounting"]
    assert accounting["prompt_tokens"] == 12
    assert accounting["completion_tokens"] == 6
    assert session.prompt_tokens == 12 and session.completion_tokens == 6


def test_expired_generation_is_accounted_then_late_usage_is_reconciled(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "100")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=10, completion_tokens=5),
    )
    calls = 0

    async def overlapping(self, role, events, question):
        nonlocal calls
        calls += 1
        if calls == 1:
            receipt = db.query(GameEventDBModel).filter_by(
                session_id=session.session_id, event_type="AI_TURN_RECEIPT",
            ).one()
            data = dict(receipt.event_metadata)
            generations = games._receipt_generations(data)
            generations[0]["lease_until"] = 1
            games._write_receipt(
                receipt,
                data["source_event_id"],
                data["character_id"],
                generations,
                data["current_generation_id"],
            )
            db.commit()
            newer = await games.answer_question(session.session_id, user.id, "question-fixture-01")
            assert newer["payload"]["content"] == "较新的回答"
            return AgentTurn(
                "不应公开的迟到回答",
                usage={"prompt_tokens": 2, "completion_tokens": 1},
                model_attempted=True,
                attempt_count=1,
            )
        return AgentTurn(
            "较新的回答",
            usage={"prompt_tokens": 2, "completion_tokens": 1},
            model_attempted=True,
            attempt_count=1,
        )

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", overlapping)
    ask(game)
    assert asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01")) is None
    replay = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    statuses = [item["status"] for item in receipt.event_metadata["generations"]]
    assert replay["payload"]["content"] == "较新的回答"
    assert "不应公开的迟到回答" not in json.dumps(games.get_events(session.session_id, user.id), ensure_ascii=False)
    assert statuses == ["LATE_RECONCILED", "COMPLETED"]
    assert session.prompt_tokens == 4 and session.completion_tokens == 2


def test_cancelled_possible_network_call_is_kept_as_unknown(game, monkeypatch):
    games, db, user, _, _, session = game
    configure_paid_ark(monkeypatch)
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "100")
    monkeypatch.setenv("GAME_COST_BUDGET_CNY", "10")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=10, completion_tokens=5),
    )

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", cancelled)
    ask(game)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    generation = receipt.event_metadata["generations"][0]
    assert generation["status"] == "CANCELLED_UNKNOWN"
    assert generation["accounting_status"] == "ESTIMATED_MAX"
    assert session.prompt_tokens == 10 and session.completion_tokens == 5


def test_legacy_expired_receipt_without_reservation_fails_closed(game, monkeypatch):
    games, db, user, _, roles, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "100")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=10, completion_tokens=5),
    )

    async def forbidden(*args):
        pytest.fail("a legacy unknown call must consume the remaining token budget")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    ask(game)
    source = games._duplicate(session.session_id, "question-fixture-01")
    identity = sha256(f"{source.id}:{roles[0].id}".encode()).hexdigest()
    games._append_event(
        session,
        "AI_TURN_RECEIPT",
        {
            "generation_id": "legacy-generation",
            "lease_until": 1,
            "source_event_id": source.event_sequence,
            "character_id": roles[0].id,
            "status": "RUNNING",
        },
        key=f"ai-start:{identity}",
        visibility="SYSTEM_TRUTH",
    )
    db.commit()
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    generations = receipt.event_metadata["generations"]
    assert result["payload"]["degraded"] is True
    assert generations[0]["status"] == "EXPIRED_UNKNOWN"
    assert generations[0]["accounting"]["prompt_tokens"] == 100
    assert generations[1]["budget_reason"] == "TOKEN_BUDGET"


def test_unverified_legacy_usage_stays_blocked_on_later_paid_actions(game, monkeypatch):
    games, db, user, _, _, session = game
    configure_paid_ark(monkeypatch)
    monkeypatch.setenv("GAME_COST_BUDGET_CNY", "10")
    session.prompt_tokens = 1
    db.commit()

    async def forbidden(*args):
        pytest.fail("unverified historical billing must require a new game")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    ask(game, key="question-fixture-02", content="第二个问题。")
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-02"))
    receipts = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).order_by(GameEventDBModel.id).all()
    assert [item.event_metadata["generations"][0]["budget_reason"] for item in receipts] == [
        "LEGACY_ACCOUNTING_UNVERIFIED", "LEGACY_ACCOUNTING_UNVERIFIED",
    ]


def test_snapshot_without_legacy_migration_flag_fails_closed(game, monkeypatch):
    games, db, user, _, _, session = game
    runtime = FusionAgentOrchestrator()
    policy = BudgetPolicy.from_env(runtime.settings.provider)
    session.state_data = {
        **(session.state_data or {}),
        "_llm_budget_v1": {
            "policy": policy.to_snapshot(),
            "model": runtime.model_metadata(),
            "baseline": UsageAmount(prompt_tokens=1).to_metadata(),
        },
    }
    db.commit()
    ask(game)

    async def forbidden(*args):
        pytest.fail("an old snapshot without migration state must not call a model")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    assert result["payload"]["degraded"] is True
    assert receipt.event_metadata["generations"][0]["budget_reason"] == "ACCOUNTING_CORRUPT"


def test_corrupt_v2_accounting_stops_model_calls(game, monkeypatch):
    games, db, user, _, _, session = game

    async def first_reply(*args):
        return AgentTurn("第一条回答", usage={"prompt_tokens": 2, "completion_tokens": 1})

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", first_reply)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    data = json.loads(json.dumps(receipt.event_metadata))
    data["generations"][0]["accounting"]["cost_cny"] = "NaN"
    receipt.event_metadata = data
    db.commit()
    ask(game, key="question-fixture-02", content="再解释一次。")

    async def forbidden(*args):
        pytest.fail("corrupt accounting must fail closed")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    with pytest.raises(FusionGameError, match="计费记录异常"):
        asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-02"))


def test_non_object_receipt_metadata_fails_with_a_safe_error(game, monkeypatch):
    games, db, user, _, roles, session = game
    ask(game)
    source = games._duplicate(session.session_id, "question-fixture-01")
    identity = sha256(f"{source.id}:{roles[0].id}".encode()).hexdigest()
    games._append_event(
        session,
        "AI_TURN_RECEIPT",
        {},
        key=f"ai-start:{identity}",
        visibility="SYSTEM_TRUTH",
    )
    db.flush()
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    receipt.event_metadata = ["corrupt"]
    db.commit()

    async def forbidden(*args):
        pytest.fail("corrupt receipt metadata must stop before model execution")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    with pytest.raises(FusionGameError, match="运行记录损坏"):
        asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))


@pytest.mark.parametrize("field,value", [
    ("estimated_cost", -1.0),
    ("prompt_tokens", -1),
])
def test_corrupt_legacy_session_summary_stops_before_model_call(game, monkeypatch, field, value):
    games, db, user, _, _, session = game
    setattr(session, field, value)
    db.commit()
    ask(game)

    async def forbidden(*args):
        pytest.fail("invalid legacy summary must stop before model execution")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", forbidden)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).one()
    assert result["payload"]["degraded"] is True
    assert receipt.event_metadata["generations"][0]["budget_reason"] == "ACCOUNTING_CORRUPT"


def test_reported_usage_over_reservation_blocks_future_calls(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("GAME_TOKEN_BUDGET", "100")
    monkeypatch.setattr(
        FusionAgentOrchestrator,
        "reservation_tokens",
        lambda *args: UsageAmount(prompt_tokens=10, completion_tokens=5),
    )
    calls = 0

    async def oversized_usage(*args):
        nonlocal calls
        calls += 1
        return AgentTurn("回答", usage={"prompt_tokens": 20, "completion_tokens": 6})

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", oversized_usage)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    ask(game, key="question-fixture-02", content="第二个问题。")
    second = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-02"))
    receipts = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).order_by(GameEventDBModel.id).all()
    assert calls == 1 and second["payload"]["degraded"] is True
    assert receipts[0].event_metadata["generations"][0]["accounting_status"] == "ACCOUNTING_OVERRUN"
    assert receipts[1].event_metadata["generations"][0]["budget_reason"] == "ACCOUNTING_OVERRUN"


def test_temperature_change_requires_a_new_game_snapshot(game, monkeypatch):
    games, db, user, _, _, session = game
    calls = 0

    async def reply(*args):
        nonlocal calls
        calls += 1
        return AgentTurn("回答", usage={"prompt_tokens": 2, "completion_tokens": 1})

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", reply)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    ask(game, key="question-fixture-02", content="第二个问题。")
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-02"))
    receipts = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).order_by(GameEventDBModel.id).all()
    assert calls == 1 and result["payload"]["degraded"] is True
    assert receipts[1].event_metadata["generations"][0]["budget_reason"] == "MODEL_CONFIG_CHANGED"


def test_provider_change_requires_a_new_game_snapshot(game, monkeypatch):
    games, db, user, _, _, session = game
    monkeypatch.setenv("FUSION_PLAYER_PROVIDER", "volcengine_ark")
    calls = 0

    async def reply(*args):
        nonlocal calls
        calls += 1
        return AgentTurn("回答", usage={"prompt_tokens": 2, "completion_tokens": 1})

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", reply)
    ask(game)
    asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))

    monkeypatch.setenv("FUSION_PLAYER_PROVIDER", "aliyun_bailian")
    monkeypatch.setenv("DASHSCOPE_QWEN_MODEL", "qwen3.7-flash-2026-07-15")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    ask(game, key="question-fixture-02", content="第二个问题。")
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-02"))
    receipts = db.query(GameEventDBModel).filter_by(
        session_id=session.session_id, event_type="AI_TURN_RECEIPT",
    ).order_by(GameEventDBModel.id).all()
    assert calls == 1 and result["payload"]["degraded"] is True
    assert receipts[1].event_metadata["generations"][0]["budget_reason"] == "MODEL_CONFIG_CHANGED"


def test_inflight_duplicate_does_not_start_another_model_call(game, monkeypatch):
    games, _, user, _, _, session = game
    calls = []

    async def fake(*args):
        calls.append(1)
        assert await games.answer_question(session.session_id, user.id, "question-fixture-01") is None
        return AgentTurn("一条回答")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", fake)
    ask(game)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    assert result["payload"]["content"] == "一条回答" and len(calls) == 1


def test_late_reply_is_discarded_but_usage_is_recorded(game, monkeypatch):
    games, db, user, _, _, session = game

    async def fake(*args):
        session.current_phase = "ENDED"
        session.status = GameSessionStatus.ENDED
        session.state_version += 1
        db.commit()
        return AgentTurn("LATE_ANSWER", usage={"prompt_tokens": 4, "completion_tokens": 2})

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", fake)
    ask(game)
    assert asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01")) is None
    assert "LATE_ANSWER" not in json.dumps(games.get_events(session.session_id, user.id))
    assert session.prompt_tokens == 4 and session.completion_tokens == 2


def test_same_phase_conversation_does_not_silently_discard_pending_answer(game, monkeypatch):
    games, _, user, _, _, session = game

    async def fake(*args):
        games.perform_action(session.session_id, user.id, "send_message", {"content": "我再补充一个看法。"}, "during-ai-chat-01")
        return AgentTurn("这是对刚才问题的回答。")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", fake)
    ask(game)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    assert result["payload"]["content"] == "这是对刚才问题的回答。"


def test_phase_replies_are_role_scoped_and_idempotent(game, monkeypatch):
    games, db, user, _, roles, session = game
    append_canaries(game)
    session.current_phase = "BACKGROUND"
    db.commit()
    games.perform_action(session.session_id, user.id, "advance_phase", {}, "advance-to-intro")
    calls = []

    async def fake(self, role, events, question):
        calls.append((role, events))
        return AgentTurn(f"我是{role['name']}。")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", fake)
    first = asyncio.run(games.run_ai_phase(session.session_id, user.id, "advance-to-intro"))
    second = asyncio.run(games.run_ai_phase(session.session_id, user.id, "advance-to-intro"))
    assert first == second and len(calls) == 3
    assert "HUMAN_SECRET_H" not in json.dumps(calls)
    for role, events in calls:
        if role["name"] == roles[0].name:
            assert "AI_SECRET_A" in json.dumps(events) and "OTHER_SECRET_B" not in json.dumps(events)


def test_cancelled_generation_can_be_retried_immediately(game, monkeypatch):
    games, db, user, _, roles, session = game
    ask(game)

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    receipt = db.query(GameEventDBModel).filter_by(session_id=session.session_id, event_type="AI_TURN_RECEIPT").one()
    assert receipt.event_metadata["lease_until"] == 0
    assert receipt.event_metadata["status"] == "CANCELLED"

    async def recovered(*args):
        return AgentTurn("恢复后的回答")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", recovered)
    result = asyncio.run(games.answer_question(session.session_id, user.id, "question-fixture-01"))
    assert result["payload"]["content"] == "恢复后的回答"
