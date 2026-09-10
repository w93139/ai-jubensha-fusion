"""Synthetic, in-memory phase rules; no DB, source files or model calls."""
from copy import deepcopy

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from src.fusion.package_flow_rules import RulesEngine, RulesError
from src.fusion.package_validation import canonical_json, content_hash
from src.schemas.package_flow import CreatePackageFlowRequest, PackageFlowActionRequest
from tests.fusion_security.test_script_packages import fictional_package, fictional_package_v11


def material(package, identifier, *, collection="evidence", phase="opening", prerequisites=(),
             character=None, disclosure=None, kind="FACT", text=None):
    item = {"id": identifier, "text": text or f"Synthetic material {identifier}.",
            "visibility": "PUBLIC" if character is None else "CHARACTER_PRIVATE", "character_id": character,
            "release": {"phase_id": phase, "required_public_evidence_ids": list(prerequisites)},
            "disclosure": disclosure or ("PUBLIC" if character is None else "MAY_SHARE"),
            "sources": deepcopy(package["introduction"]["sources"])}
    if collection == "knowledge":
        item["kind"] = kind
    package[collection].append(item)
    return item


def ids(view, collection):
    return {item["id"] for item in view[collection]}


def share(engine, collection, identifier):
    engine.apply("SHARE_MATERIAL", {"collection": collection, "id": identifier})


@pytest.mark.parametrize("factory", [fictional_package, fictional_package_v11])
def test_package_flow_rules_support_both_immutable_package_contracts(factory):
    package = factory()
    engine = RulesEngine(package, "a")
    initial = engine.view()
    assert initial["current_phase"] == {"id": "opening", "title": "入场"}
    assert initial["can_advance"] and not initial["phase_complete"]
    assert ids(initial, "private_knowledge") == {"memory-a"}
    assert ids(initial, "private_evidence") == {"clock"}
    assert all(item["can_share"] for item in initial["private_knowledge"] + initial["private_evidence"])
    assert engine.state() == {"schema_version": "package-flow-state/1.0", "current_phase_id": "opening",
                              "public_knowledge_ids": [], "public_evidence_ids": [],
                              "shared_knowledge_ids": [], "shared_evidence_ids": []}


def test_package_flow_rules_follow_chain_not_array_order_and_keep_phase_floors():
    package = fictional_package()
    package["phases"][0]["next_phase_id"] = "middle"
    package["phases"].append({"id": "middle", "title": "中场", "next_phase_id": "ending",
                              "sources": deepcopy(package["introduction"]["sources"])})
    package["phases"] = [package["phases"][1], package["phases"][0], package["phases"][2]]
    material(package, "late-public", phase="ending")
    material(package, "early-gated", collection="knowledge", prerequisites=["late-public"], character="a")
    engine = RulesEngine(package, "a")
    assert "early-gated" not in ids(engine.view(), "private_knowledge")
    engine.apply("ADVANCE_PHASE")
    assert engine.view()["current_phase"]["id"] == "middle"
    assert "early-gated" not in ids(engine.view(), "private_knowledge")
    engine.apply("ADVANCE_PHASE")
    final = engine.view()
    assert final["current_phase"]["id"] == "ending" and final["phase_complete"]
    assert ids(final, "private_knowledge") == {"memory-a", "early-gated"}


def test_package_flow_rules_all_prerequisites_must_be_actually_public():
    package = fictional_package()
    material(package, "second-private", character="a")
    material(package, "both-needed", prerequisites=["clock", "second-private"])
    material(package, "known-after-both", collection="knowledge", prerequisites=["both-needed"], character="a")
    engine = RulesEngine(package, "a")
    assert ids(engine.view(), "public_evidence") == set()
    share(engine, "evidence", "clock")
    assert "both-needed" not in ids(engine.view(), "public_evidence")
    share(engine, "evidence", "second-private")
    assert ids(engine.view(), "public_evidence") == {"clock", "second-private", "both-needed"}
    assert "known-after-both" in ids(engine.view(), "private_knowledge")


