"""Global role permissions and deterministic settlement over invented text."""
from copy import deepcopy

import pytest

from src.fusion.package_flow_rules import RulesEngine
from src.fusion.package_play_rules import PackagePlayRules, PlayRulesError
from src.fusion.package_validation import canonical_json, content_hash
from tests.fusion_security.test_package_flow_rules import material
from tests.fusion_security.test_script_packages import fictional_package, fictional_package_v11


def play_package():
    package = fictional_package_v11()
    material(package, "b-card", character="b", text="AI_CARD_ORIGINAL")
    material(package, "b-keep", collection="knowledge", character="b", disclosure="KEEP_PRIVATE", text="AI_KEEP_PRIVATE_SENTINEL")
    material(package, "b-later", collection="knowledge", character="b", phase="ending", text="AI_FUTURE_SENTINEL")
    material(package, "b-claim", collection="knowledge", character="b", kind="CLAIM", text="AI_CLAIM_ORIGINAL")
    material(package, "both-public", prerequisites=["clock", "b-card"], text="PUBLIC_AND_ORIGINAL")
    material(package, "chain-end", prerequisites=["both-public"], text="PUBLIC_CHAIN_ORIGINAL")
    material(package, "b-gated", collection="knowledge", character="b", prerequisites=["chain-end"], kind="INFERENCE", text="AI_GATED_INFERENCE")
    return package


def test_package_play_initial_human_view_preserves_opening_rules():
    package = play_package()
    old, new = RulesEngine(package, "a"), PackagePlayRules(package, "a")
    assert new.view() == old.view() | {"settled": False, "settlement": None}
    for command, target in [("SHARE_MATERIAL", {"collection": "evidence", "id": "clock"}), ("ADVANCE_PHASE", None)]:
        old.apply(command, target)
        new.apply(command, target)
        assert new.view() == old.view() | {"settled": False, "settlement": None}


def test_package_play_role_projection_has_only_public_and_own_disclosable_material():
    engine = PackagePlayRules(play_package(), "a")
    context = engine.role_context("b")
    assert set(context) == {"character", "current_phase", "materials"}
    assert context["character"] == {"id": "b", "name": "值班员乙"}
    assert {(item["collection"], item["id"]) for item in context["materials"]} == {
        ("knowledge", "memory-b"), ("knowledge", "b-claim"), ("evidence", "b-card")}
    assert all(marker not in canonical_json(context) for marker in (
        "PRIVATE_a_SENTINEL", "AI_KEEP_PRIVATE_SENTINEL", "AI_FUTURE_SENTINEL", "SYSTEM_TRUTH_SENTINEL",
        "sources", "relative_path", "truth", "introduction", "required_public_evidence_ids"))
    assert "PRIVATE_b_SENTINEL" not in canonical_json(engine.view())


def test_package_play_ai_responds_by_formally_sharing_and_unlocks_global_and_chain():
    engine = PackagePlayRules(play_package(), "a")
    engine.apply("SHARE_MATERIAL", {"collection": "evidence", "id": "clock"})
    context = engine.role_context("b")
    assert "clock" in {item["id"] for item in context["materials"]}
    assert "both-public" not in {item["id"] for item in context["materials"]}
    refs = [{"collection": "evidence", "id": "b-card"}, {"collection": "knowledge", "id": "b-claim"}]
    engine.apply_reply("b", refs)
    assert set(engine.state()["public_evidence_ids"]) == {"clock", "b-card", "both-public", "chain-end"}
    assert engine.state()["shared_materials"] == [
        {"collection": "evidence", "id": "b-card", "character_id": "b"},
        {"collection": "evidence", "id": "clock", "character_id": "a"},
        {"collection": "knowledge", "id": "b-claim", "character_id": "b"}]
    reply = engine.render_reply("b", refs)
    assert reply["character_id"] == "b" and "角色说法原文：AI_CLAIM_ORIGINAL" in reply["text"]
    assert reply["materials"][1]["kind"] == "CLAIM" and reply["materials"][1]["shared_by_character_id"] == "b"
    assert "AI_GATED_INFERENCE" not in reply["text"]
    assert "b-gated" in {item["id"] for item in engine.role_context("b")["materials"]}


@pytest.mark.parametrize("bad_ref", [
    {"collection": "knowledge", "id": "b-keep"},
    {"collection": "knowledge", "id": "memory-a"},
    {"collection": "knowledge", "id": "b-later"},
    {"collection": "knowledge", "id": "b-gated"},
    {"collection": "truth", "id": "answer"},
    {"collection": "evidence", "id": "PRIVATE_UNKNOWN_SENTINEL"},
])
def test_package_play_reply_selection_is_atomic_and_cannot_expand_scope(bad_ref):
    engine = PackagePlayRules(play_package(), "a")
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_REPLY_INVALID$"):
        engine.apply_reply("b", [{"collection": "evidence", "id": "b-card"}, bad_ref])
    assert engine.state() == before


def test_package_play_reply_cannot_use_its_first_share_to_authorize_later_selection():
    package = play_package()
    material(package, "newly-unlocked", collection="knowledge", character="b", prerequisites=["b-card"])
    engine = PackagePlayRules(package, "a")
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_REPLY_INVALID$"):
        engine.apply_reply("b", [{"collection": "evidence", "id": "b-card"}, {"collection": "knowledge", "id": "newly-unlocked"}])
    assert engine.state()["shared_materials"] == []


