"""No model-produced player prose in the explicit 1.4 confirmation path."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, parse_compiler_draft, parse_compiler_output
from src.fusion.authoring_rule_plan import text_entities
from src.schemas.authoring import AuthoringRequestV14, CompilerTextConfirmation, parse_authoring_request
from tests.fusion_security.test_authoring_rule_plan import synthetic_plan, rule_model
from tests.fusion_security.test_authoring_model import fixture_config


def confirmation():
    context, package = synthetic_plan()
    context["compiler_mode"] = "CONFIRM_FROZEN_TEXT"
    draft = {"schema_version": "compiler-text-confirmation/1.0", "status": "CANDIDATE", "blockers": [],
             "slots": [{"collection": collection, "id": identifier} for (collection, identifier), _ in text_entities(package)]}
    return context, package, draft


def test_text_confirmation_model_wire_cannot_contain_prose():
    context, package, draft = confirmation()
    assert parse_compiler_draft(draft, context)["package"] == package
    draft["slots"][0]["text"] = package["introduction"]["text"]
    with pytest.raises(AuthoringModelError, match="COMPILER_TEXT_DRAFT_INVALID"):
        parse_compiler_draft(draft, context)


def test_text_confirmation_domain_reparse_rejects_even_valid_alternative_quote():
    context, package, _ = confirmation()
    package["introduction"]["text"] = package["truth"][0]["text"]
    with pytest.raises(AuthoringModelError, match="COMPILER_FROZEN_TEXT_MISMATCH"):
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)


def test_text_confirmation_is_explicit_and_cannot_replay_v13():
    context, package, _ = confirmation()
    model = AuthoringModel(fixture_config(), "script-package/1.2", rule_plan_enabled=True, confirm_frozen_text=True)
    prepared = model.prepare("COMPILE", context)
    assert model.snapshot()["schema_version"] == "authoring-model/1.4"
    slots = json.loads(prepared.messages[1].content)["text_slots"]
    assert [slot["text"] for slot in slots] == [entity["text"] for _, entity in text_entities(package)]
    with pytest.raises(AuthoringModelError, match="AUTHORING_CONTRACT_MISMATCH"): rule_model().prepare("COMPILE", context)
    without_mode = {key: value for key, value in context.items() if key != "compiler_mode"}
    with pytest.raises(AuthoringModelError, match="AUTHORING_CONTRACT_MISMATCH"): model.prepare("COMPILE", without_mode)
    with pytest.raises(AuthoringModelError): AuthoringModel(fixture_config(), confirm_frozen_text=True)


def test_text_confirmation_request_and_schema_preserve_explicit_mode():
    context, _, _ = confirmation()
    body = {key: value for key, value in context.items() if key in AuthoringRequestV14.model_fields}
    body["idempotency_key"] = "confirmation-test"
    assert parse_authoring_request(body).model_dump() == body
    root = Path(__file__).resolve().parents[3] / "docs/contracts"
    for name, model in [("authoring-request.v1.4.schema.json", AuthoringRequestV14),
                        ("compiler-text-confirmation.v1.0.schema.json", CompilerTextConfirmation)]:
        assert json.loads((root / name).read_text()) == model.model_json_schema()


def test_text_confirmation_blocker_never_becomes_candidate():
    context, package, _ = confirmation()
    blocked = {"schema_version": "compiler-text-confirmation/1.0", "status": "BLOCKED", "slots": None,
               "blockers": [{"code": "CONTRADICTION", "message": "Needs review.", "sources": deepcopy(package["introduction"]["sources"])}]}
    assert parse_compiler_draft(blocked, context)["package"] is None