def test_package_flow_rules_public_chain_reaches_fixed_point_before_knowledge():
    package = fictional_package()
    material(package, "third", prerequisites=["second"])
    material(package, "second", prerequisites=["first"])
    material(package, "first")
    material(package, "inferred", collection="knowledge", prerequisites=["third", "first"], kind="INFERENCE")
    engine = RulesEngine(package, "a")
    view = engine.view()
    assert ids(view, "public_evidence") == {"first", "second", "third"}
    assert view["public_knowledge"][0]["kind"] == "INFERENCE"
    assert all(not item["can_share"] and "shared_by_character_id" not in item for item in view["public_evidence"])


def test_package_flow_rules_future_phase_does_not_unlock_when_prerequisite_is_early_public():
    package = fictional_package()
    material(package, "first")
    material(package, "future-card", phase="ending", prerequisites=["first"])
    engine = RulesEngine(package, "a")
    assert ids(engine.view(), "public_evidence") == {"first"}
    engine.apply("ADVANCE_PHASE")
    assert ids(engine.view(), "public_evidence") == {"first", "future-card"}


def test_package_flow_rules_other_role_private_evidence_does_not_satisfy_dependency():
    package = fictional_package()
    material(package, "other-card", character="b", disclosure="MUST_SHARE", text="OTHER_PRIVATE_SENTINEL")
    material(package, "waiting-card", prerequisites=["other-card"], text="BLOCKED_CARD_SENTINEL")
    engine = RulesEngine(package, "a")
    engine.apply("ADVANCE_PHASE")
    serialized = canonical_json(engine.view()) + canonical_json(engine.state())
    assert all(marker not in serialized for marker in ("other-card", "waiting-card", "OTHER_PRIVATE_SENTINEL", "BLOCKED_CARD_SENTINEL"))
    before = engine.state()
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_MATERIAL_NOT_SHAREABLE$"):
        share(engine, "evidence", "other-card")
    assert engine.state() == before


def test_package_flow_rules_must_share_has_no_invented_deadline_or_automatic_publication():
    package = fictional_package()
    package["evidence"][0]["disclosure"] = "MUST_SHARE"
    engine = RulesEngine(package, "a")
    assert ids(engine.view(), "private_evidence") == {"clock"}
    engine.apply("ADVANCE_PHASE")
    assert engine.view()["phase_complete"] and not engine.view()["public_evidence"]
    share(engine, "evidence", "clock")
    assert ids(engine.view(), "public_evidence") == {"clock", "record"}
    shared = next(item for item in engine.view()["public_evidence"] if item["id"] == "clock")
    assert shared["disclosure"] == "MUST_SHARE" and shared["shared_by_character_id"] == "a" and not shared["can_share"]


def test_package_flow_rules_collection_identity_and_claim_classification_survive_sharing():
    package = fictional_package()
    material(package, "clock", collection="knowledge", character="a", kind="CLAIM")
    engine = RulesEngine(package, "a")
    share(engine, "knowledge", "clock")
    engine.apply("ADVANCE_PHASE")
    assert not engine.view()["public_evidence"]
    assert engine.state()["shared_knowledge_ids"] == ["clock"] and engine.state()["shared_evidence_ids"] == []
    public = engine.view()["public_knowledge"]
    assert public == [{"id": "clock", "text": "Synthetic material clock.", "disclosure": "MAY_SHARE",
                       "kind": "CLAIM", "can_share": False, "shared_by_character_id": "a"}]
    assert "clock" not in ids(engine.view(), "private_knowledge")
    share(engine, "evidence", "clock")
    assert ids(engine.view(), "public_evidence") == {"clock", "record"}


