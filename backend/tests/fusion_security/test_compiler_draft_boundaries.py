"""Cross-step provenance regression using independently invented local materials."""
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from src.fusion import authoring_model
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, validate_model_audit
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_validation import canonical_json, validate_package
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.authoring import AuthoringRequest
from tests.fusion_security.test_authoring_model import fixture_config


@pytest.fixture
def provenance_draft(tmp_path):
    """Two metadata-only originals, selected revised text, and an editorial clue."""
    inputs = tmp_path / "invented-inputs"
    inputs.mkdir()
    bodies = {
        "original-a.md": "# Original record\nUNSELECTED_ORIGINAL_A_SENTINEL\n",
        "original-b.md": "# Original record\nUNSELECTED_ORIGINAL_B_SENTINEL\n",
        "revised-record.md": (
            "# Opening\nA clock has stopped.\n"
            "# Cast\nKeeper Lin\nKeeper Han\n"
            "# Flow\nArrival proceeds to Review.\n"
            "# Lin memory\nLin saw the wind-up key at noon.\n"
            "# Han memory\nHan heard two chimes at noon.\n"
            "# Public clue\nAn inspection card hangs beside the clock.\n"
            "# Truth\nThe clock spring was removed.\n"
            "# Settlement\nCompare the inspection record and end the review.\n"
        ),
        "editorial-clue.md": (
            "# Editorial clue\nAn editorial note mentions a spare spring in the cabinet.\n"
            "# Editorial limits\nThis note is an editorial supplement, not a recovered original.\n"
        ),
    }
    for relative_path, body in bodies.items():
        (inputs / relative_path).write_text(body, encoding="utf-8")
    store = SourceBundleStore(tmp_path / "private-bundles")
    bundle = store.freeze(inputs, {
        "schema_version": "source-plan/1.0", "script_key": "draft-boundary-gallery", "edition": "invented-v1",
        "notes": ["Invented editorial supplement; provenance does not grant publication."],
        "sources": [
            {"relative_path": "original-a.md", "kind": "original", "material_type": "host"},
            {"relative_path": "original-b.md", "kind": "original", "material_type": "host"},
            {"relative_path": "revised-record.md", "kind": "revised", "material_type": "host",
             "original_paths": ["original-b.md", "original-a.md"]},
            {"relative_path": "editorial-clue.md", "kind": "supplement", "material_type": "clue"},
        ],
    })
    sources = {item["relative_path"]: item for item in bundle["sources"]}
    revised_id = sources["revised-record.md"]["id"]
    editorial_id = sources["editorial-clue.md"]["id"]
    request = AuthoringRequest(idempotency_key="boundary-job", bundle_hash=bundle["bundle_hash"],
                               source_ids=[revised_id, editorial_id], title="Invented clock inspection",
                               content_version="boundary-v1", player_count=2)
    context = prepare_authoring_sources(store, request)

    def refs(anchor, source_id=revised_id):
        return [{"source_id": source_id, "anchor": anchor}]

    content = {
        "introduction": {"text": "A clock has stopped.", "sources": refs("Opening")},
        "characters": [{"id": "lin", "name": "Keeper Lin", "sources": refs("Cast")},
                       {"id": "han", "name": "Keeper Han", "sources": refs("Cast")}],
        "initial_phase_id": "arrival",
        "phases": [{"id": "arrival", "title": "Arrival", "next_phase_id": "review", "sources": refs("Flow")},
                   {"id": "review", "title": "Review", "next_phase_id": None, "sources": refs("Flow")}],
        "knowledge": [
            {"id": "lin-memory", "text": "Lin saw the wind-up key at noon.", "kind": "FACT",
             "visibility": "CHARACTER_PRIVATE", "character_id": "lin", "release": {"phase_id": "arrival"},
             "disclosure": "MAY_SHARE", "sources": refs("Lin memory")},
            {"id": "han-memory", "text": "Han heard two chimes at noon.", "kind": "FACT",
             "visibility": "CHARACTER_PRIVATE", "character_id": "han", "release": {"phase_id": "arrival"},
             "disclosure": "MAY_SHARE", "sources": refs("Han memory")},
        ],
        "evidence": [
            {"id": "inspection", "text": "An inspection card hangs beside the clock.", "visibility": "PUBLIC",
             "character_id": None, "release": {"phase_id": "arrival"}, "disclosure": "PUBLIC", "sources": refs("Public clue")},
            {"id": "editorial", "text": "An editorial note mentions a spare spring in the cabinet.",
             "visibility": "CHARACTER_PRIVATE", "character_id": "lin", "release": {"phase_id": "review"},
             "disclosure": "MAY_SHARE", "sources": refs("Editorial clue", editorial_id)},
        ],
        "truth": [{"id": "clock-truth", "text": "The clock spring was removed.", "visibility": "SYSTEM_TRUTH", "sources": refs("Truth")}],
        "settlement": {"phase_id": "review", "truth_ids": ["clock-truth"], "instructions": {
            "text": "Compare the inspection record and end the review.", "sources": refs("Settlement")}},
    }
    draft = {"schema_version": "compiler-draft/1.0", "status": "CANDIDATE", "content": content, "blockers": []}
    return SimpleNamespace(store=store, bundle=bundle, sources=sources, context=context, draft=draft,
                           revised_id=revised_id, editorial_id=editorial_id)


