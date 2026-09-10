"""Independent boundary review of v1.2 grants, finite actions and replay pairs."""
from collections import deque
from copy import deepcopy

import pytest

from src.fusion.package_investigation_rules import PackageInvestigationRules
from src.fusion.package_play_engine import play_engine
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_validation import content_hash, validate_package
from tests.fusion_security.test_package_investigation import investigation_package, perform
from tests.fusion_security.test_script_packages import fictional_package, fictional_package_v11


@pytest.mark.parametrize("factory,wrong_contract", [
    (fictional_package, "package-play-rules/1.1"),
    (fictional_package_v11, "package-play-rules/1.1"),
    (investigation_package, "package-play-rules/1.0"),
    (investigation_package, "package-play-rules/999"),
])
def test_package_investigation_review_frozen_rules_pair_cannot_grant_new_engine(factory, wrong_contract):
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_SNAPSHOT_INVALID$"):
        play_engine(factory(), "a", wrong_contract)


def test_package_investigation_review_structural_validity_is_not_every_role_playability():
    # This is an intentional evidence test for the review boundary, not an
    # instruction to let the selected human impersonate another character.
    package = investigation_package()
    assert validate_package(package)["valid"]
    engine = PackageInvestigationRules(package, "b")
    perform(engine, "find-key")
    perform(engine, "free-check")
    view = engine.view()
    assert view["mechanics"]["remaining_points"] == 2
    assert view["mechanics"]["available_actions"] == []
    assert not view["can_advance"] and not view["mechanics"]["can_finish_phase"]
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_PHASE_BUDGET_REMAINS$"):
        engine.apply("ADVANCE_PHASE")
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        engine.apply("PERFORM_ACTION", {"action_id": "open-case"}, actor_character_id="a")
    assert engine.state() == before


def test_package_investigation_review_cross_namespace_ids_do_not_satisfy_each_other():
    package = investigation_package()
    # An already-public evidence ID equal to an action ID must not count as
    # action completion, and completing an action must not publish a namesake.
    package["evidence"].append({"id": "find-key", "text": "PUBLIC_ID_COLLISION",
        "visibility": "PUBLIC", "character_id": None, "disclosure": "PUBLIC",
        "release": {"phase_id": "opening"}, "sources": deepcopy(package["introduction"]["sources"])})
    engine = PackageInvestigationRules(package, "a")
    assert "find-key" in engine.state()["public_evidence_ids"]
    assert "open-case" not in {item["id"] for item in engine.view()["mechanics"]["available_actions"]}
    assert engine.state()["completed_action_ids"] == []
    perform(engine, "find-key")
    assert engine.state()["completed_action_ids"] == ["find-key"]


@pytest.mark.parametrize("human", ["a", "b"])
def test_package_investigation_review_finite_command_orders_preserve_authority_and_point_ledger(human):
    package = investigation_package()
    action_index = {item["id"]: item for item in package["mechanics"]["actions"]}
    budget_index = {item["phase_id"]: item for item in package["mechanics"]["phase_budgets"]}
    phase_rank = {"opening": 0, "ending": 1}
    materials = {name: {item["id"]: item for item in package[name]} for name in ("knowledge", "evidence")}
    commands = [("PERFORM_ACTION", {"action_id": identifier}) for identifier in action_index]
    commands += [("SHARE_MATERIAL", {"collection": name, "id": identifier})
                 for name, items in materials.items() for identifier in items]
    commands += [("ADVANCE_PHASE", None), ("SETTLE", None)]
    initial = PackageInvestigationRules(package, human)
    queue, seen = deque([(initial, [])]), {content_hash(initial.state())}
    checked = 0
    while queue:
        engine, ledger = queue.popleft()
        state, view = engine.state(), engine.view()
        completed = [identifier for _, identifier in ledger]
        phase_id = state["current_phase_id"]
        assert len(completed) == len(set(completed))
        assert state["completed_action_ids"] == sorted(completed)
        spent = {key: sum(action_index[identifier]["cost"] for phase, identifier in ledger if phase == key)
                 for key in budget_index}
        assert state["spent_points_by_phase"] == [{"phase_id": key, "spent_points": value}
                                                  for key, value in sorted(spent.items())]
        assert view["mechanics"]["remaining_points"] == budget_index[phase_id]["points"] - spent[phase_id]
        assert view["mechanics"]["remaining_points"] >= 0
        assert bool(view["settlement"]) == state["settled"]
        for name, items in materials.items():
            for scope in ("public", "private"):
                for projected in view[scope + "_" + name]:
                    item = items[projected["id"]]
                    assert phase_rank[item["release"]["phase_id"]] <= phase_rank[phase_id]
                    assert set(item["release"].get("required_action_ids", [])) <= set(completed)
                    assert set(item["release"].get("required_public_evidence_ids", [])) <= set(state["public_evidence_ids"])
                    if scope == "private":
                        assert item["character_id"] == human
                    elif item["visibility"] == "CHARACTER_PRIVATE":
                        assert projected["shared_by_character_id"] == item["character_id"]
                        assert item["disclosure"] in {"MAY_SHARE", "MUST_SHARE"}
        # Read-only UI and model projections cannot themselves grant anything.
        for actor in ("a", "b"):
            context = engine.role_context(actor)
            for item in context["materials"]:
                raw = materials[item["collection"]][item["id"]]
                assert raw["disclosure"] != "KEEP_PRIVATE"
                assert raw["visibility"] == "PUBLIC" or raw["character_id"] == actor or item["id"] in state["public_" + item["collection"] + "_ids"]
        assert engine.state() == state
        checked += 1
        for action, target in commands:
            branch = deepcopy(engine)
            try:
                branch.apply(action, target)
            except PlayRulesError:
                assert branch.state() == state
                continue
            child_ledger = ledger + [(phase_id, target["action_id"])] if action == "PERFORM_ACTION" else ledger
            digest = content_hash(branch.state())
            if digest not in seen:
                seen.add(digest)
                queue.append((branch, child_ledger))
        assert len(seen) <= 1000
    assert checked >= (20 if human == "a" else 4)
