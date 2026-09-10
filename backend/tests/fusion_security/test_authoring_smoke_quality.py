"""Fixed synthetic smoke acceptance, independently of model self-evaluation."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from src.fusion.authoring_model import AuthoringModelError
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_validation import content_hash, validate_package
from tests.fusion_security.test_authoring_model import (
    draft_for, fixture_config, fixture_report, good_response, smoke_expected_package,
    smoke_module, stub_sdk, synthetic_context_and_package,
)


@pytest.fixture
def fixture(tmp_path):
    module = smoke_module()
    root = tmp_path / "synthetic-quality"
    root.mkdir(mode=0o700)
    sources, request = module.make_synthetic_bundle(root)
    context = prepare_authoring_sources(sources, request)
    package = smoke_expected_package(context, module)
    return module, context, package


@pytest.mark.parametrize("mutation,code", [
    ("truth-in-introduction", "SMOKE_INTRODUCTION_MAPPING_FAILED"),
    ("private-fact-in-public-evidence", "SMOKE_EVIDENCE_MAPPING_FAILED"),
    ("extra-truth", "SMOKE_TRUTH_MAPPING_FAILED"),
    ("wrong-introduction", "SMOKE_INTRODUCTION_MAPPING_FAILED"),
    ("wrong-settlement", "SMOKE_SETTLEMENT_MAPPING_FAILED"),
    ("extra-knowledge", "SMOKE_PRIVATE_FACT_MAPPING_FAILED"),
    ("duplicate-fact", "SMOKE_PRIVATE_FACT_MAPPING_FAILED"),
    ("swapped-facts", "SMOKE_PRIVATE_FACT_MAPPING_FAILED"),
    ("extra-public-knowledge", "SMOKE_PRIVATE_FACT_MAPPING_FAILED"),
    ("wrong-phase-title", "SMOKE_PHASE_ORDER_FAILED"),
    ("swapped-phase-titles", "SMOKE_PHASE_ORDER_FAILED"),
])
def test_authoring_smoke_quality_rejects_structurally_valid_fixture_deviations(fixture, mutation, code):
    module, context, original = fixture
    package = deepcopy(original)
    if mutation == "truth-in-introduction":
        package["introduction"]["text"] = module.EXPECTED_TRUTH
    elif mutation == "private-fact-in-public-evidence":
        extra = deepcopy(package["evidence"][0])
        extra.update(id="extra-public-fact", text=module.EXPECTED_FACTS["值班员甲"])
        package["evidence"].append(extra)
    elif mutation == "extra-truth":
        extra = deepcopy(package["truth"][0])
        extra.update(id="extra-system-truth", text=module.EXPECTED_FACTS["值班员甲"])
        package["truth"].append(extra)
        package["settlement"]["truth_ids"].append(extra["id"])
    elif mutation == "wrong-introduction":
        package["introduction"]["text"] = module.EXPECTED_EVIDENCE
    elif mutation == "wrong-settlement":
        package["settlement"]["instructions"]["text"] = module.EXPECTED_TRUTH
    elif mutation in {"extra-knowledge", "extra-public-knowledge"}:
        extra = deepcopy(package["knowledge"][0])
        extra["id"] = "extra-memory"
        if mutation == "extra-public-knowledge":
            extra.update(visibility="PUBLIC", character_id=None, disclosure="PUBLIC")
        package["knowledge"].append(extra)
    elif mutation == "duplicate-fact":
        package["knowledge"][1]["text"] = package["knowledge"][0]["text"]
    elif mutation == "swapped-facts":
        first, second = package["knowledge"]
        first["character_id"], second["character_id"] = second["character_id"], first["character_id"]
    elif mutation == "wrong-phase-title":
        package["phases"][0]["title"] = module.EXPECTED_TRUTH
    else:
        first, second = package["phases"]
        first["title"], second["title"] = second["title"], first["title"]
    # Every variant can pass generic package validation and a schema-valid
    # model Audit. Acceptance must independently reject the semantic error.
    assert validate_package(package)["valid"]
    with pytest.raises(module.AuthoringSmokeError, match=f"^{code}$"):
        module.validate_smoke_artifacts(context, package, fixture_report(package))


def test_authoring_smoke_quality_accepts_optional_default_omission_without_rewriting(fixture):
    module, context, original = fixture
    for explicit_defaults in (False, True):
        package = deepcopy(original)
        for item in package["knowledge"] + package["evidence"]:
            if explicit_defaults:
                item["release"]["required_public_evidence_ids"] = []
            else:
                item["release"].pop("required_public_evidence_ids", None)
        before = deepcopy(package)
        quality = module.validate_smoke_artifacts(context, package, fixture_report(package))
        assert package == before
        assert quality["candidate_package_hash"] == content_hash(package)
        assert quality["quality_checks_version"] == module.QUALITY_CHECKS_VERSION == "authoring-smoke-quality/1.1"
        assert quality["evidence_count"] == quality["truth_count"] == 1
    assert module.FIXTURE_HASH == "843940852e4cd687017bd4c07f25dffe04bf96171be55c868bcf5e30a0aa3be4"


def test_authoring_smoke_quality_accepts_different_generated_ids_and_array_order(fixture):
    module, context, package = fixture
    characters = {item["id"]: f"person-{index + 10}" for index, item in enumerate(package["characters"])}
    phases = {item["id"]: f"stage-{index + 20}" for index, item in enumerate(package["phases"])}
    for item in package["characters"]:
        item["id"] = characters[item["id"]]
    for item in package["phases"]:
        item["id"] = phases[item["id"]]
        item["next_phase_id"] = phases.get(item["next_phase_id"])
    package["initial_phase_id"] = phases[package["initial_phase_id"]]
    package["settlement"]["phase_id"] = phases[package["settlement"]["phase_id"]]
    for index, item in enumerate(package["knowledge"] + package["evidence"]):
        item["id"] = f"fact-or-card-{index + 30}"
        item["character_id"] = characters.get(item["character_id"])
        item["release"]["phase_id"] = phases[item["release"]["phase_id"]]
    package["truth"][0]["id"] = "answer-40"
    package["settlement"]["truth_ids"] = ["answer-40"]
    for collection in ("characters", "phases", "knowledge"):
        package[collection].reverse()
    assert module.validate_smoke_artifacts(context, package, fixture_report(package))["candidate_package_hash"] == content_hash(package)


def test_authoring_smoke_quality_keeps_source_checks_ahead_of_fixture_matching(fixture):
    module, context, package = fixture
    package["introduction"]["text"] = module.EXPECTED_TRUTH
    package["introduction"]["sources"][0]["anchor"] = "L999999"
    with pytest.raises(AuthoringModelError, match="^COMPILER_REFERENCE_UNAUTHORIZED$"):
        module.validate_smoke_artifacts(context, package, fixture_report(package))


def test_authoring_smoke_quality_refuses_additional_initial_prerequisites(fixture):
    module, context, package = fixture
    package["knowledge"][0]["release"]["required_public_evidence_ids"] = [package["evidence"][0]["id"]]
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$"):
        module.validate_smoke_artifacts(context, package, fixture_report(package))


def test_authoring_smoke_quality_failure_preserves_completed_job_and_real_adapter_charge(tmp_path, monkeypatch):
    module = smoke_module()
    _factory, client = stub_sdk(monkeypatch)

    async def fixture_reply(messages, **_kwargs):
        user = json.loads(messages[1].content)
        if user["task"] == "COMPILE":
            context, _ = synthetic_context_and_package()
            context.update(user["input"])
            context["source_ids"] = [item["source_id"] for item in user["input"]["materials"]]
            package = smoke_expected_package(context, module)
            package["introduction"]["text"] = module.EXPECTED_TRUTH
            return good_response(draft_for(package))
        return good_response(fixture_report(user["candidate"]))

    client.chat_completion.side_effect = fixture_reply
    root = tmp_path / "private-smoke"
    root.mkdir(mode=0o700)
    status, receipt = asyncio.run(module.execute_smoke(module.SmokeAuthoringModel(fixture_config()), root))
    assert status == 3 and receipt["result_code"] == "SMOKE_INTRODUCTION_MAPPING_FAILED"
    assert receipt["job_state"] == "COMPLETED" and receipt["candidate_version_id"] > 0
    assert receipt["job_reload_verified"] is True and receipt["source_report_hash"]
    assert receipt["quality_checks_version"] == module.QUALITY_CHECKS_VERSION
    assert Decimal(receipt["charged_cost_cny"]) == Decimal(receipt["persisted_job_cost_cny"]) > 0
    assert receipt["usage_known"] is True and receipt["calls_started"] == ["COMPILE", "AUDIT"]
    assert receipt["publication_ready"] is False and client.chat_completion.await_count == 2
