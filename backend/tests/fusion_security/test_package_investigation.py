"""Fictional v1.2 investigation rules; no real sources, database or SDK."""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from src.fusion.package_investigation_rules import PackageInvestigationRules, STATE_CONTRACT
from src.fusion.package_play_rules import PackagePlayRules, PlayRulesError
from src.fusion.package_validation import canonical_json, content_hash, validate_package, validator_version_for
from src.fusion.source_bundles import verifier_version_for
from src.schemas.script_package import ScriptPackageV12, package_json_schema, parse_script_package
from tests.fusion_security.test_script_packages import fictional_package, fictional_package_v11, set_at
from tests.fusion_security.test_source_bundles import bundle, candidate_v11_for


def investigation_package(base=None):
    package = deepcopy(base if base is not None else fictional_package_v11())
    package["schema_version"] = "script-package/1.2"
    refs = deepcopy(package["introduction"]["sources"])
    common = {"origin": "SOURCE_EXPLICIT", "sources": refs}
    package["mechanics"] = {"phase_budgets": [
        {"phase_id": "opening", "points": 3, "advance_policy": "REQUIRE_EXHAUSTED", **deepcopy(common)},
        {"phase_id": "ending", "points": 1, "advance_policy": "ALLOW_REMAINING", **deepcopy(common)},
    ], "actions": [
        {"id": "find-key", "label": "查看公开钥匙牌", "cost": 1, "phase_ids": ["opening"],
         "allowed_character_ids": ["a", "b"], **deepcopy(common)},
        {"id": "open-case", "label": "调查展柜", "cost": 2, "phase_ids": ["opening"],
         "allowed_character_ids": ["a"], "required_action_ids": ["find-key"],
         "required_public_evidence_ids": ["key"], **deepcopy(common)},
        {"id": "next-search", "label": "调查复盘记录", "cost": 1, "phase_ids": ["ending"],
         "allowed_character_ids": ["a"], "required_action_ids": ["open-case"], **deepcopy(common)},
        {"id": "free-check", "label": "核对共享标记", "cost": 0, "phase_ids": ["opening", "ending"],
         "allowed_character_ids": ["a", "b"], **deepcopy(common)},
    ]}
    package["evidence"][0]["release"]["required_action_ids"] = ["open-case"]
    package["evidence"].append({"id": "key", "text": "PUBLIC_SHARED_KEY", "visibility": "PUBLIC",
        "character_id": None, "release": {"phase_id": "opening", "required_action_ids": ["find-key"]},
        "disclosure": "PUBLIC", "sources": deepcopy(refs)})
    package["knowledge"].extend([
        {"id": "b-private", "text": "ACTION_GRANTED_OTHER_PRIVATE", "kind": "FACT", "visibility": "CHARACTER_PRIVATE",
         "character_id": "b", "disclosure": "KEEP_PRIVATE", "sources": deepcopy(refs),
         "release": {"phase_id": "opening", "required_action_ids": ["open-case"]}},
        {"id": "a-later", "text": "FUTURE_ACTION_MATERIAL", "kind": "FACT", "visibility": "CHARACTER_PRIVATE",
         "character_id": "a", "disclosure": "MAY_SHARE", "sources": deepcopy(refs),
         "release": {"phase_id": "ending", "required_action_ids": ["find-key"]}},
    ])
    return package


def perform(engine, identifier):
    engine.apply("PERFORM_ACTION", {"action_id": identifier})


def test_package_investigation_complete_sourced_points_and_private_grants():
    package = investigation_package()
    report = validate_package(package)
    assert report["valid"], report
    assert report["contract_version"] == "script-package/1.2"
    assert report["validator_version"] == "script-package-validator/1.2"
    assert not report["publication_ready"]
    assert isinstance(parse_script_package(package), ScriptPackageV12)
    parsed = parse_script_package(package).model_dump()
    assert parse_script_package(parsed).model_dump() == parsed
    Draft202012Validator(package_json_schema("script-package/1.2")).validate(package)
    engine = PackageInvestigationRules(package, "a")
    assert engine.view()["private_evidence"] == [] and engine.view()["public_evidence"] == []
    assert engine.view()["mechanics"] == {"remaining_points": 3, "initial_points": 3, "spent_points": 0,
        "can_finish_phase": False, "available_actions": [{"id": "find-key", "label": "查看公开钥匙牌", "cost": 1},
                                                        {"id": "free-check", "label": "核对共享标记", "cost": 0}]}
    perform(engine, "find-key")
    assert engine.view()["mechanics"]["remaining_points"] == 2
    assert [item["id"] for item in engine.view()["public_evidence"]] == ["key"]
    perform(engine, "open-case")
    assert engine.view()["mechanics"]["remaining_points"] == 0 and engine.view()["can_advance"]
    assert [item["id"] for item in engine.view()["private_evidence"]] == ["clock"]
    for value in (engine.view(), engine.role_context("b")):
        assert not any(marker in canonical_json(value) for marker in
                       ("ACTION_GRANTED_OTHER_PRIVATE", "FUTURE_ACTION_MATERIAL", "SYSTEM_TRUTH_SENTINEL"))
    engine.apply("SHARE_MATERIAL", {"collection": "evidence", "id": "clock"})
    engine.apply("ADVANCE_PHASE")
    assert engine.view()["mechanics"]["remaining_points"] == 1
    assert {item["id"] for item in engine.view()["public_evidence"]} == {"clock", "record", "key"}
    assert "a-later" in {item["id"] for item in engine.view()["private_knowledge"]}
    perform(engine, "next-search")
    engine.apply("SETTLE")
    assert engine.view()["settled"] and not engine.view()["mechanics"]["available_actions"]
    assert engine.view()["settlement"]["truths"][0]["id"] == "answer"
    assert engine.state()["schema_version"] == STATE_CONTRACT
    assert engine.state()["spent_points_by_phase"] == [
        {"phase_id": "ending", "spent_points": 1}, {"phase_id": "opening", "spent_points": 3}]
    assert "sources" not in canonical_json(engine.view()) and "mechanics" not in engine.role_context("b")