def assembled(fixture):
    return authoring_model.parse_compiler_draft(fixture.draft, fixture.context)["package"]


def editorial_report(package):
    return {"schema_version": "script-audit/1.0", "summary": "Invented review; editorial provenance still needs human review.",
            "coverage": ["PROVENANCE", "TIMELINE", "EVIDENCE", "KNOWLEDGE_BOUNDARY", "PLAYABILITY"],
            "findings": [{"id": "editorial-review", "category": "PROVENANCE", "severity": "WARNING",
                          "target": {"collection": "evidence", "id": "editorial"},
                          "message": "Confirm the editorial addition before publication.",
                          "sources": deepcopy(package["evidence"][1]["sources"])}]}


def test_compiler_draft_multioriginal_and_editorial_lineage_reaches_verifier_and_audit(provenance_draft):
    fixture = provenance_draft
    frozen = deepcopy(fixture.context)
    package = assembled(fixture)
    assert package["sources"] == frozen["sources"]
    declarations = {item["id"]: item for item in package["sources"]}
    assert declarations[fixture.revised_id]["original_source_ids"] == [
        fixture.sources["original-b.md"]["id"], fixture.sources["original-a.md"]["id"]]
    assert declarations[fixture.editorial_id]["kind"] == "supplement"
    assert declarations[fixture.editorial_id]["original_source_ids"] == []
    assert "not grant publication" in declarations[fixture.editorial_id]["provenance_note"]
    verified = fixture.store.verify(fixture.bundle["bundle_hash"], document=package)
    assert verified["valid"] and verified["publication_ready"] is False
    assert validate_package(package)["publication_ready"] is False
    prepared = AuthoringModel(fixture_config()).prepare("AUDIT", fixture.context, package)
    audit_input = json.loads(prepared.messages[-1].content)
    assert audit_input["candidate"]["sources"] == frozen["sources"]
    assert validate_model_audit(editorial_report(package), package)["findings"][0]["sources"][0]["source_id"] == fixture.editorial_id
    package["sources"][0]["relative_path"] = "mutated-return-value.md"
    assert fixture.context == frozen


@pytest.mark.parametrize("original", ["original-a.md", "original-b.md"])
def test_compiler_draft_attached_original_metadata_does_not_authorize_unselected_text(provenance_draft, original):
    fixture = provenance_draft
    fixture.draft["content"]["introduction"]["sources"] = [{"source_id": fixture.sources[original]["id"], "anchor": "Original record"}]
    with pytest.raises(AuthoringModelError, match="^COMPILER_REFERENCE_UNAUTHORIZED$"):
        assembled(fixture)


def test_compiler_draft_cannot_join_excerpts_across_selected_sources(provenance_draft):
    fixture = provenance_draft
    introduction = fixture.draft["content"]["introduction"]
    introduction["text"] += " " + fixture.draft["content"]["evidence"][1]["text"]
    introduction["sources"] += deepcopy(fixture.draft["content"]["evidence"][1]["sources"])
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$") as caught:
        assembled(fixture)
    assert caught.value.details == [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/introduction/text"}]
    assert introduction["text"] not in canonical_json(caught.value.details)


def test_compiler_draft_and_audit_do_not_accept_another_selected_sources_locator(provenance_draft):
    fixture = provenance_draft
    package = assembled(fixture)
    report = editorial_report(package)
    report["findings"][0]["sources"] = deepcopy(package["introduction"]["sources"])
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$"):
        validate_model_audit(report, package)
    fixture.draft["content"]["evidence"][1]["sources"] = deepcopy(package["introduction"]["sources"])
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$") as caught:
        assembled(fixture)
    assert caught.value.details == [{"code": "TEXT_OUTSIDE_REFERENCES", "entity_path": "/evidence/1/text"}]


def test_compiler_draft_prompt_excludes_fixed_provenance_but_reserves_all_sent_input(provenance_draft):
    fixture = provenance_draft
    prepared = AuthoringModel(fixture_config()).prepare("COMPILE", fixture.context)
    wire = prepared.messages[-1].content
    for declaration in fixture.context["sources"]:
        assert declaration["relative_path"] not in wire
        assert declaration["sha256"] not in wire
        if declaration["kind"] == "original":
            assert declaration["id"] not in wire
    assert fixture.context["script_key"] not in wire
    assert fixture.context["content_version"] not in wire
    assert fixture.context["bundle_hash"] not in wire
    assert "UNSELECTED_ORIGINAL_" not in wire
    assert fixture.revised_id in wire and fixture.editorial_id in wire
    assert "Editorial clue" in wire
    assert prepared.context == fixture.context
    assert prepared.input_tokens >= sum(len(message.content.encode("utf-8")) for message in prepared.messages)
    assert prepared.reservation.prompt_tokens == prepared.input_tokens
    assert Decimal("0") < prepared.reservation.cost_cny <= Decimal("0.05")