@pytest.mark.parametrize("target", [
    {"collection": "evidence", "id": "unknown-private-sentinel"},
    {"collection": "evidence", "id": "record"},
    {"collection": "knowledge", "id": "memory-b"},
    {"collection": "knowledge", "id": "keep-private"},
    {"collection": "knowledge", "id": "later-private"},
    {"collection": "knowledge", "id": "native-public"},
])
def test_package_flow_rules_inaccessible_share_has_one_safe_error_and_no_state_change(target):
    package = fictional_package()
    material(package, "keep-private", collection="knowledge", character="a", disclosure="KEEP_PRIVATE")
    material(package, "later-private", collection="knowledge", character="a", phase="ending")
    material(package, "native-public", collection="knowledge")
    engine = RulesEngine(package, "a")
    before, view = engine.state(), engine.view()
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_MATERIAL_NOT_SHAREABLE$"):
        engine.apply("SHARE_MATERIAL", target)
    assert engine.state() == before and engine.view() == view


def test_package_flow_rules_already_shared_action_is_not_a_second_transition():
    engine = RulesEngine(fictional_package(), "a")
    share(engine, "knowledge", "memory-a")
    before = engine.state()
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_MATERIAL_NOT_SHAREABLE$"):
        share(engine, "knowledge", "memory-a")
    assert engine.state() == before


@pytest.mark.parametrize("action,target", [
    ("REVEAL_TRUTH", None), ([], None), (None, None),
    ("ADVANCE_PHASE", {"phase_id": "ending"}), ("ADVANCE_PHASE", {}),
    ("SHARE_MATERIAL", None), ("SHARE_MATERIAL", []),
    ("SHARE_MATERIAL", {"collection": "truth", "id": "answer"}),
    ("SHARE_MATERIAL", {"collection": "knowledge", "id": "memory-a", "PRIVATE_SENTINEL": "text"}),
])
def test_package_flow_rules_unknown_commands_and_shapes_fail_safely(action, target):
    engine = RulesEngine(fictional_package(), "a")
    before = engine.state()
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_ACTION_INVALID$"):
        engine.apply(action, target)
    assert engine.state() == before


def test_package_flow_rules_end_of_phase_chain_never_reveals_settlement_or_truth():
    package = fictional_package()
    package["settlement"]["instructions"]["text"] = "SETTLEMENT_SECRET_SENTINEL"
    material(package, "hidden-answer", text="BLOCKED_ANSWER_SENTINEL", prerequisites=["clock"])
    engine = RulesEngine(package, "b")
    engine.apply("ADVANCE_PHASE")
    view = engine.view()
    assert view["phase_complete"] and not view["can_advance"]
    serialized = canonical_json(view)
    assert all(key not in serialized for key in ("truth", "settlement", "SYSTEM_TRUTH_SENTINEL", "SETTLEMENT_SECRET_SENTINEL", "BLOCKED_ANSWER_SENTINEL", "sources", "required_public_evidence_ids", "relative_path"))
    before = engine.state()
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_PHASE_COMPLETE$"):
        engine.apply("ADVANCE_PHASE")
    assert engine.state() == before


def test_package_flow_rules_input_and_returned_values_cannot_mutate_engine():
    package = fictional_package()
    engine = RulesEngine(package, "a")
    expected = engine.view()
    package["knowledge"][0]["text"] = "MUTATED_INPUT_SENTINEL"
    package["phases"][0]["next_phase_id"] = None
    view, state = engine.view(), engine.state()
    view["private_knowledge"][0]["text"] = "MUTATED_VIEW_SENTINEL"
    state["public_evidence_ids"].append("clock")
    assert engine.view() == expected and engine.state()["public_evidence_ids"] == []
    engine.apply("ADVANCE_PHASE")
    assert engine.view()["current_phase"]["id"] == "ending"


def test_package_flow_rules_replay_is_deterministic_and_state_contains_no_body():
    package = fictional_package()
    commands = [("SHARE_MATERIAL", {"collection": "knowledge", "id": "memory-a"}),
                ("ADVANCE_PHASE", None), ("SHARE_MATERIAL", {"collection": "evidence", "id": "clock"})]
    first, second = RulesEngine(package, "a"), RulesEngine(package, "a")
    for action, target in commands:
        first.apply(action, target)
        second.apply(action, target)
        assert content_hash(first.state()) == content_hash(second.state())
    assert first.view() == second.view()
    serialized = canonical_json(first.state())
    assert "text" not in serialized and "PRIVATE_a_SENTINEL" not in serialized
    assert first.state()["public_evidence_ids"] == ["clock", "record"]