@pytest.mark.parametrize("target", [None, {}, {"action_id": []}, {"action_id": "unknown"},
    {"action_id": "open-case"}, {"action_id": "next-search"}, {"action_id": "find-key", "cost": 0},
    {"action_id": "find-key", "actor_character_id": "b"}])
def test_package_investigation_rejected_actions_are_atomic_and_safe(target):
    engine = PackageInvestigationRules(investigation_package(), "a")
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        engine.apply("PERFORM_ACTION", target)
    assert engine.state() == before


def test_package_investigation_restrictions_budget_and_zero_cost_do_not_bypass_global_once():
    engine = PackageInvestigationRules(investigation_package(), "a")
    perform(engine, "free-check")
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        perform(engine, "free-check")
    assert engine.state() == before and engine.view()["mechanics"]["remaining_points"] == 3
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_PHASE_BUDGET_REMAINS$"):
        engine.apply("ADVANCE_PHASE")
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        engine.apply("PERFORM_ACTION", {"action_id": "find-key"}, actor_character_id="b")
    perform(engine, "find-key")
    perform(engine, "open-case")
    engine.apply("ADVANCE_PHASE")
    assert "free-check" not in {item["id"] for item in engine.view()["mechanics"]["available_actions"]}
    # ALLOW_REMAINING is explicit: it is permissible to settle with one point.
    engine.apply("SETTLE")
    assert engine.view()["mechanics"]["remaining_points"] == 1


def test_package_investigation_selected_role_cannot_see_or_execute_other_role_action():
    engine = PackageInvestigationRules(investigation_package(), "b")
    perform(engine, "find-key")
    assert "open-case" not in canonical_json(engine.view())
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        perform(engine, "open-case")
    assert engine.state() == before and engine.view()["private_evidence"] == []


def test_package_investigation_phase_budget_is_not_carried_and_final_policy_blocks_settle():
    package = investigation_package()
    package["mechanics"]["phase_budgets"][0]["advance_policy"] = "ALLOW_REMAINING"
    package["mechanics"]["phase_budgets"][1]["advance_policy"] = "REQUIRE_EXHAUSTED"
    package["mechanics"]["actions"][2]["required_action_ids"] = []
    engine = PackageInvestigationRules(package, "a")
    engine.apply("ADVANCE_PHASE")
    assert engine.view()["mechanics"]["remaining_points"] == 1
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_PHASE_BUDGET_REMAINS$"):
        engine.apply("SETTLE")
    assert engine.state() == before
    perform(engine, "next-search")
    engine.apply("SETTLE")


def test_package_investigation_public_prerequisite_needs_share_not_private_possession():
    package = investigation_package()
    package["evidence"][-1].update(visibility="CHARACTER_PRIVATE", character_id="a", disclosure="MAY_SHARE")
    engine = PackageInvestigationRules(package, "a")
    perform(engine, "find-key")
    assert "open-case" not in {item["id"] for item in engine.view()["mechanics"]["available_actions"]}
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
        perform(engine, "open-case")
    engine.apply("SHARE_MATERIAL", {"collection": "evidence", "id": "key"})
    perform(engine, "open-case")
    assert engine.view()["mechanics"]["remaining_points"] == 0