@pytest.mark.parametrize("refs", [None, {}, [{"collection": "evidence", "id": "b-card"}] * 2,
                                  [{"collection": "evidence", "id": "b-card"}] * 4,
                                  [{"collection": "evidence", "id": "b-card", "text": "PRIVATE_EXTRA"}]])
def test_package_play_reply_rejects_malformed_or_duplicate_selection(refs):
    engine = PackagePlayRules(play_package(), "a")
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_REPLY_INVALID$"):
        engine.apply_reply("b", refs)
    assert engine.state()["shared_materials"] == []


def test_package_play_empty_reply_is_no_information_and_does_not_share():
    engine = PackagePlayRules(play_package(), "a")
    before = engine.state()
    engine.apply_reply("b", [])
    assert engine.state() == before
    assert engine.render_reply("b", []) == {"character_id": "b", "character_name": "值班员乙",
        "text": "我目前没有可以补充的公开材料。", "materials": []}
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_REPLY_INVALID$"):
        engine.apply_reply("a", [])


def test_package_play_public_reference_is_available_to_other_ai_without_resharing():
    engine = PackagePlayRules(play_package(), "a")
    engine.apply("SHARE_MATERIAL", {"collection": "knowledge", "id": "memory-a"})
    before = engine.state()
    refs = [{"collection": "knowledge", "id": "memory-a"}]
    engine.apply_reply("b", refs)
    assert engine.state() == before
    assert engine.render_reply("b", refs)["materials"][0]["shared_by_character_id"] == "a"


def test_package_play_collection_ids_remain_separate():
    package = play_package()
    material(package, "b-card", collection="knowledge", character="b", kind="CLAIM")
    engine = PackagePlayRules(package, "a")
    engine.apply_reply("b", [{"collection": "knowledge", "id": "b-card"}])
    assert engine.state()["public_knowledge_ids"] == ["b-card"] and not engine.state()["public_evidence_ids"]


@pytest.mark.parametrize("action", ["ADVANCE_PHASE", "SETTLE"])
def test_package_play_ai_cannot_advance_or_settle(action):
    engine = PackagePlayRules(play_package(), "a")
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_FORBIDDEN$"):
        engine.apply(action, actor_character_id="b")
    assert engine.state() == before


def test_package_play_settlement_uses_phase_id_and_only_declared_truth_subset():
    package = play_package()
    package["phases"][0]["title"] = "结算"
    package["phases"][1]["title"] = "开场"
    package["phases"].reverse()
    refs = deepcopy(package["truth"][0]["sources"])
    package["truth"].extend([
        {"id": "second", "text": "SECOND_SELECTED_TRUTH", "visibility": "SYSTEM_TRUTH", "sources": refs},
        {"id": "unused", "text": "UNSELECTED_TRUTH_SENTINEL", "visibility": "SYSTEM_TRUTH", "sources": refs}])
    package["settlement"]["truth_ids"] = ["second", "answer"]
    package["settlement"]["instructions"]["text"] = "原样显示的复盘说明；没有机器可执行的投票或计分规则。"
    engine = PackagePlayRules(package, "a")
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_SETTLEMENT_NOT_READY$"):
        engine.apply("SETTLE")
    engine.apply("ADVANCE_PHASE")
    assert engine.view()["phase_complete"] and engine.view()["settlement"] is None
    engine.apply("SETTLE")
    result = engine.view()
    assert result["settled"] and not result["can_advance"]
    assert result["settlement"] == {"text": package["settlement"]["instructions"]["text"],
        "truths": [{"id": identifier, "text": next(item["text"] for item in package["truth"] if item["id"] == identifier)}
                   for identifier in ["second", "answer"]]}
    assert all(marker not in canonical_json(result) for marker in ("UNSELECTED_TRUTH_SENTINEL", "AI_KEEP_PRIVATE_SENTINEL", "sources"))
    assert "SYSTEM_TRUTH_SENTINEL" not in canonical_json(engine.role_context("b"))
    assert not any(item["can_share"] for item in result["private_knowledge"] + result["private_evidence"])


@pytest.mark.parametrize("action", ["SETTLE", "ADVANCE_PHASE", "SHARE_MATERIAL"])
def test_package_play_settlement_freezes_all_actions(action):
    engine = PackagePlayRules(play_package(), "a")
    engine.apply("ADVANCE_PHASE")
    engine.apply("SETTLE")
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ALREADY_SETTLED$"):
        engine.apply(action, {"collection": "evidence", "id": "clock"} if action == "SHARE_MATERIAL" else None)
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ALREADY_SETTLED$"):
        engine.apply_reply("b", [])
    assert engine.state() == before


def test_package_play_must_share_has_no_invented_deadline():
    package = play_package()
    package["evidence"][2]["disclosure"] = "MUST_SHARE"  # b-card
    engine = PackagePlayRules(package, "a")
    engine.apply("ADVANCE_PHASE")
    assert engine.state()["shared_materials"] == []
    engine.apply("SETTLE")
    assert engine.view()["settled"]


def test_package_play_copies_package_and_projection_for_deterministic_replay():
    package = play_package()
    engine = PackagePlayRules(package, "a")
    other = PackagePlayRules(package, "a")
    context = engine.role_context("b")
    context["materials"][0]["text"] = "MUTATED_CONTEXT"
    package["knowledge"][1]["text"] = "MUTATED_PACKAGE"
    engine.apply_reply("b", [{"collection": "knowledge", "id": "memory-b"}])
    other.apply_reply("b", [{"collection": "knowledge", "id": "memory-b"}])
    assert content_hash(engine.state()) == content_hash(other.state()) and engine.view() == other.view()