@pytest.mark.parametrize("invalid", [None, [], {"PRIVATE_SENTINEL": "SECRET"}])
def test_package_flow_rules_invalid_package_does_not_echo_input(invalid):
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_PACKAGE_INVALID$"):
        RulesEngine(invalid, "a")


def test_package_flow_rules_domain_invalid_package_is_rejected_before_indexing():
    package = fictional_package()
    package["phases"][1]["next_phase_id"] = "opening"
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_PACKAGE_INVALID$"):
        RulesEngine(package, "a")
    with pytest.raises(RulesError, match="^PACKAGE_FLOW_CHARACTER_NOT_FOUND$"):
        RulesEngine(fictional_package(), "PRIVATE_UNKNOWN_CHARACTER")


@pytest.mark.parametrize("body", [
    {"opening_session_id": "package-" + "a" * 32, "idempotency_key": "flow-one", "character_id": "b"},
    {"opening_session_id": "package-" + "A" * 32, "idempotency_key": "flow-one"},
    {"opening_session_id": "package-" + "a" * 32 + "\n", "idempotency_key": "flow-one"},
    {"opening_session_id": 123, "idempotency_key": "flow-one"},
])
def test_package_flow_create_request_cannot_inject_binding_or_malformed_opening(body):
    with pytest.raises(ValidationError):
        CreatePackageFlowRequest.model_validate(body)


@pytest.mark.parametrize("change", [
    {"expected_revision": True}, {"expected_revision": "0"}, {"expected_revision": -1},
    {"phase_id": "ending"}, {"target": None}, {"target": {"collection": "evidence", "id": "clock"}},
    {"action": "SHARE_MATERIAL"}, {"action": "SHARE_MATERIAL", "target": {"collection": "truth", "id": "answer"}},
])
def test_package_flow_action_request_strict_target_revision_and_action(change):
    with pytest.raises(ValidationError):
        PackageFlowActionRequest.model_validate({"idempotency_key": "action-one", "expected_revision": 0,
                                                 "action": "ADVANCE_PHASE"} | change)


def test_package_flow_valid_requests_preserve_wire_without_injected_null_target():
    body = {"idempotency_key": "advance-one", "expected_revision": 0, "action": "ADVANCE_PHASE"}
    parsed = PackageFlowActionRequest.model_validate(body)
    assert parsed.model_dump() == body
    assert PackageFlowActionRequest.model_validate(parsed.model_dump()) == parsed
    assert PackageFlowActionRequest.model_validate_json(parsed.model_dump_json()) == parsed
    shared = body | {"action": "SHARE_MATERIAL", "target": {"collection": "knowledge", "id": "memory-a"}}
    parsed = PackageFlowActionRequest.model_validate(shared)
    assert parsed.model_dump() == shared
    assert PackageFlowActionRequest.model_validate(parsed.model_dump()) == parsed
    assert PackageFlowActionRequest.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("change,valid", [
    ({}, True), ({"target": None}, False), ({"target": {"collection": "evidence", "id": "clock"}}, False),
    ({"action": "SHARE_MATERIAL"}, False), ({"action": "SHARE_MATERIAL", "target": None}, False),
    ({"action": "SHARE_MATERIAL", "target": {"collection": "evidence", "id": "clock"}}, True),
    ({"action": "SHARE_MATERIAL", "target": {"collection": "truth", "id": "answer"}}, False),
    ({"action": "SHARE_MATERIAL", "target": {"collection": "knowledge", "id": "memory-a", "text": "PRIVATE_SENTINEL"}}, False),
])
def test_package_flow_static_schema_matches_strict_action_branches(change, valid):
    body = {"idempotency_key": "schema-action", "expected_revision": 0, "action": "ADVANCE_PHASE"} | change
    schema = PackageFlowActionRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(body) is valid
    if valid:
        assert PackageFlowActionRequest.model_validate(body).model_dump() == body
    else:
        with pytest.raises(ValidationError):
            PackageFlowActionRequest.model_validate(body)