@pytest.mark.parametrize("order", [("find-key", "free-check"), ("free-check", "find-key")])
def test_package_investigation_material_requires_both_action_and_public_evidence_in_either_order(order):
    package = investigation_package()
    package["evidence"][1]["release"] = {"phase_id": "opening", "required_public_evidence_ids": ["key"],
                                           "required_action_ids": ["free-check"]}
    engine = PackageInvestigationRules(package, "a")
    perform(engine, order[0])
    assert "record" not in {item["id"] for item in engine.view()["public_evidence"]}
    perform(engine, order[1])
    assert "record" in {item["id"] for item in engine.view()["public_evidence"]}


def test_package_investigation_exclusive_choices_cannot_overspend_or_repeat_spend():
    package = investigation_package()
    package["mechanics"]["actions"][3]["cost"] = 2
    assert validate_package(package)["valid"]  # Alternative actions need not all fit together.
    engine = PackageInvestigationRules(package, "a")
    perform(engine, "free-check")
    perform(engine, "find-key")
    before = engine.state()
    assert engine.view()["mechanics"]["remaining_points"] == 0
    assert not engine.view()["mechanics"]["available_actions"]
    for identifier in ("free-check", "open-case"):
        with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_NOT_AVAILABLE$"):
            perform(engine, identifier)
        assert engine.state() == before


def test_package_investigation_ai_reply_closure_respects_action_fence_and_whole_selection():
    package = investigation_package()
    package["evidence"][-1].update(visibility="CHARACTER_PRIVATE", character_id="b", disclosure="MAY_SHARE")
    package["evidence"][0]["character_id"] = "b"
    engine = PackageInvestigationRules(package, "a")
    assert "key" not in {item["id"] for item in engine.role_context("b")["materials"]}
    perform(engine, "find-key")
    before = engine.state()
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_REPLY_INVALID$"):
        engine.apply_reply("b", [{"collection": "evidence", "id": "key"}, {"collection": "evidence", "id": "clock"}])
    assert engine.state() == before
    engine.apply_reply("b", [{"collection": "evidence", "id": "key"}])
    perform(engine, "open-case")
    assert "clock" in {item["id"] for item in engine.role_context("b")["materials"]}
    assert engine.view()["private_evidence"] == []


@pytest.mark.parametrize("path,value,code", [
    (("mechanics", "phase_budgets", 0, "points"), True, "SCHEMA_INVALID"),
    (("mechanics", "phase_budgets", 0, "points"), -1, "SCHEMA_INVALID"),
    (("mechanics", "phase_budgets", 0, "points"), "3", "SCHEMA_INVALID"),
    (("mechanics", "phase_budgets", 0, "advance_policy"), "AUTO", "SCHEMA_INVALID"),
    (("mechanics", "phase_budgets", 0, "phase_id"), "ending", "PHASE_BUDGET_COVERAGE"),
    (("mechanics", "actions", 0, "cost"), 4, "UNREACHABLE_ACTION"),
    (("mechanics", "actions", 0, "cost"), -1, "SCHEMA_INVALID"),
    (("mechanics", "actions", 0, "phase_ids"), ["missing"], "PHASE_NOT_FOUND"),
    (("mechanics", "actions", 0, "phase_ids"), ["opening", "opening"], "DUPLICATE_REFERENCE"),
    (("mechanics", "actions", 0, "allowed_character_ids"), [], "SCHEMA_INVALID"),
    (("mechanics", "actions", 0, "allowed_character_ids"), ["absent"], "CHARACTER_NOT_FOUND"),
    (("mechanics", "actions", 0, "required_action_ids"), ["absent"], "ACTION_NOT_FOUND"),
    (("mechanics", "actions", 0, "required_action_ids"), ["find-key"], "UNREACHABLE_ACTION"),
    (("mechanics", "actions", 0, "required_public_evidence_ids"), ["key"], "UNREACHABLE_ACTION"),
    (("mechanics", "actions", 1, "phase_ids"), ["ending"], "UNREACHABLE_ACTION"),
    (("mechanics", "actions", 0, "origin"), "EDITORIAL", "RULE_ORIGIN_MISMATCH"),
    (("mechanics", "actions", 0, "sources"), [{"source_id": "editorial", "anchor": "x"}], "RULE_ORIGIN_MISMATCH"),
    (("mechanics", "actions", 0, "sources"), [{"source_id": "missing", "anchor": "x"}], "SOURCE_NOT_FOUND"),
    (("knowledge", 0, "release", "required_action_ids"), ["find-key"], "INITIAL_KNOWLEDGE_MISSING"),
    (("evidence", 0, "release", "required_action_ids"), None, "SCHEMA_INVALID"),
])
def test_package_investigation_invalid_rules_fail_closed_without_body(path, value, code):
    package = investigation_package()
    set_at(package, path, value)
    report = validate_package(package)
    assert not report["valid"] and code in {item["code"] for item in report["issues"]}, report
    assert "PRIVATE_a_SENTINEL" not in canonical_json(report)
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_PACKAGE_INVALID$"):
        PackageInvestigationRules(package, "a")


def test_package_investigation_editorial_rules_require_real_supplement_and_no_claim_of_approval():
    package = investigation_package()
    action = package["mechanics"]["actions"][0]
    action["origin"] = "EDITORIAL"
    action["sources"].append({"source_id": "editorial", "anchor": "rule-note"})
    report = validate_package(package)
    assert report["valid"] and not report["publication_ready"]
    package["mechanics"]["actions"][0]["PRIVATE_FIELD_SENTINEL"] = "PRIVATE_VALUE_SENTINEL"
    safe = canonical_json(validate_package(package))
    assert "PRIVATE_FIELD_SENTINEL" not in safe and "PRIVATE_VALUE_SENTINEL" not in safe


def test_package_investigation_mixed_cycle_and_later_phase_cannot_unlock_earlier_action():
    package = investigation_package()
    package["mechanics"]["actions"][0]["required_public_evidence_ids"] = ["key"]
    assert "UNREACHABLE_ACTION" in {item["code"] for item in validate_package(package)["issues"]}
    package = investigation_package()
    package["evidence"][-1]["release"]["phase_id"] = "ending"
    assert "UNREACHABLE_ACTION" in {item["code"] for item in validate_package(package)["issues"]}


@pytest.mark.parametrize("collection", ["actions", "phase_budgets"])
def test_package_investigation_source_verifier_checks_new_rule_anchor_and_keeps_old_version(bundle, collection):
    _, _, store, frozen = bundle
    old = candidate_v11_for(bundle)
    package = investigation_package(old)
    report = store.verify(frozen["bundle_hash"], document=package)
    assert report["valid"] and report["verifier_version"] == "source-verifier/1.2"
    assert store.get_report(report["report_hash"]) == report
    package["mechanics"][collection][0]["sources"][0]["anchor"] = "MISSING_PRIVATE_ANCHOR_SENTINEL"
    invalid = store.verify(frozen["bundle_hash"], document=package)
    assert not invalid["valid"] and {item["code"] for item in invalid["issues"]} == {"SOURCE_LOCATION_UNVERIFIED"}
    assert "MISSING_PRIVATE_ANCHOR_SENTINEL" not in canonical_json(invalid)
    assert store.verify(frozen["bundle_hash"], document=old)["verifier_version"] == "source-verifier/1.1"


@pytest.mark.parametrize("factory", [fictional_package, fictional_package_v11])
def test_package_investigation_does_not_change_old_contract_views_hashes_or_grants(factory):
    package = factory()
    original_hash = content_hash(package)
    old = PackagePlayRules(package, "a")
    expected = {"schema_version": "package-play-state/1.0", "current_phase_id": "opening", "settled": False,
                "public_knowledge_ids": [], "public_evidence_ids": [], "shared_materials": []}
    assert old.state() == expected and "mechanics" not in old.view()
    assert validator_version_for(package) == "script-package-validator/1.1"
    assert verifier_version_for(package) == "source-verifier/1.1"
    assert content_hash(package) == original_hash
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_PACKAGE_INVALID$"):
        PackageInvestigationRules(package, "a")
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_ACTION_INVALID$"):
        old.apply("PERFORM_ACTION", {"action_id": "find-key"})
    with pytest.raises(PlayRulesError, match="^PACKAGE_PLAY_PACKAGE_INVALID$"):
        PackagePlayRules(investigation_package(), "a")


def test_package_investigation_copies_input_and_replays_to_identical_state_and_view():
    package = investigation_package()
    engine = PackageInvestigationRules(package, "a")
    package["mechanics"]["actions"][0]["cost"] = 999
    commands = [("PERFORM_ACTION", {"action_id": "find-key"}), ("PERFORM_ACTION", {"action_id": "open-case"}),
                ("SHARE_MATERIAL", {"collection": "evidence", "id": "clock"}), ("ADVANCE_PHASE", None)]
    for action, target in commands:
        engine.apply(action, target)
    restored = PackageInvestigationRules(investigation_package(), "a")
    for action, target in commands:
        restored.apply(action, target)
    assert restored.state() == engine.state() and content_hash(restored.state()) == content_hash(engine.state())
    assert restored.view() == engine.view()


def test_package_investigation_schema_helper_rejects_unknown_and_unhashable_versions():
    schema_path = Path(__file__).resolve().parents[3] / "docs/contracts/script-package.v1.2.schema.json"
    assert json.loads(schema_path.read_text()) == {
        "$schema": "https://json-schema.org/draft/2020-12/schema", **package_json_schema("script-package/1.2")}
    for version in ("script-package/999", [], None):
        with pytest.raises(ValueError, match="^unsupported package contract$"):
            package_json_schema(version)
    package = investigation_package()
    package["schema_version"] = []
    assert not validate_package(package)["valid"]
