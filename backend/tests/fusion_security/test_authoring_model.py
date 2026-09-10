"""Fictional authoring inputs and stubbed SDK calls; no commercial or paid I/O."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.fusion import authoring_model
from src.fusion.authoring_model import (
    AUDIT_CATEGORIES, AuthoringModel, AuthoringModelError, parse_compiler_draft, parse_compiler_output, validate_model_audit,
    validate_output_diagnostics,
)
from src.fusion.authoring_sources import AuthoringSourceError, locate_text, prepare_authoring_sources
from src.fusion.budget import BudgetPolicy
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.fusion.provider_smoke import SelectedSmokeConfig
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.authoring import AuthoringRequest, CompilerContent, CompilerDraftOutput
from src.schemas.script_package import SourceFileV11, SourceReference
from src.services.llm_service import LLMResponse, ToolCall
from tests.fusion_security.test_script_packages import fictional_package
from tests.fusion_security.test_source_bundles import bundle


def synthetic_context_and_package() -> tuple[dict, dict]:
    """Small independently invented complete fixture, suitable for local stubs."""
    package = fictional_package()
    package["schema_version"] = "script-package/1.1"
    fragments = [package["introduction"]["text"], *(item["name"] for item in package["characters"]),
                 *(item["title"] for item in package["phases"]),
                 *(item["text"] for collection in ("knowledge", "evidence", "truth") for item in package[collection]),
                 package["settlement"]["instructions"]["text"]]
    text = "# fixture-opening\n" + "\n".join(fragments) + "\n"
    source = {"id": "fixture-source", "relative_path": "fixture.md", "sha256": sha256(text.encode()).hexdigest(),
              "kind": "original", "media_type": "text/markdown", "original_source_ids": []}
    package["sources"] = [source]
    refs = [{"source_id": source["id"], "anchor": "fixture-opening"}]
    package["introduction"]["sources"] = deepcopy(refs)
    package["settlement"]["instructions"]["sources"] = deepcopy(refs)
    for collection in ("characters", "phases", "knowledge", "evidence", "truth"):
        for item in package[collection]:
            item["sources"] = deepcopy(refs)
    context = {key: deepcopy(package[key]) for key in ("script_key", "content_version", "title", "player_count", "sources")}
    context.update(bundle_hash="1" * 64, source_ids=[source["id"]],
                   materials=[{"source_id": source["id"], "text": text}], notes=["Synthetic source fixture."])
    return context, package


def draft_for(package: dict) -> dict:
    """Explicit SDK wire fixture; good_response deliberately does not convert."""
    return {"schema_version": "compiler-draft/1.0", "status": "CANDIDATE",
            "content": {key: deepcopy(package[key]) for key in CompilerContent.model_fields}, "blockers": []}


def blocked_draft() -> dict:
    return {"schema_version": "compiler-draft/1.0", "status": "BLOCKED", "content": None,
            "blockers": deepcopy(blocked_output()["blockers"])}


def fixture_config() -> SelectedSmokeConfig:
    profile = PLAYER_PROVIDER_PROFILES["aliyun_bailian"]
    pricing = BudgetPolicy(100000, Decimal("0.1"), Decimal("0.2"), Decimal("0.04"),
                           Decimal("0.8"), True, "fixture-rates-v1")
    return SelectedSmokeConfig(profile, "SYNTHETIC_KEY_SENTINEL", profile.default_base_url, profile.default_model, pricing)


def fixture_report(package: dict) -> dict:
    return {"schema_version": "script-audit/1.0", "summary": "Synthetic review; never approval.",
            "coverage": sorted(AUDIT_CATEGORIES), "findings": [
                {"id": "f1", "category": "PROVENANCE", "severity": "WARNING",
                 "target": {"collection": "introduction", "id": None},
                 "message": "Fixture has no human review.", "sources": deepcopy(package["introduction"]["sources"])}]}


def request_for(frozen: dict, paths: list[str]) -> AuthoringRequest:
    by_path = {item["relative_path"]: item for item in frozen["sources"]}
    return AuthoringRequest(idempotency_key="fixture-job", bundle_hash=frozen["bundle_hash"],
                            source_ids=[by_path[path]["id"] for path in paths], title="Fixture", content_version="v1", player_count=2)


@pytest.mark.parametrize("patch", [
    {"source_ids": []}, {"source_ids": ["a", "a"]}, {"source_ids": [str(i) for i in range(21)]},
    {"source_ids": ["../x"]}, {"title": " "}, {"player_count": True}, {"player_count": "2"},
    {"player_count": 1}, {"player_count": 9}, {"bundle_hash": "abc"}, {"provider": "other"},
])
def test_authoring_request_is_strict(patch):
    body = {"idempotency_key": "fixture", "bundle_hash": "a" * 64, "source_ids": ["a"],
            "title": "Fixture", "content_version": "v1", "player_count": 2}
    with pytest.raises(ValidationError):
        AuthoringRequest.model_validate(body | patch)


def test_selected_sources_keep_originals_metadata_and_never_read_unselected_body(bundle):
    _, _, store, frozen = bundle
    wrapped = Mock(wraps=store)
    context = prepare_authoring_sources(wrapped, request_for(frozen, ["normalized.txt"]))
    assert len(context["sources"]) == 2
    assert context["sources"][1]["original_source_ids"] == [context["sources"][0]["id"]]
    assert [item["source_id"] for item in context["materials"]] == context["source_ids"]
    assert len(wrapped.read_source.call_args_list) == 1
    assert wrapped.read_source.call_args.args[1] == context["source_ids"][0]
    assert "Synthetic original" not in canonical_json(context)
    AuthoringModel(fixture_config()).prepare("COMPILE", context)


def test_selected_source_integrity_failure_is_safe(bundle):
    _, _, store, frozen = bundle
    (store.root / frozen["bundle_hash"] / "files/normalized.txt").write_text("SECRET_CHANGED_BODY")
    with pytest.raises(AuthoringSourceError, match="^SOURCE_INTEGRITY_FAILED$"):
        prepare_authoring_sources(store, request_for(frozen, ["normalized.txt"]))


def test_selecting_unseen_image_or_unknown_source_is_refused(bundle):
    _, _, store, frozen = bundle
    with pytest.raises(AuthoringSourceError, match="^SOURCE_TYPE_UNSUPPORTED$"):
        prepare_authoring_sources(store, request_for(frozen, ["original.jpg"]))
    request = request_for(frozen, ["normalized.txt"]).model_copy(update={"source_ids": ["absent"]})
    with pytest.raises(AuthoringSourceError, match="^SOURCE_NOT_IN_BUNDLE$"):
        prepare_authoring_sources(store, request)


@pytest.mark.parametrize("kind,notes,text,error", [
    ("reference", ["Fixture"], "Body", "SOURCE_TYPE_UNSUPPORTED"),
    ("revised", ["Fixture"], "Body", "ORIGINAL_RELATION_MISSING"),
    ("original", [], "x" * (16 * 1024 + 1), "SOURCE_TEXT_TOO_LARGE"),
    ("supplement", [], "Body", "SUPPLEMENT_PROVENANCE_UNAVAILABLE"),
    ("supplement", ["x" * 500] * 5, "Body", "SUPPLEMENT_PROVENANCE_UNAVAILABLE"),
])
def test_unsupported_or_oversized_sources_fail_without_truncation(tmp_path, kind, notes, text, error):
    root = tmp_path / "in"
    root.mkdir()
    (root / "fixture.md").write_text(text)
    store = SourceBundleStore(tmp_path / "store")
    frozen = store.freeze(root, {"schema_version": "source-plan/1.0", "script_key": "fixture", "edition": "v1",
                                 "notes": notes, "sources": [{"relative_path": "fixture.md", "kind": kind, "material_type": "host"}]})
    with pytest.raises(AuthoringSourceError, match=f"^{error}$"):
        prepare_authoring_sources(store, request_for(frozen, ["fixture.md"]))


def test_multiple_originals_and_editorial_note_are_preserved(bundle):
    root, plan, store, _ = bundle
    (root / "supplement.md").write_text("Synthetic added note.")
    plan["sources"][1]["original_paths"].append("original.jpg")
    plan["sources"].append({"relative_path": "supplement.md", "kind": "supplement", "material_type": "clue"})
    frozen = store.freeze(root, plan)
    wrapped = Mock(wraps=store)
    context = prepare_authoring_sources(wrapped, request_for(frozen, ["normalized.txt", "supplement.md"]))
    normalized = next(source for source in context["sources"] if source["kind"] == "normalized")
    supplement = next(source for source in context["sources"] if source["kind"] == "supplement")
    assert len(normalized["original_source_ids"]) == 2
    assert "不代表原件恢复或人工批准" in supplement["provenance_note"]
    assert plan["notes"][0] in supplement["provenance_note"]
    assert wrapped.read_source.call_count == 2


def test_total_material_bytes_are_bounded(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    paths = [f"f{i}.txt" for i in range(4)]
    for path in paths:
        (root / path).write_text("x" * (13 * 1024))
    store = SourceBundleStore(tmp_path / "store")
    frozen = store.freeze(root, {"schema_version": "source-plan/1.0", "script_key": "fixture", "edition": "v1",
                                 "sources": [{"relative_path": path, "kind": "original", "material_type": "host"} for path in paths]})
    with pytest.raises(AuthoringSourceError, match="^MATERIAL_TEXT_TOO_LARGE$"):
        prepare_authoring_sources(store, request_for(frozen, paths))


def test_extractively_valid_compiler_output_preserves_raw_package():
    context, package = synthetic_context_and_package()
    assert validate_package(package)["valid"]
    result = parse_compiler_output(json.dumps({"status": "CANDIDATE", "package": package, "blockers": []}), context)
    assert result["package"] == package
    assert "required_public_evidence_ids" not in result["package"]["knowledge"][0]["release"]


@pytest.mark.parametrize("field,value", [
    ("script_key", "other"), ("title", "Other title"), ("content_version", "other-v"), ("player_count", 3),
    ("schema_version", "script-package/1.0"), ("sources", []),
])
def test_compiler_cannot_change_bound_inputs(field, value):
    context, package = synthetic_context_and_package()
    package[field] = value
    with pytest.raises(AuthoringModelError, match="^COMPILER_INPUT_BINDING_MISMATCH$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert caught.value.details == [{"code": "INPUT_BINDING_MISMATCH", "entity_path": "/" + field}]


@pytest.mark.parametrize("collection", ["introduction", "settlement", "characters", "knowledge", "evidence", "truth"])
def test_compiler_cannot_invent_or_rewrite_text(collection):
    context, package = synthetic_context_and_package()
    entity = (package["introduction"] if collection == "introduction" else package["settlement"]["instructions"]
              if collection == "settlement" else package[collection][0])
    entity["name" if collection == "characters" else "text"] = "INVENTED_SECRET_SENTINEL"
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    path = ({"introduction": "/introduction/text", "settlement": "/settlement/instructions/text",
             "characters": "/characters/0/name"}.get(collection, f"/{collection}/0/text"))
    assert caught.value.details == [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": path}]
    assert "INVENTED_SECRET_SENTINEL" not in canonical_json(caught.value.details)


@pytest.mark.parametrize("reference,code", [
    ({"source_id": "fixture-source", "anchor": "L999"}, "COMPILER_REFERENCE_UNAUTHORIZED"),
    ({"source_id": "fixture-source", "page": 1}, "COMPILER_REFERENCE_UNAUTHORIZED"),
    ({"source_id": "fixture-source", "anchor": "missing"}, "COMPILER_REFERENCE_UNAUTHORIZED"),
    ({"source_id": "fixture-source", "anchor": "L1"}, "COMPILER_TEXT_NOT_EXTRACTIVE"),
    ({"source_id": "unseen-image", "page": 1}, "COMPILER_PACKAGE_INVALID"),
])
def test_compiler_requires_actual_authorized_text_locators(reference, code):
    context, package = synthetic_context_and_package()
    package["introduction"]["sources"] = [reference]
    with pytest.raises(AuthoringModelError, match=f"^{code}$"):
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)


def test_duplicate_heading_needs_lines_and_crlf_remains_exact():
    materials = {"s": "# section\r\nline\r\n# section\r\nend"}
    assert locate_text(SourceReference(source_id="s", anchor="L1-L2"), materials) == "# section\r\nline\r\n"
    with pytest.raises(AuthoringSourceError):
        locate_text(SourceReference(source_id="s", anchor="section"), materials)


def blocked_output() -> dict:
    return {"status": "BLOCKED", "package": None, "blockers": [
        {"code": "SOURCE_GAP", "message": "Synthetic missing rule.",
         "sources": [{"source_id": "fixture-source", "anchor": "L1"}]}]}


def test_compiler_blocked_output_is_structured_and_locatable():
    context, _ = synthetic_context_and_package()
    result = parse_compiler_output(blocked_output(), context)
    assert result["status"] == "BLOCKED" and result["package"] is None
    changed = blocked_output()
    changed["blockers"][0]["sources"][0]["anchor"] = "L999"
    with pytest.raises(AuthoringModelError, match="^COMPILER_REFERENCE_UNAUTHORIZED$"):
        parse_compiler_output(changed, context)


@pytest.mark.parametrize("patch", [
    {"status": "CANDIDATE"}, {"package": {}}, {"blockers": []}, {"approved": True},
    {"blockers": [{"code": "SOURCE_GAP", "message": "Missing", "sources": []}]},
])
def test_compiler_branches_are_strict(patch):
    context, _ = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^COMPILER_OUTPUT_INVALID$"):
        parse_compiler_output(blocked_output() | patch, context)


@pytest.mark.parametrize("raw", ['{"status":"BLOCKED","status":"CANDIDATE"}', "```json\n{}\n```", "null", "[]", '{"v":NaN}'])
def test_compiler_raw_json_is_strict(raw):
    context, _ = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError):
        parse_compiler_output(raw, context)


def test_audit_requires_complete_coverage_and_exact_entity_reference():
    _, package = synthetic_context_and_package()
    report = fixture_report(package)
    assert set(validate_model_audit(report, package)["coverage"]) == AUDIT_CATEGORIES
    report["coverage"] = ["PROVENANCE"]
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$"):
        validate_model_audit(report, package)


@pytest.mark.parametrize("mutation", ["target", "anchor", "source", "category", "approval", "duplicate"])
def test_audit_cannot_invent_targets_locators_or_approval(mutation):
    _, package = synthetic_context_and_package()
    report = fixture_report(package)
    finding = report["findings"][0]
    if mutation == "target":
        finding["target"] = {"collection": "knowledge", "id": "absent"}
    elif mutation in ("anchor", "source"):
        finding["sources"][0]["anchor" if mutation == "anchor" else "source_id"] = "other"
    elif mutation == "category":
        finding["category"] = "OTHER"
    elif mutation == "approval":
        report["publication_ready"] = True
    else:
        report["findings"].append(deepcopy(finding))
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$"):
        validate_model_audit(report, package)


def test_model_preparation_and_snapshot_are_bounded_and_redacted():
    context, package = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    compile_call = model.prepare("COMPILE", context)
    audit_call = model.prepare("AUDIT", context, package)
    assert compile_call.max_completion_tokens == 8192 and audit_call.max_completion_tokens == 4096
    assert compile_call.reservation.completion_tokens == 8208
    assert compile_call.reservation.cost_cny <= Decimal("0.05")
    assert compile_call.input_tokens == sum(len(item.content.encode()) for item in compile_call.messages) + 512
    assert '"CompilerDraftOutput"' in compile_call.messages[0].content
    assert '"candidate_package"' not in compile_call.messages[0].content
    assert '"additionalProperties":false' in compile_call.messages[0].content
    snapshot = canonical_json(model.snapshot())
    assert "SYNTHETIC_KEY_SENTINEL" not in snapshot
    assert "PRIVATE_a_SENTINEL" not in snapshot
    assert model.snapshot()["prompt_hashes"]["COMPILE"] == compile_call.prompt_hash


@pytest.mark.parametrize("mutation", ["provider", "model", "url", "key", "rate", "cached-rate", "paid", "version"])
def test_model_rejects_unallowlisted_configuration(mutation):
    config = fixture_config()
    if mutation == "provider":
        config = replace(config, profile=PLAYER_PROVIDER_PROFILES["volcengine_ark"])
    elif mutation in ("model", "url", "key"):
        config = replace(config, **{{"model": "model", "url": "base_url", "key": "api_key"}[mutation]: ""})
    else:
        patch = {"rate": {"input_rate_cny": Decimal("NaN")}, "cached-rate": {"cached_input_rate_cny": Decimal("9")},
                 "paid": {"paid_calls_enabled": False}, "version": {"pricing_version": "secret\nurl"}}[mutation]
        config = replace(config, pricing=replace(config.pricing, **patch))
    with pytest.raises(AuthoringModelError, match="^AUTHORING_CONFIG_INVALID$"):
        AuthoringModel(config)


def test_complete_schema_and_context_count_towards_input_limit():
    context, _ = synthetic_context_and_package()
    # Under per-file and overall source limits, but over the request token cap
    # once the complete contract, second file, and system prompt are included.
    context["materials"][0]["text"] = "x" * 16000
    other = deepcopy(context["sources"][0])
    other["id"] = "other"
    other["relative_path"] = "other.md"
    context["sources"].append(other)
    context["source_ids"].append("other")
    context["materials"].append({"source_id": "other", "text": "x" * 10000})
    with pytest.raises(AuthoringModelError, match="^AUTHORING_INPUT_TOO_LARGE$"):
        AuthoringModel(fixture_config()).prepare("COMPILE", context)


def test_expensive_configuration_refuses_before_sdk(monkeypatch):
    config = fixture_config()
    config = replace(config, pricing=replace(config.pricing, output_rate_cny=Decimal("100")))
    factory = Mock()
    monkeypatch.setattr(authoring_model, "OpenAILLMService", factory)
    context, _ = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^AUTHORING_RESERVATION_TOO_LARGE$"):
        AuthoringModel(config).prepare("COMPILE", context)
    factory.assert_not_called()


def stub_sdk(monkeypatch, response=None, exception=None):
    client = Mock()
    client._client = None
    client.chat_completion = AsyncMock(return_value=response, side_effect=exception)
    factory = Mock(return_value=client)
    monkeypatch.setattr(authoring_model, "OpenAILLMService", factory)
    return factory, client


def good_response(output: dict) -> LLMResponse:
    return LLMResponse(content=json.dumps(output), model=fixture_config().model, finish_reason="stop",
                       usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})


def test_model_compile_and_audit_use_exact_contract_and_known_receipts(monkeypatch):
    context, package = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    compile_output = draft_for(package)
    for step, output in [("COMPILE", compile_output), ("AUDIT", fixture_report(package))]:
        supplied_package = package if step == "AUDIT" else None
        factory, client = stub_sdk(monkeypatch, good_response(output))
        prepared = model.prepare(step, context, supplied_package)
        result = asyncio.run(model.call(step, context, supplied_package, prepared=prepared))
        if step == "COMPILE":
            assert result["output"] == {"status": "CANDIDATE", "package": package, "blockers": []}
        assert result["receipt"]["output_diagnostics"] == []
        assert result["receipt"]["usage_known"] is True
        assert Decimal(result["receipt"]["charged_cost_cny"]) == Decimal("0.00006")
        assert "SYNTHETIC_KEY_SENTINEL" not in canonical_json(result["receipt"])
        assert "PRIVATE_a_SENTINEL" not in canonical_json(result["receipt"])
        factory.assert_called_once_with(api_key="SYNTHETIC_KEY_SENTINEL", base_url=fixture_config().base_url,
                                        model=fixture_config().model, client_max_retries=0)
        client.chat_completion.assert_awaited_once()
        params = client.chat_completion.call_args.kwargs
        assert params == {"max_completion_tokens": 8192 if step == "COMPILE" else 4096, "temperature": 0,
                          "response_format": {"type": "json_object"},
                          "extra_body": {"enable_thinking": False, "preserve_thinking": False}}


@pytest.mark.parametrize("mutation,code", [
    ("usage", "AUTHORING_USAGE_UNKNOWN"), ("tools", "AUTHORING_TOOL_CALL_REFUSED"),
    ("reasoning", "AUTHORING_REASONING_REFUSED"), ("reasoning-usage", "AUTHORING_REASONING_REFUSED"),
    ("length", "AUTHORING_FINISH_INVALID"), ("model", "AUTHORING_RESPONSE_MODEL_MISMATCH"),
    ("output", "COMPILER_DRAFT_INVALID"), ("overrun", "AUTHORING_USAGE_EXCEEDS_RESERVATION"),
])
def test_invalid_provider_results_keep_safe_receipt_without_retry(monkeypatch, mutation, code):
    context, _ = synthetic_context_and_package()
    response = good_response(blocked_draft())
    if mutation == "usage":
        response.usage = None
    elif mutation == "tools":
        response.tool_calls = [ToolCall("exfiltrate", {"secret": "PRIVATE_PROVIDER_RAW"})]
    elif mutation == "reasoning":
        response.reasoning_content = "PRIVATE_PROVIDER_RAW"
    elif mutation == "reasoning-usage":
        response.usage["completion_tokens_details"] = {"reasoning_tokens": 1}
    elif mutation == "length":
        response.finish_reason = "length"
    elif mutation == "model":
        response.model = "PRIVATE_PROVIDER_RAW"
    elif mutation == "output":
        response.content = "PRIVATE_PROVIDER_RAW"
    else:
        response.usage = {"prompt_tokens": 40000, "completion_tokens": 10000}
    _, client = stub_sdk(monkeypatch, response)
    with pytest.raises(AuthoringModelError, match=f"^{code}$") as caught:
        asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))
    receipt = caught.value.receipt
    assert "PRIVATE_PROVIDER_RAW" not in str(caught.value) + canonical_json(receipt)
    assert receipt["output_diagnostics"] == caught.value.details == []
    assert receipt["usage_known"] is (mutation != "usage")
    if mutation == "usage":
        assert receipt["charged_cost_cny"] == receipt["reservation"]["cost_cny"]
    if mutation == "overrun":
        assert Decimal(receipt["charged_cost_cny"]) > Decimal(receipt["reservation"]["cost_cny"])
    client.chat_completion.assert_awaited_once()


@pytest.mark.parametrize("exception,code", [
    (RuntimeError("PRIVATE_KEY_URL_AND_BODY"), "AUTHORING_CALL_FAILED_USAGE_UNKNOWN"),
    (asyncio.TimeoutError("PRIVATE_KEY_URL_AND_BODY"), "AUTHORING_TIMEOUT_USAGE_UNKNOWN"),
])
def test_network_failures_are_unknown_and_not_retried(monkeypatch, exception, code):
    context, _ = synthetic_context_and_package()
    _, client = stub_sdk(monkeypatch, exception=exception)
    with pytest.raises(AuthoringModelError, match=f"^{code}$") as caught:
        asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))
    receipt = caught.value.receipt
    assert not receipt["usage_known"]
    assert receipt["charged_cost_cny"] == receipt["reservation"]["cost_cny"]
    assert caught.value.__suppress_context__
    assert "PRIVATE_KEY_URL_AND_BODY" not in canonical_json(receipt)
    client.chat_completion.assert_awaited_once()


def test_sdk_initialization_failure_is_also_safe(monkeypatch):
    factory = Mock(side_effect=RuntimeError("PRIVATE_SDK_INIT_ERROR"))
    monkeypatch.setattr(authoring_model, "OpenAILLMService", factory)
    context, _ = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^AUTHORING_CALL_FAILED_USAGE_UNKNOWN$") as caught:
        asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))
    assert not caught.value.receipt["usage_known"]
    assert "PRIVATE_SDK_INIT_ERROR" not in canonical_json(caught.value.receipt)
    factory.assert_called_once()


def test_changed_preparation_is_rejected_before_network(monkeypatch):
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("COMPILE", context)
    prepared.messages[0].content = "Changed untrusted prompt"
    factory, _ = stub_sdk(monkeypatch)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_PREPARATION_CHANGED$"):
        asyncio.run(model.call("COMPILE", context, prepared=prepared))
    factory.assert_not_called()


def test_sdk_pool_is_closed_after_one_call(monkeypatch):
    context, _ = synthetic_context_and_package()
    _, client = stub_sdk(monkeypatch, good_response(blocked_draft()))
    client._client = Mock(close=AsyncMock())
    asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))
    client._client.close.assert_awaited_once()


def test_request_context_cannot_change_while_waiting_for_model(monkeypatch):
    context, package = synthetic_context_and_package()
    _, client = stub_sdk(monkeypatch)

    async def changed_while_waiting(*args, **kwargs):
        package["introduction"]["text"] = "UNAUTHORIZED_NEW_MATERIAL"
        context["materials"][0]["text"] += "\nUNAUTHORIZED_NEW_MATERIAL\n"
        return good_response(draft_for(package))

    client.chat_completion.side_effect = changed_while_waiting
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$"):
        asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))


def smoke_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "real_authoring_smoke.py"
    spec = importlib.util.spec_from_file_location("authoring_smoke_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke_expected_package(context, module):
    _, package = synthetic_context_and_package()
    for field in ("script_key", "content_version", "title", "player_count", "sources"):
        package[field] = deepcopy(context[field])
    refs = [{"source_id": context["source_ids"][0], "anchor": f"L1-L{len(module.FIXTURE_TEXT.splitlines())}"}]
    package["introduction"] = {"text": "展馆的挂钟停止走动，两位值班员一起核对维修记录。", "sources": deepcopy(refs)}
    for collection in ("characters", "phases", "knowledge", "evidence", "truth"):
        for item in package[collection]:
            item["sources"] = deepcopy(refs)
    package["characters"][0]["name"] = "值班员甲"
    package["characters"][1]["name"] = "值班员乙"
    package["phases"][0]["title"] = "入场"
    package["phases"][1]["title"] = "复盘"
    package["knowledge"][0]["text"] = module.EXPECTED_FACTS["值班员甲"]
    package["knowledge"][1]["text"] = module.EXPECTED_FACTS["值班员乙"]
    package["evidence"] = [{"id": "repair-card", "text": module.EXPECTED_EVIDENCE,
                             "visibility": "PUBLIC", "character_id": None, "release": {"phase_id": "opening"},
                             "disclosure": "PUBLIC", "sources": deepcopy(refs)}]
    package["truth"][0]["text"] = module.EXPECTED_TRUTH
    package["settlement"]["instructions"] = {
        "text": "对照公开维修卡说明挂钟停走的原因，然后结束复盘。", "sources": deepcopy(refs)}
    return package


def test_real_smoke_fixture_is_private_and_compiles_within_bound(tmp_path):
    module = smoke_module()
    root = tmp_path / "smoke"
    root.mkdir(mode=0o700)
    store, request = module.make_synthetic_bundle(root)
    context = prepare_authoring_sources(store, request)
    package = smoke_expected_package(context, module)
    report = fixture_report(package)
    quality = module.validate_smoke_artifacts(context, package, report)
    assert quality["characters"] == quality["phases"] == 2
    assert quality["audit_findings"] == 1 and quality["publication_ready"] is False
    assert store.verify(request.bundle_hash, document=package)["valid"]
    model = AuthoringModel(fixture_config())
    total = model.prepare("COMPILE", context).reservation.cost_cny + model.prepare("AUDIT", context, package).reservation.cost_cny
    assert total < module.MAX_TOTAL_COST_CNY
    assert (root / "synthetic-inputs/fixture.md").stat().st_mode & 0o777 == 0o600
    assert context["materials"][0]["text"] == module.FIXTURE_TEXT


@pytest.mark.parametrize("mutation,code", [
    ("fact", "SMOKE_PRIVATE_FACT_MAPPING_FAILED"),
    ("evidence", "SMOKE_EVIDENCE_MAPPING_FAILED"),
    ("truth", "SMOKE_TRUTH_MAPPING_FAILED"),
])
def test_real_smoke_checks_semantics_beyond_schema(tmp_path, mutation, code):
    module = smoke_module()
    root = tmp_path / "smoke"
    root.mkdir(mode=0o700)
    store, request = module.make_synthetic_bundle(root)
    context = prepare_authoring_sources(store, request)
    package = smoke_expected_package(context, module)
    if mutation == "fact":
        package["knowledge"][0]["character_id"] = "b"
        package["knowledge"][1]["character_id"] = "a"
    elif mutation == "evidence":
        package["evidence"][0]["release"]["phase_id"] = "ending"
    else:
        package["truth"][0]["text"] = module.EXPECTED_EVIDENCE
    with pytest.raises(module.AuthoringSmokeError, match=f"^{code}$"):
        module.validate_smoke_artifacts(context, package, fixture_report(package))


def test_real_smoke_requires_explicit_cli_flag_and_private_directory(tmp_path):
    module = smoke_module()
    with pytest.raises(SystemExit):
        module.build_parser().parse_args([])
    assert module.build_parser().parse_args(["--allow-paid"]).allow_paid is True
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(module.AuthoringSmokeError, match="^SMOKE_DIRECTORY_MUST_BE_PRIVATE$"):
        module.make_synthetic_bundle(root)


def test_real_smoke_has_independent_two_call_and_package_limit(monkeypatch):
    module = smoke_module()
    context, package = synthetic_context_and_package()
    model = module.SmokeAuthoringModel(fixture_config())
    _, client = stub_sdk(monkeypatch)
    client.chat_completion.side_effect = [good_response(draft_for(package)),
                                          good_response(fixture_report(package))]
    asyncio.run(model.call("COMPILE", context))
    changed = deepcopy(package)
    changed["title"] = "changed"
    with pytest.raises(AuthoringModelError, match="^SMOKE_AUDIT_PACKAGE_REFUSED$"):
        asyncio.run(model.call("AUDIT", context, changed))
    asyncio.run(model.call("AUDIT", context, package))
    with pytest.raises(AuthoringModelError, match="^SMOKE_CALL_SEQUENCE_REFUSED$"):
        asyncio.run(model.call("COMPILE", context))
    assert client.chat_completion.await_count == 2
    receipt = module.sanitized_receipt(model, result_code="PASSED")
    assert receipt["calls_started"] == ["COMPILE", "AUDIT"]
    assert Decimal(receipt["total_reserved_cny"]) <= Decimal("0.10")
    assert receipt["usage_known"] is True
    assert "SYNTHETIC_KEY_SENTINEL" not in canonical_json(receipt)


def test_real_smoke_blocked_compile_cannot_proceed_to_audit(monkeypatch):
    module = smoke_module()
    context, package = synthetic_context_and_package()
    _, client = stub_sdk(monkeypatch, good_response(blocked_draft()))
    model = module.SmokeAuthoringModel(fixture_config())
    asyncio.run(model.call("COMPILE", context))
    with pytest.raises(AuthoringModelError, match="^SMOKE_AUDIT_PACKAGE_REFUSED$"):
        asyncio.run(model.call("AUDIT", context, package))
    assert client.chat_completion.await_count == 1


def test_real_smoke_cli_uses_persisted_runner_and_only_selected_config(monkeypatch, tmp_path, capsys):
    module = smoke_module()
    config_loader = Mock(return_value=fixture_config())
    monkeypatch.setattr(module, "load_selected_config", config_loader)
    _, client = stub_sdk(monkeypatch)

    async def fixture_reply(messages, **kwargs):
        user = json.loads(messages[1].content)
        if user["task"] == "COMPILE":
            fixture_context, _ = synthetic_context_and_package()
            fixture_context.update(user["input"])
            fixture_context["source_ids"] = [item["source_id"] for item in user["input"]["materials"]]
            # Only content is returned; these local fixture metadata are never
            # sent to or echoed by the mocked model.
            output = draft_for(smoke_expected_package(fixture_context, module))
        else:
            output = fixture_report(user["candidate"])
        return good_response(output)

    client.chat_completion.side_effect = fixture_reply
    root = tmp_path / "receipts"
    assert module.main(["--allow-paid", "--output-root", str(root)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["job_state"] == "COMPLETED"
    assert receipt["job_reload_verified"] is True
    assert receipt["calls_started"] == ["COMPILE", "AUDIT"]
    assert receipt["quality"]["characters"] == 2
    path = Path(receipt["receipt_path"])
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    assert (path.parent / "synthetic-authoring.sqlite3").stat().st_mode & 0o777 == 0o600
    assert "SYNTHETIC_KEY_SENTINEL" not in path.read_text()
    config_loader.assert_called_once_with("aliyun_bailian")
    assert client.chat_completion.await_count == 2


def test_real_smoke_cli_persists_unknown_failure_without_retries(monkeypatch, tmp_path, capsys):
    module = smoke_module()
    monkeypatch.setattr(module, "load_selected_config", Mock(return_value=fixture_config()))
    _, client = stub_sdk(monkeypatch, exception=RuntimeError("PRIVATE_PROVIDER_ERROR"))
    assert module.main(["--allow-paid", "--output-root", str(tmp_path / "receipts")]) == 3
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["usage_known"] is False
    assert receipt["job_state"] == "NEEDS_RECONCILIATION"
    assert Decimal(receipt["charged_cost_cny"]) > 0
    assert "PRIVATE_PROVIDER_ERROR" not in canonical_json(receipt)
    assert Path(receipt["receipt_path"]).is_file()
    client.chat_completion.assert_awaited_once()


def test_real_smoke_rejects_repository_output_before_loading_credentials(monkeypatch, capsys):
    module = smoke_module()
    loader = Mock()
    monkeypatch.setattr(module, "load_selected_config", loader)
    assert module.main(["--allow-paid", "--output-root", str(module.REPOSITORY_ROOT / "unsafe-smoke")]) == 3
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["result_code"] == "SMOKE_OUTPUT_MUST_BE_OUTSIDE_REPOSITORY"
    loader.assert_not_called()


def test_real_smoke_interruption_conservatively_keeps_usage(monkeypatch):
    module = smoke_module()
    context, _ = synthetic_context_and_package()
    _, client = stub_sdk(monkeypatch, exception=asyncio.CancelledError())
    model = module.SmokeAuthoringModel(fixture_config())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(model.call("COMPILE", context))
    receipt = module.sanitized_receipt(model, result_code="SMOKE_WORKFLOW_FAILED")
    assert receipt["usage_known"] is False
    assert receipt["charged_cost_cny"] == receipt["total_reserved_cny"]
    assert Decimal(receipt["charged_cost_cny"]) > 0


def test_compiler_diagnostics_distinguish_incorrect_reference_from_changed_name(monkeypatch):
    context, package = synthetic_context_and_package()
    package["introduction"]["sources"] = [{"source_id": "fixture-source", "anchor": "L1"}]
    package["characters"][1]["name"] = "CHANGED_NAME_PRIVATE_SENTINEL"
    expected = [{"code": "TEXT_OUTSIDE_REFERENCES", "entity_path": "/introduction/text"},
                {"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/characters/1/name"}]
    response = good_response(draft_for(package))
    _, client = stub_sdk(monkeypatch, response)
    model = AuthoringModel(fixture_config())
    frozen_snapshot = model.snapshot()
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$") as caught:
        asyncio.run(model.call("COMPILE", context))
    assert caught.value.details == expected
    assert caught.value.receipt["output_diagnostics"] == expected
    assert caught.value.receipt["usage_known"] is True
    assert Decimal(caught.value.receipt["charged_cost_cny"]) == Decimal("0.00006")
    safe = canonical_json(caught.value.receipt)
    for secret in ("CHANGED_NAME_PRIVATE_SENTINEL", "PRIVATE_a_SENTINEL", package["introduction"]["text"]):
        assert secret not in safe
    assert model.snapshot() == frozen_snapshot
    client.chat_completion.assert_awaited_once()


def test_compiler_diagnostics_are_limited_to_ten_fixed_positions():
    context, package = synthetic_context_and_package()
    for index in range(10):
        package["knowledge"].append(deepcopy(package["knowledge"][0]) | {"id": f"extra-{index}"})
    for item in package["knowledge"]:
        item["text"] = "PRIVATE_BOGUS_TEXT_SENTINEL"
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert caught.value.details == [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": f"/knowledge/{index}/text"}
                                    for index in range(10)]
    assert "PRIVATE_BOGUS_TEXT_SENTINEL" not in canonical_json(caught.value.details)


@pytest.mark.parametrize("diagnostics", [
    None, {}, (), ["PRIVATE_DIAGNOSTIC_SENTINEL"],
    [{"code": "OTHER", "entity_path": "/introduction/text"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/PRIVATE_DIAGNOSTIC_SENTINEL"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/characters/8/name"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/characters/0/text"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/knowledge/5000/text"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/truth/1000/text"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/knowledge/00/text"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/introduction/text", "text": "PRIVATE_DIAGNOSTIC_SENTINEL"}],
    [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/introduction/text"}] * 11,
])
def test_output_diagnostics_reject_extra_content_and_unknown_paths(diagnostics):
    with pytest.raises(ValueError, match="^AUTHORING_DIAGNOSTICS_INVALID$") as caught:
        validate_output_diagnostics(diagnostics)
    assert "PRIVATE_DIAGNOSTIC_SENTINEL" not in str(caught.value)


def test_output_diagnostics_accept_known_boundary_positions_without_mutation():
    diagnostics = [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/characters/7/name"},
                   {"code": "TEXT_OUTSIDE_REFERENCES", "entity_path": "/knowledge/4999/text"},
                   {"code": "TEXT_OUTSIDE_REFERENCES", "entity_path": "/evidence/4999/text"},
                   {"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/truth/999/text"}]
    result = validate_output_diagnostics(diagnostics)
    result[0]["entity_path"] = "/introduction/text"
    assert diagnostics[0]["entity_path"] == "/characters/7/name"
    assert validate_output_diagnostics([]) == []


def test_compiler_v4_indexes_only_unique_usable_headings_without_changing_materials():
    context, _ = synthetic_context_and_package()
    text = ("# Unique title\nPRIVATE_BODY_SENTINEL\n## Repeated\nfirst\n# Repeated\nsecond\n"
            "# L1\nnot a usable heading anchor\n# L1-L3\nnot a usable heading anchor\n"
            "# \nempty\n# " + "x" * 257 + "\ntoo long\n### Final section\nlast body\n")
    context["materials"][0]["text"] = text
    before = deepcopy(context)
    prepared = AuthoringModel(fixture_config()).prepare("COMPILE", context)
    user = json.loads(prepared.messages[1].content)
    assert user["available_locators"] == [{"source_id": "fixture-source", "anchors": ["Unique title", "Final section"]}]
    assert prepared.context == before == context
    assert user["input"] == {key: before[key] for key in ("title", "player_count", "materials", "notes")}
    assert user["input"]["materials"][0]["text"] == text
    assert "PRIVATE_BODY_SENTINEL" not in canonical_json(user["available_locators"])
    assert "available_locators" in prepared.messages[0].content
    assert "同级或更高级标题之前" in prepared.messages[0].content


def test_compiler_v4_does_not_claim_locator_index_for_plain_text():
    context, _ = synthetic_context_and_package()
    context["materials"][0]["text"] = "Plain text without headings.\nSecond line."
    prepared = AuthoringModel(fixture_config()).prepare("COMPILE", context)
    user = json.loads(prepared.messages[1].content)
    assert user["available_locators"] == []
    assert "没有服务端行号索引" in prepared.messages[0].content
    assert locate_text(SourceReference(source_id="fixture-source", anchor="L2"),
                       {"fixture-source": context["materials"][0]["text"]}) == "Second line."


def test_compiler_v4_indexes_each_selected_source_independently():
    context, _ = synthetic_context_and_package()
    context["materials"][0]["text"] = "# Shared title\nFirst source.\n"
    context["sources"].append(deepcopy(context["sources"][0]) | {"id": "second-source", "relative_path": "second.md"})
    context["source_ids"].append("second-source")
    context["materials"].append({"source_id": "second-source", "text": "# Shared title\nSecond source.\n"})
    user = json.loads(AuthoringModel(fixture_config()).prepare("COMPILE", context).messages[1].content)
    assert user["available_locators"] == [{"source_id": source_id, "anchors": ["Shared title"]}
                                           for source_id in context["source_ids"]]


def test_compiler_v4_locator_index_is_included_in_token_reservation(monkeypatch):
    context, _ = synthetic_context_and_package()
    context["materials"][0]["text"] = "\n".join(f"# Heading {index}\nSynthetic text." for index in range(20))
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("COMPILE", context)
    user = json.loads(prepared.messages[1].content)
    user.pop("available_locators")
    without_index = len(prepared.messages[0].content.encode()) + len(canonical_json(user).encode()) + 512
    assert prepared.input_tokens > without_index
    assert prepared.reservation.prompt_tokens == prepared.input_tokens
    monkeypatch.setattr(authoring_model, "MAX_INPUT_TOKENS", without_index)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_INPUT_TOO_LARGE$"):
        model.prepare("COMPILE", context)


def test_compiler_v1_v2_v3_are_retained_and_v4_changes_only_the_prompt_contract():
    model = AuthoringModel(fixture_config())
    old_prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v1.txt").read_bytes()
    v2_prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v2.txt").read_bytes()
    v3_prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v3.txt").read_bytes()
    new_prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v4.txt").read_bytes()
    snapshot = model.snapshot()
    assert snapshot["schema_version"] == "authoring-model/1.1"
    assert snapshot["request_contract"] == "bailian-authoring-json/1.1"
    assert snapshot["prompt_hashes"]["COMPILE"] == sha256(new_prompt).hexdigest()
    assert snapshot["prompt_hashes"]["COMPILE"] != sha256(old_prompt).hexdigest()
    assert snapshot["prompt_hashes"]["COMPILE"] != sha256(v2_prompt).hexdigest()
    assert snapshot["prompt_hashes"]["COMPILE"] != sha256(v3_prompt).hexdigest()
    assert sha256(old_prompt).hexdigest() == "93523bf689488b7261980a3d90efc38d849ece6dfc6160808bc64ae8c27fcfe1"
    assert sha256(v2_prompt).hexdigest() == "c25d477dbfb11c7963bb24bb8696ac4449257b5600e87de3f456c052f3990b67"
    assert sha256(v3_prompt).hexdigest() == "d5f04d6642ae90a88577c07f1a9f9a999f6a13e44c136ba672e7a31aab443a85"
    assert b"available_locators" not in old_prompt
    assert snapshot["prompt_hashes"]["AUDIT"] == sha256(
        (authoring_model.PROMPT_DIRECTORY / "audit_system_v3.txt").read_bytes()).hexdigest()
    context, package = synthetic_context_and_package()
    audit = model.prepare("AUDIT", context, package)
    assert "available_locators" not in json.loads(audit.messages[1].content)


def test_historical_compiler_binding_diagnostics_identify_fields_without_values():
    context, package = synthetic_context_and_package()
    package["title"] = "PRIVATE_CHANGED_TITLE_SENTINEL"
    package["sources"][0]["sha256"] = "a" * 64
    with pytest.raises(AuthoringModelError, match="^COMPILER_INPUT_BINDING_MISMATCH$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    diagnostics = [{"code": "INPUT_BINDING_MISMATCH", "entity_path": "/title"},
                   {"code": "INPUT_BINDING_MISMATCH", "entity_path": "/sources"}]
    assert caught.value.details == diagnostics
    safe = canonical_json(caught.value.details)
    for value in ("PRIVATE_CHANGED_TITLE_SENTINEL", "a" * 64, "PRIVATE_a_SENTINEL"):
        assert value not in safe


@pytest.mark.parametrize("diagnostic", [
    {"code": "INPUT_BINDING_MISMATCH", "entity_path": "/introduction/text"},
    {"code": "INPUT_BINDING_MISMATCH", "entity_path": "/sources/0/sha256"},
    {"code": "INPUT_BINDING_MISMATCH", "entity_path": "/PRIVATE_PATH_SENTINEL"},
    {"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/title"},
    {"code": "TEXT_OUTSIDE_REFERENCES", "entity_path": "/sources"},
    {"code": "INPUT_BINDING_MISMATCH", "entity_path": "/title", "actual": "PRIVATE_VALUE_SENTINEL"},
])
def test_compiler_binding_diagnostics_reject_unapproved_paths_and_data(diagnostic):
    with pytest.raises(ValueError, match="^AUTHORING_DIAGNOSTICS_INVALID$"):
        validate_output_diagnostics([diagnostic])


def test_optional_null_page_count_is_accepted_without_rewriting_raw_package_hash():
    context, package = synthetic_context_and_package()
    assert "page_count" not in context["sources"][0]
    original_hash = content_hash(package)
    package["sources"][0]["page_count"] = None
    assert validate_package(package)["valid"]
    assert SourceFileV11.model_validate(package["sources"][0]).model_dump(exclude_none=True) == (
        SourceFileV11.model_validate(context["sources"][0]).model_dump(exclude_none=True))
    result = parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert result["package"] == package
    assert result["package"]["sources"][0]["page_count"] is None
    assert content_hash(result["package"]) == content_hash(package) != original_hash
    assert "page_count" not in context["sources"][0]


@pytest.mark.parametrize("patch", [
    {"sha256": "f" * 64}, {"relative_path": "different.md"}, {"original_source_ids": ["extra-original"]},
    {"page_count": 1}, {"page_count": 1.0}, {"media_type": "text/plain"},
    {"private_extra": "PRIVATE_EXTRA_SENTINEL"}, {"provenance_note": None},
])
def test_source_null_equivalence_does_not_relax_other_metadata_or_schema(patch):
    context, package = synthetic_context_and_package()
    package["sources"][0].update(patch)
    with pytest.raises(AuthoringModelError, match="^COMPILER_INPUT_BINDING_MISMATCH$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert caught.value.details == [{"code": "INPUT_BINDING_MISMATCH", "entity_path": "/sources"}]
    assert "PRIVATE_EXTRA_SENTINEL" not in canonical_json(caught.value.details)


def normalized_context_and_package():
    context, package = synthetic_context_and_package()
    primary = context["sources"][0]
    originals = [deepcopy(primary) | {"id": f"original-{index}", "relative_path": f"original-{index}.md"}
                 for index in range(2)]
    primary.update(kind="normalized", original_source_ids=[item["id"] for item in originals])
    context["sources"].extend(originals)
    package["sources"] = deepcopy(context["sources"])
    return context, package


@pytest.mark.parametrize("change", ["source-order", "original-order", "missing-original", "different-original"])
def test_source_semantic_comparison_keeps_array_order_and_original_relationships(change):
    context, package = normalized_context_and_package()
    assert parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)["status"] == "CANDIDATE"
    if change == "source-order":
        package["sources"].reverse()
    elif change == "original-order":
        package["sources"][0]["original_source_ids"].reverse()
    elif change == "missing-original":
        package["sources"][0]["original_source_ids"].pop()
    else:
        package["sources"][0]["original_source_ids"][0] = "other-original"
    with pytest.raises(AuthoringModelError, match="^COMPILER_INPUT_BINDING_MISMATCH$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert caught.value.details == [{"code": "INPUT_BINDING_MISMATCH", "entity_path": "/sources"}]


def test_source_semantic_comparison_does_not_accept_null_supplement_provenance():
    context, package = synthetic_context_and_package()
    context["sources"][0].update(kind="supplement", provenance_note="Synthetic editorial addition, not recovered original.")
    package["sources"] = deepcopy(context["sources"])
    assert parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)["status"] == "CANDIDATE"
    package["sources"][0]["provenance_note"] = None
    with pytest.raises(AuthoringModelError, match="^COMPILER_INPUT_BINDING_MISMATCH$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert caught.value.details == [{"code": "INPUT_BINDING_MISMATCH", "entity_path": "/sources"}]


def test_draft_without_fixed_metadata_assembles_exact_historical_domain_package():
    context, package = synthetic_context_and_package()
    draft = draft_for(package)
    before = deepcopy(draft)
    result = parse_compiler_draft(json.dumps(draft), context)
    expected = {"status": "CANDIDATE", "package": package, "blockers": []}
    assert result == parse_compiler_output(expected, context) == expected
    assert content_hash(result["package"]) == content_hash(package)
    assert draft == before
    assert set(draft) == {"schema_version", "status", "content", "blockers"}
    assert set(draft["content"]) == set(CompilerContent.model_fields)
    assert set(draft["content"]).isdisjoint({"script_key", "title", "content_version", "player_count", "sources"})


@pytest.mark.parametrize("field", ["script_key", "title", "content_version", "player_count", "sources", "package"])
@pytest.mark.parametrize("location", ["top", "content"])
def test_draft_rejects_fixed_metadata_injection_without_echoing_values(field, location):
    context, package = synthetic_context_and_package()
    draft = draft_for(package)
    target = draft if location == "top" else draft["content"]
    target[field] = "PRIVATE_FIXED_METADATA_SENTINEL"
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$") as caught:
        parse_compiler_draft(draft, context)
    assert caught.value.details == []
    assert "PRIVATE_FIXED_METADATA_SENTINEL" not in str(caught.value) + canonical_json(caught.value.receipt)


@pytest.mark.parametrize("patch", [
    {"schema_version": "script-package/1.1"}, {"schema_version": "compiler-draft/2.0"},
    {"content": None}, {"status": "BLOCKED"}, {"blockers": blocked_output()["blockers"]},
    {"status": "APPROVED"}, {"approved": True},
])
def test_draft_candidate_has_a_strict_version_and_branch(patch):
    context, package = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$"):
        parse_compiler_draft(draft_for(package) | patch, context)


@pytest.mark.parametrize("field", list(CompilerContent.model_fields))
def test_draft_requires_every_content_field(field):
    context, package = synthetic_context_and_package()
    draft = draft_for(package)
    draft["content"].pop(field)
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$"):
        parse_compiler_draft(draft, context)


@pytest.mark.parametrize("mutation", ["approval", "runtime-action", "number-coercion", "nested-metadata"])
def test_draft_does_not_extend_entity_permissions_or_coerce_values(mutation):
    context, package = synthetic_context_and_package()
    draft = draft_for(package)
    content = draft["content"]
    if mutation == "approval":
        content["settlement"]["publication_ready"] = True
    elif mutation == "runtime-action":
        content["knowledge"][0]["release"]["resource_cost"] = 2
    elif mutation == "number-coercion":
        content["introduction"]["sources"][0]["page"] = "1"
    else:
        content["characters"][0]["sources"][0]["sha256"] = "f" * 64
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$"):
        parse_compiler_draft(draft, context)


def test_draft_content_reuses_full_package_field_types_and_collection_limits():
    from src.schemas.script_package import ScriptPackageV11

    draft_schema = CompilerContent.model_json_schema()
    full_schema = ScriptPackageV11.model_json_schema()
    for field in CompilerContent.model_fields:
        assert draft_schema["properties"][field] == full_schema["properties"][field]
        assert field in draft_schema["required"]
    assert draft_schema["additionalProperties"] is False


def test_draft_blocked_branch_uses_the_same_authorized_locator_rules():
    context, _ = synthetic_context_and_package()
    assert parse_compiler_draft(blocked_draft(), context) == parse_compiler_output(blocked_output(), context)
    changed = blocked_draft()
    changed["blockers"][0]["sources"][0]["source_id"] = "unseen-original"
    with pytest.raises(AuthoringModelError, match="^COMPILER_REFERENCE_UNAUTHORIZED$"):
        parse_compiler_draft(changed, context)


@pytest.mark.parametrize("patch", [{"blockers": []}, {"content": {}}, {"status": "CANDIDATE"}, {"package": None}])
def test_draft_blocked_branch_does_not_accept_mixed_or_empty_results(patch):
    context, _ = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$"):
        parse_compiler_draft(blocked_draft() | patch, context)


@pytest.mark.parametrize("branch", ["CANDIDATE", "BLOCKED"])
def test_old_domain_wire_is_not_silently_accepted_as_new_draft(monkeypatch, branch):
    context, package = synthetic_context_and_package()
    domain = {"status": "CANDIDATE", "package": package, "blockers": []} if branch == "CANDIDATE" else blocked_output()
    assert parse_compiler_output(domain, context)["status"] == branch
    response = good_response(domain)
    assert json.loads(response.content) == domain
    _, client = stub_sdk(monkeypatch, response)
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$") as caught:
        asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))
    assert caught.value.receipt["usage_known"] is True
    assert caught.value.receipt["result_code"] == "COMPILER_DRAFT_INVALID"
    assert caught.value.receipt["output_diagnostics"] == []
    assert "PRIVATE_a_SENTINEL" not in canonical_json(caught.value.receipt)
    client.chat_completion.assert_awaited_once()


def test_draft_preserves_server_source_metadata_and_raw_content_without_aliasing():
    context, package = normalized_context_and_package()
    context["sources"][1]["page_count"] = None
    supplement = {"id": "editorial-source", "relative_path": "private/edited.md", "sha256": "e" * 64,
                  "kind": "supplement", "media_type": "text/markdown", "original_source_ids": [],
                  "provenance_note": "Synthetic editor note; not recovered original or approval."}
    context["sources"].append(supplement)
    context["source_ids"].append(supplement["id"])
    context["materials"].append({"source_id": supplement["id"], "text": "# Editorial note\nSynthetic addition.\n"})
    package["sources"] = deepcopy(context["sources"])
    package["introduction"]["sources"][0]["page"] = None
    draft = draft_for(package)
    before_context, before_draft = deepcopy(context), deepcopy(draft)
    result = parse_compiler_draft(draft, context)
    assert result["package"] == package
    assert content_hash(result["package"]) == content_hash(package)
    assert result["package"]["sources"][1]["page_count"] is None
    assert result["package"]["introduction"]["sources"][0]["page"] is None
    # Omitted defaults remain omitted; metadata order and editor attribution are
    # copied exactly, not synthesized from the model's schema representation.
    assert "required_public_evidence_ids" not in result["package"]["evidence"][0]["release"]
    result["package"]["sources"][0]["original_source_ids"].reverse()
    result["package"]["sources"][-1]["provenance_note"] = "changed"
    result["package"]["introduction"]["text"] = "changed"
    assert context == before_context and draft == before_draft


def test_compiler_projection_omits_fixed_metadata_but_keeps_it_frozen_for_assembly():
    context, package = normalized_context_and_package()
    context["script_key"] = "PRIVATE_SCRIPT_KEY_SENTINEL"
    context["content_version"] = "PRIVATE_VERSION_SENTINEL"
    context["sources"][0]["relative_path"] = "PRIVATE_PATH_SENTINEL.md"
    context["sources"][0]["sha256"] = "f" * 64
    before = deepcopy(context)
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("COMPILE", context)
    user = json.loads(prepared.messages[1].content)
    assert set(user) == {"task", "input", "available_locators"}
    assert user["input"] == {key: context[key] for key in ("title", "player_count", "materials", "notes")}
    schema = json.loads(prepared.messages[0].content.split("\nJSON Schema：\n", 1)[1])
    assert schema == {"output": CompilerDraftOutput.model_json_schema()}
    for value in (context["script_key"], context["content_version"], context["bundle_hash"],
                  context["sources"][0]["relative_path"], context["sources"][0]["sha256"], "original-0", "original-1"):
        assert value not in prepared.messages[1].content
    context["sources"][0]["original_source_ids"].reverse()
    assert prepared.context == before
    assembled = parse_compiler_draft(draft_for(package), prepared.context)["package"]
    assert assembled["script_key"] == before["script_key"]
    assert assembled["content_version"] == before["content_version"]
    assert assembled["sources"] == before["sources"]
    assert prepared.reservation.prompt_tokens == sum(len(message.content.encode()) for message in prepared.messages) + 512
    assert prepared.request_contract["schema_hash"] == content_hash(schema)
    assert model.snapshot()["schema_hashes"]["COMPILE"] == content_hash(schema)


def test_draft_keeps_deterministic_package_validation_after_assembly():
    context, package = synthetic_context_and_package()
    draft = draft_for(package)
    draft["content"]["initial_phase_id"] = "absent-phase"
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$"):
        parse_compiler_draft(draft, context)


@pytest.mark.parametrize("raw", [
    '```json\n{"schema_version":"compiler-draft/1.0"}\n```',
    '{"schema_version":"compiler-draft/1.0","schema_version":"compiler-draft/1.0"}',
    '[{"status":"BLOCKED"}]',
])
def test_draft_rejects_malformed_or_ambiguous_json(raw):
    context, _ = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^COMPILER_DRAFT_INVALID$"):
        parse_compiler_draft(raw, context)


@pytest.mark.parametrize("path,value,diagnostic", [
    (("phases", 0, "next_phase_id"), "PRIVATE_MISSING_PHASE", ("PHASE_NOT_FOUND", "/phases/0/next_phase_id")),
    (("initial_phase_id",), "PRIVATE_MISSING_PHASE", ("PHASE_NOT_FOUND", "/initial_phase_id")),
    (("knowledge", 0, "release", "phase_id"), "PRIVATE_MISSING_PHASE", ("PHASE_NOT_FOUND", "/knowledge/0/release/phase_id")),
    (("phases", 1, "next_phase_id"), "opening", ("PHASE_CYCLE", "/phases")),
    (("phases", 0, "next_phase_id"), None, ("UNREACHABLE_PHASE", "/phases")),
    (("settlement", "phase_id"), "opening", ("INVALID_SETTLEMENT_PHASE", "/settlement/phase_id")),
    (("settlement", "truth_ids"), ["answer", "answer"], ("DUPLICATE_REFERENCE", "/settlement/truth_ids")),
    (("settlement", "truth_ids"), ["PRIVATE_MISSING_TRUTH"], ("TRUTH_NOT_FOUND", "/settlement/truth_ids")),
    (("knowledge", 1, "id"), "memory-a", ("DUPLICATE_ID", "/knowledge/1/id")),
    (("evidence", 1, "character_id"), "a", ("INVALID_PUBLIC_SCOPE", "/evidence/1")),
    (("knowledge", 0, "character_id"), "PRIVATE_MISSING_CHARACTER", ("INVALID_PRIVATE_SCOPE", "/knowledge/0")),
    (("knowledge", 0, "release", "phase_id"), "ending", ("INITIAL_KNOWLEDGE_MISSING", "/characters/0")),
    (("evidence", 0, "disclosure"), "KEEP_PRIVATE", ("UNSATISFIABLE_RELEASE", "/evidence/1/release")),
    (("evidence", 1, "release", "required_public_evidence_ids"), ["PRIVATE_MISSING_EVIDENCE"],
     ("EVIDENCE_NOT_FOUND", "/evidence/1/release")),
    (("evidence", 0, "release", "required_public_evidence_ids"), ["clock"], ("UNREACHABLE_EVIDENCE", "/evidence")),
    (("evidence", 1, "release", "required_public_evidence_ids"), ["clock", "clock"],
     ("DUPLICATE_REFERENCE", "/evidence/1/release")),
    (("introduction", "sources", 0, "source_id"), "PRIVATE_MISSING_SOURCE", ("SOURCE_NOT_FOUND", "/introduction/sources/0")),
    (("introduction", "sources", 0, "anchor"), None, ("SOURCE_LOCATION_MISSING", "/introduction/sources/0")),
])
def test_package_rule_diagnostics_explain_only_safe_fixed_positions(path, value, diagnostic):
    context, package = synthetic_context_and_package()
    target = package
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    draft = draft_for(package)
    # These cross-field errors satisfy the wire types and enums. They still
    # need the same deterministic rejection after any prompt improvement.
    CompilerDraftOutput.model_validate(draft)
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        parse_compiler_draft(draft, context)
    assert {"code": "PACKAGE_" + diagnostic[0], "entity_path": diagnostic[1]} in caught.value.details
    safe = str(caught.value) + canonical_json(caught.value.details)
    assert "PRIVATE_MISSING_" not in safe and "SYSTEM_TRUTH_SENTINEL" not in safe
    assert all(set(item) == {"code", "entity_path"} for item in caught.value.details)


def test_package_rule_diagnostics_cover_character_count_and_source_page_range():
    context, package = synthetic_context_and_package()
    package["characters"].append(deepcopy(package["characters"][0]) | {"id": "c"})
    context["sources"][0]["page_count"] = 1
    package["sources"] = deepcopy(context["sources"])
    package["introduction"]["sources"][0]["page"] = 2
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        parse_compiler_draft(draft_for(package), context)
    assert {"code": "PACKAGE_CHARACTER_COUNT_MISMATCH", "entity_path": "/player_count"} in caught.value.details
    assert {"code": "PACKAGE_SOURCE_PAGE_OUT_OF_RANGE", "entity_path": "/introduction/sources/0"} in caught.value.details


def test_historical_domain_schema_diagnostics_never_copy_unknown_field_names():
    context, package = synthetic_context_and_package()
    package["knowledge"][0]["PRIVATE_UNKNOWN_FIELD_SENTINEL"] = "PRIVATE_UNKNOWN_VALUE_SENTINEL"
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    assert caught.value.details == [{"code": "PACKAGE_SCHEMA_INVALID", "entity_path": "/"}]
    assert "PRIVATE_UNKNOWN" not in str(caught.value) + canonical_json(caught.value.details)


@pytest.mark.parametrize("code,path", [
    ("SCHEMA_INVALID", "/"), ("DUPLICATE_ID", "/sources/499/id"), ("DUPLICATE_ID", "/characters/7/id"),
    ("DUPLICATE_ID", "/phases/99/id"), ("DUPLICATE_ID", "/knowledge/4999/id"),
    ("DUPLICATE_ID", "/evidence/4999/id"), ("DUPLICATE_ID", "/truth/999/id"),
    ("SOURCE_NOT_FOUND", "/settlement/instructions/sources/99"),
    ("SOURCE_PAGE_OUT_OF_RANGE", "/truth/999/sources/99"), ("SOURCE_LOCATION_MISSING", "/phases/99/sources/99"),
    ("DUPLICATE_SOURCE_PATH", "/sources/499"), ("ORIGINAL_SOURCE_MISSING", "/sources/499"),
    ("INVALID_SOURCE_LINK", "/sources/499"), ("CHARACTER_COUNT_MISMATCH", "/player_count"),
    ("PHASE_NOT_FOUND", "/initial_phase_id"), ("PHASE_NOT_FOUND", "/phases/99/next_phase_id"),
    ("PHASE_NOT_FOUND", "/evidence/4999/release/phase_id"), ("PHASE_CYCLE", "/phases"),
    ("UNREACHABLE_PHASE", "/phases"), ("INVALID_SETTLEMENT_PHASE", "/settlement/phase_id"),
    ("DUPLICATE_REFERENCE", "/settlement/truth_ids"), ("DUPLICATE_REFERENCE", "/knowledge/4999/release"),
    ("TRUTH_NOT_FOUND", "/settlement/truth_ids"), ("INVALID_PUBLIC_SCOPE", "/knowledge/4999"),
    ("INVALID_PRIVATE_SCOPE", "/evidence/4999"), ("EVIDENCE_NOT_FOUND", "/evidence/4999/release"),
    ("UNSATISFIABLE_RELEASE", "/knowledge/4999/release"), ("UNREACHABLE_EVIDENCE", "/evidence"),
    ("INITIAL_KNOWLEDGE_MISSING", "/characters/7"),
])
def test_package_diagnostic_whitelist_accepts_known_rules_at_collection_limits(code, path):
    diagnostic = {"code": "PACKAGE_" + code, "entity_path": path}
    assert validate_output_diagnostics([diagnostic]) == [diagnostic]


@pytest.mark.parametrize("code,path", [
    ("PRIVATE_UNKNOWN_RULE", "/phases"), ("SCHEMA_INVALID", "/PRIVATE_FIELD_SENTINEL"),
    ("DUPLICATE_ID", "/sources/500/id"), ("DUPLICATE_ID", "/characters/8/id"),
    ("DUPLICATE_ID", "/phases/100/id"), ("DUPLICATE_ID", "/knowledge/5000/id"),
    ("DUPLICATE_ID", "/evidence/5000/id"), ("DUPLICATE_ID", "/truth/1000/id"),
    ("DUPLICATE_ID", "/knowledge/00/id"), ("DUPLICATE_ID", "/knowledge/-1/id"),
    ("DUPLICATE_ID", "/knowledge/１/id"), ("DUPLICATE_ID", "/knowledge/0/text"),
    ("SOURCE_NOT_FOUND", "/introduction/sources/100"), ("SOURCE_NOT_FOUND", "/sources/0/sources/0"),
    ("SOURCE_PAGE_OUT_OF_RANGE", "/characters/8/sources/0"),
    ("SOURCE_LOCATION_MISSING", "/introduction/sources/00"),
    ("DUPLICATE_SOURCE_PATH", "/sources/0/relative_path"), ("ORIGINAL_SOURCE_MISSING", "/knowledge/0"),
    ("INVALID_SOURCE_LINK", "/sources/0/original_source_ids"), ("CHARACTER_COUNT_MISMATCH", "/characters"),
    ("PHASE_NOT_FOUND", "/phases/100/next_phase_id"), ("PHASE_NOT_FOUND", "/knowledge/0/phase_id"),
    ("PHASE_NOT_FOUND", "/knowledge/0/release/PRIVATE_PHASE_ID_SENTINEL"),
    ("PHASE_NOT_FOUND", "/phases/PRIVATE_MODEL_ID_SENTINEL/next_phase_id"),
    ("PHASE_CYCLE", "/phases/0"), ("UNREACHABLE_PHASE", "/evidence"),
    ("INVALID_SETTLEMENT_PHASE", "/initial_phase_id"), ("DUPLICATE_REFERENCE", "/truth/0"),
    ("TRUTH_NOT_FOUND", "/settlement/truth_ids/0"), ("INVALID_PUBLIC_SCOPE", "/knowledge/5000"),
    ("INVALID_PRIVATE_SCOPE", "/characters/0"), ("EVIDENCE_NOT_FOUND", "/evidence"),
    ("UNSATISFIABLE_RELEASE", "/settlement/truth_ids"), ("UNREACHABLE_EVIDENCE", "/evidence/0"),
    ("INITIAL_KNOWLEDGE_MISSING", "/characters/8"), ("PHASE_NOT_FOUND", "/initial_phase_id\n"),
])
def test_package_diagnostic_whitelist_rejects_unknown_wrong_and_unbounded_paths(code, path):
    with pytest.raises(ValueError, match="^AUTHORING_DIAGNOSTICS_INVALID$") as caught:
        validate_output_diagnostics([{"code": "PACKAGE_" + code, "entity_path": path}])
    assert "PRIVATE_" not in str(caught.value)


def test_package_rule_diagnostics_drop_report_details_unknowns_and_duplicate_items(monkeypatch):
    context, package = synthetic_context_and_package()
    phase = {"code": "PHASE_NOT_FOUND", "severity": "ERROR", "path": "/knowledge/0/release/phase_id",
             "message": "PRIVATE_VALIDATOR_TEXT_SENTINEL", "sources": [{"source_id": "PRIVATE_MODEL_ID_SENTINEL"}]}
    report = {"valid": False, "issues": [
        phase, deepcopy(phase), {**phase, "code": "PRIVATE_UNKNOWN_CODE_SENTINEL"},
        {**phase, "path": "/knowledge/PRIVATE_MODEL_ID_SENTINEL/release/phase_id"},
        {**phase, "code": "INITIAL_KNOWLEDGE_MISSING"}, {**phase, "severity": "WARNING"},
        {**phase, "path": "/evidence/5000/release/phase_id"}, None, "PRIVATE_RAW_SENTINEL",
        {**phase, "code": "SCHEMA_INVALID", "path": "/PRIVATE_FIELD_SENTINEL"},
    ]}
    monkeypatch.setattr(authoring_model, "validate_package", Mock(return_value=report))
    _, client = stub_sdk(monkeypatch, good_response(draft_for(package)))
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        asyncio.run(AuthoringModel(fixture_config()).call("COMPILE", context))
    expected = [{"code": "PACKAGE_PHASE_NOT_FOUND", "entity_path": "/knowledge/0/release/phase_id"},
                {"code": "PACKAGE_SCHEMA_INVALID", "entity_path": "/"}]
    assert caught.value.details == caught.value.receipt["output_diagnostics"] == expected
    assert "PRIVATE_" not in canonical_json(caught.value.receipt)
    assert caught.value.receipt["usage_known"] is True
    client.chat_completion.assert_awaited_once()


def test_package_rule_diagnostics_are_bounded_to_ten_and_do_not_rewrite_request_contract():
    context, package = synthetic_context_and_package()
    for index in range(12):
        package["knowledge"].append(deepcopy(package["knowledge"][0]) | {
            "id": f"extra-{index}", "release": {"phase_id": "PRIVATE_MISSING_PHASE"}})
    model = AuthoringModel(fixture_config())
    before = model.snapshot()
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        parse_compiler_draft(draft_for(package), context)
    assert caught.value.details == [
        {"code": "PACKAGE_PHASE_NOT_FOUND", "entity_path": f"/knowledge/{index}/release/phase_id"}
        for index in range(2, 12)]
    assert model.snapshot() == before
    assert before["schema_version"] == "authoring-model/1.1"
    assert before["request_contract"] == "bailian-authoring-json/1.1"
    assert before["prompt_hashes"]["COMPILE"] == sha256(
        (authoring_model.PROMPT_DIRECTORY / "compiler_system_v4.txt").read_bytes()).hexdigest()
    assert "PACKAGE_PHASE_NOT_FOUND" not in canonical_json(authoring_model._schema("COMPILE"))


def test_package_rule_failure_retains_known_usage_and_sanitized_receipt_without_retry(monkeypatch):
    context, package = synthetic_context_and_package()
    package["knowledge"][0]["release"]["phase_id"] = "PRIVATE_INVALID_PHASE_SENTINEL"
    _, client = stub_sdk(monkeypatch, good_response(draft_for(package)))
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("COMPILE", context)
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        asyncio.run(model.call("COMPILE", context, prepared=prepared))
    expected = [{"code": "PACKAGE_PHASE_NOT_FOUND", "entity_path": "/knowledge/0/release/phase_id"},
                {"code": "PACKAGE_INITIAL_KNOWLEDGE_MISSING", "entity_path": "/characters/0"}]
    assert caught.value.details == caught.value.receipt["output_diagnostics"] == expected
    assert caught.value.receipt["usage_known"] is True
    assert Decimal(caught.value.receipt["charged_cost_cny"]) == Decimal("0.00006")
    assert caught.value.receipt["reservation"] == prepared.reservation.to_metadata()
    assert caught.value.receipt["prompt_hash"] == prepared.prompt_hash
    assert caught.value.receipt["contract_hash"] == prepared.contract_hash
    assert caught.value.receipt["result_code"] == "COMPILER_PACKAGE_INVALID"
    for forbidden in ("PRIVATE_INVALID_PHASE_SENTINEL", "PRIVATE_a_SENTINEL", "SYSTEM_TRUTH_SENTINEL"):
        assert forbidden not in str(caught.value) + canonical_json(caught.value.receipt)
    with pytest.raises(ValueError, match="^AUTHORING_DIAGNOSTICS_INVALID$"):
        validate_output_diagnostics([expected[0] | {"text": "PRIVATE_RAW_SENTINEL"}])
    client.chat_completion.assert_awaited_once()


def test_compiler_v4_sends_all_existing_cross_field_rules_to_the_model(monkeypatch):
    context, _ = synthetic_context_and_package()
    _, client = stub_sdk(monkeypatch, good_response(blocked_draft()))
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("COMPILE", context)
    result = asyncio.run(model.call("COMPILE", context, prepared=prepared))
    messages = client.chat_completion.call_args.args[0]
    assert messages == list(prepared.messages)
    system = messages[0].content
    rules = [
        "同一集合内每个实体的 id 必须唯一",
        "所有阶段、角色、证据与真相引用都必须指向对应集合中实际声明的 id",
        "从它沿 next_phase_id 必须能依次到达所有阶段",
        "最后阶段的 next_phase_id 必须为 null",
        "settlement.phase_id 必须等于上述完整线性链的最后阶段",
        "settlement.truth_ids 必须非空、不能重复",
        "visibility 为 PUBLIC 时，character_id 必须为 null，disclosure 必须为 PUBLIC",
        "disclosure 必须为 MAY_SHARE、MUST_SHARE 或 KEEP_PRIVATE，不能为 PUBLIC",
        "每个角色至少有一条属于自己的 knowledge",
        "release.phase_id 等于 initial_phase_id",
        "release.required_public_evidence_ids 为空数组或省略",
        "公共知识、evidence、其他角色的知识或后续阶段才解锁的知识都不能代替",
        "不能引用 disclosure 为 KEEP_PRIVATE 的证据",
        "不得自我依赖、形成依赖环或依赖不存在的证据",
        "所有前置证据必须都已公开，条件是 AND",
        "可以依赖更晚阶段出现的证据",
        "必须 BLOCKED，不能把后期记忆提前",
        "不能为了凑齐字段而编写故事或删掉限制",
    ]
    assert all(rule in system for rule in rules)
    assert '"compiler-draft/1.0"' in system
    assert "available_locators" in system
    assert "SYSTEM_TRUTH_SENTINEL" not in system
    assert "钟的维修卡" not in system
    assert result["output"]["status"] == "BLOCKED"
    assert result["receipt"]["prompt_hash"] == prepared.prompt_hash
    assert result["receipt"]["contract_hash"] == prepared.contract_hash
    assert prepared.input_tokens == sum(len(message.content.encode()) for message in messages) + 512
    client.chat_completion.assert_awaited_once()


def test_compiler_v4_rules_are_fully_included_in_pre_network_reservation(monkeypatch):
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("COMPILE", context)
    old_prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v3.txt").read_text("utf-8")
    old_system = old_prompt + "\nJSON Schema：\n" + canonical_json(authoring_model._schema("COMPILE"))
    previous_input_bound = len(old_system.encode()) + len(prepared.messages[1].content.encode()) + 512
    assert prepared.input_tokens > previous_input_bound
    assert prepared.reservation.prompt_tokens == prepared.input_tokens
    assert prepared.reservation == fixture_config().pricing.amount(prepared.input_tokens, 8192 + 16)
    factory, _ = stub_sdk(monkeypatch)
    monkeypatch.setattr(authoring_model, "MAX_INPUT_TOKENS", previous_input_bound)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_INPUT_TOO_LARGE$"):
        asyncio.run(model.call("COMPILE", context))
    factory.assert_not_called()


def test_compiler_v4_does_not_silently_resume_a_v3_prepared_call(monkeypatch):
    context, _ = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    old_prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v3.txt").read_text("utf-8")
    current_prompt = authoring_model._prompt
    with monkeypatch.context() as previous_version:
        previous_version.setattr(authoring_model, "_prompt", lambda step: old_prompt if step == "COMPILE" else current_prompt(step))
        previous = model.prepare("COMPILE", context)
    current = model.prepare("COMPILE", context)
    assert previous.request_contract == current.request_contract
    assert previous.prompt_hash != current.prompt_hash
    assert previous.contract_hash != current.contract_hash
    factory, _ = stub_sdk(monkeypatch)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_PREPARATION_CHANGED$"):
        asyncio.run(model.call("COMPILE", context, prepared=previous))
    factory.assert_not_called()


def test_audit_v3_sends_target_and_uniqueness_rules_and_accounts_for_every_input_byte(monkeypatch):
    context, package = synthetic_context_and_package()
    before = deepcopy(package)
    report = fixture_report(package)
    _, client = stub_sdk(monkeypatch, good_response(report))
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("AUDIT", context, package)
    result = asyncio.run(model.call("AUDIT", context, package, prepared=prepared))
    messages = client.chat_completion.call_args.args[0]
    assert messages == list(prepared.messages)
    system = messages[0].content
    rules = [
        "每类恰好一次", "finding.id 在全份报告内必须唯一",
        "target.collection 为 introduction 或 settlement 时，target.id 必须明确写 null",
        "target.id 必须是 candidate 对应集合中实际存在的实体 id，不能为 null",
        "每项 finding.sources 只能逐项引用该 target 已声明的完整定位",
        "settlement 对应 candidate.settlement.instructions.sources",
        "不得输出审批或发布字段", "findings 可以为空", "不得据此批准发布",
        "JSON 顶层必须且只能有 schema_version、summary、coverage、findings 四个字段",
        "不要给结果加 output 或 report 包装",
    ]
    assert all(rule in system for rule in rules)
    assert all(category in system for category in AUDIT_CATEGORIES)
    assert result["output"] == validate_model_audit(report, package)
    assert "publication_ready" not in result["output"]
    assert package == before
    assert prepared.input_tokens == sum(len(message.content.encode()) for message in messages) + 512
    assert prepared.reservation == fixture_config().pricing.amount(prepared.input_tokens, 4096 + 16)
    old_prompt = (authoring_model.PROMPT_DIRECTORY / "audit_system_v1.txt").read_text("utf-8")
    old_system = old_prompt + "\nJSON Schema：\n" + canonical_json(authoring_model._schema("AUDIT"))
    previous_input_bound = len(old_system.encode()) + len(prepared.messages[1].content.encode()) + 512
    assert previous_input_bound < prepared.input_tokens
    monkeypatch.setattr(authoring_model, "MAX_INPUT_TOKENS", previous_input_bound)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_INPUT_TOO_LARGE$"):
        asyncio.run(model.call("AUDIT", context, package))
    client.chat_completion.assert_awaited_once()


@pytest.mark.parametrize("mutation", ["introduction-id", "knowledge-null-id", "duplicate-id", "unauthorized-ref", "approval"])
def test_audit_v3_keeps_invalid_targets_duplicates_references_and_approval_blocked(monkeypatch, mutation):
    context, package = synthetic_context_and_package()
    report = fixture_report(package)
    finding = report["findings"][0]
    if mutation == "introduction-id":
        finding["target"]["id"] = "PRIVATE_INVENTED_TARGET_SENTINEL"
    elif mutation == "knowledge-null-id":
        finding["target"] = {"collection": "knowledge", "id": None}
    elif mutation == "duplicate-id":
        report["findings"].append(deepcopy(finding))
    elif mutation == "unauthorized-ref":
        finding["sources"][0]["anchor"] = "L1"
    else:
        report["publication_ready"] = True
    _, client = stub_sdk(monkeypatch, good_response(report))
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
        asyncio.run(AuthoringModel(fixture_config()).call("AUDIT", context, package))
    assert caught.value.receipt["usage_known"] is True
    expected = {
        "introduction-id": ("AUDIT_TARGET_INVALID", "/findings/0/target"),
        "knowledge-null-id": ("AUDIT_TARGET_INVALID", "/findings/0/target"),
        "duplicate-id": ("AUDIT_DUPLICATE_FINDING_ID", "/findings/1/id"),
        "unauthorized-ref": ("AUDIT_REFERENCE_MISMATCH", "/findings/0/sources/0"),
        "approval": ("AUDIT_SCHEMA_INVALID", "/"),
    }[mutation]
    assert caught.value.details == caught.value.receipt["output_diagnostics"] == [
        {"code": expected[0], "entity_path": expected[1]}]
    assert "PRIVATE_INVENTED_TARGET_SENTINEL" not in str(caught.value) + canonical_json(caught.value.receipt)
    client.chat_completion.assert_awaited_once()


@pytest.mark.parametrize("version,digest", [
    (1, "1b6a495c125825a59acdff9a1c2122b953e5b32a8de330298ff0ed7f972d450c"),
    (2, "4aff095498dd31165de18674d91f574648b765bb178225a857dd605bd85efbc7"),
])
def test_audit_v1_v2_are_preserved_and_cannot_silently_resume_as_v3(monkeypatch, version, digest):
    context, package = synthetic_context_and_package()
    model = AuthoringModel(fixture_config())
    old_prompt = (authoring_model.PROMPT_DIRECTORY / f"audit_system_v{version}.txt").read_text("utf-8")
    assert sha256(old_prompt.encode()).hexdigest() == digest
    current_prompt = authoring_model._prompt
    with monkeypatch.context() as previous_version:
        previous_version.setattr(authoring_model, "_prompt", lambda step: old_prompt if step == "AUDIT" else current_prompt(step))
        previous = model.prepare("AUDIT", context, package)
    current = model.prepare("AUDIT", context, package)
    assert previous.request_contract == current.request_contract
    assert previous.prompt_hash != current.prompt_hash
    assert previous.contract_hash != current.contract_hash
    assert model.snapshot()["schema_version"] == "authoring-model/1.1"
    assert model.snapshot()["request_contract"] == "bailian-authoring-json/1.1"
    factory, _ = stub_sdk(monkeypatch)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_PREPARATION_CHANGED$"):
        asyncio.run(model.call("AUDIT", context, package, prepared=previous))
    factory.assert_not_called()


@pytest.mark.parametrize("mutation,code,path", [
    ("missing-version", "AUDIT_SCHEMA_INVALID", "/schema_version"),
    ("severity", "AUDIT_SCHEMA_INVALID", "/findings/0/severity"),
    ("unknown-root", "AUDIT_SCHEMA_INVALID", "/"),
    ("unknown-finding-field", "AUDIT_SCHEMA_INVALID", "/findings/0"),
    ("unknown-reference-field", "AUDIT_SCHEMA_INVALID", "/findings/0/sources/0"),
    ("reference-page-type", "AUDIT_SCHEMA_INVALID", "/findings/0/sources/0/page"),
    ("missing-coverage", "AUDIT_COVERAGE_INVALID", "/coverage"),
    ("duplicate-coverage", "AUDIT_COVERAGE_INVALID", "/coverage"),
    ("unhashable-coverage", "AUDIT_COVERAGE_INVALID", "/coverage"),
    ("unknown-target", "AUDIT_TARGET_NOT_FOUND", "/findings/0/target"),
])
def test_audit_failure_diagnostics_are_precise_safe_and_keep_known_usage(monkeypatch, mutation, code, path):
    context, package = synthetic_context_and_package()
    report = fixture_report(package)
    finding = report["findings"][0]
    sentinel = "PRIVATE_AUDIT_VALUE_SENTINEL"
    if mutation == "missing-version":
        report.pop("schema_version")
    elif mutation == "severity":
        finding["severity"] = sentinel
    elif mutation == "unknown-root":
        report[sentinel] = sentinel
    elif mutation == "unknown-finding-field":
        finding[sentinel] = {"text": sentinel}
    elif mutation == "unknown-reference-field":
        finding["sources"][0][sentinel] = sentinel
    elif mutation == "reference-page-type":
        finding["sources"][0]["page"] = sentinel
    elif mutation == "missing-coverage":
        report["coverage"] = ["PROVENANCE"]
    elif mutation == "duplicate-coverage":
        report["coverage"][-1] = report["coverage"][0]
    elif mutation == "unhashable-coverage":
        report["coverage"][0] = {sentinel: sentinel}
    else:
        finding["target"] = {"collection": "knowledge", "id": sentinel}
    _, client = stub_sdk(monkeypatch, good_response(report))
    model = AuthoringModel(fixture_config())
    prepared = model.prepare("AUDIT", context, package)
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
        asyncio.run(model.call("AUDIT", context, package, prepared=prepared))
    assert caught.value.details == caught.value.receipt["output_diagnostics"] == [{"code": code, "entity_path": path}]
    assert caught.value.receipt["usage_known"] is True
    assert Decimal(caught.value.receipt["charged_cost_cny"]) == Decimal("0.00006")
    assert caught.value.receipt["prompt_hash"] == prepared.prompt_hash
    assert caught.value.receipt["contract_hash"] == prepared.contract_hash
    assert caught.value.receipt["reservation"] == prepared.reservation.to_metadata()
    assert sentinel not in str(caught.value) + canonical_json(caught.value.receipt)
    assert "PRIVATE_a_SENTINEL" not in canonical_json(caught.value.receipt)
    client.chat_completion.assert_awaited_once()


def test_audit_malformed_json_and_invalid_candidate_have_separate_safe_roots():
    _, package = synthetic_context_and_package()
    for raw in ("PRIVATE_RAW_PROVIDER_TEXT", '{"summary":"x","summary":"y"}', []):
        with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
            validate_model_audit(raw, package)
        assert caught.value.details == [{"code": "AUDIT_SCHEMA_INVALID", "entity_path": "/"}]
    package["initial_phase_id"] = "PRIVATE_BAD_CANDIDATE_PHASE"
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
        validate_model_audit(fixture_report(package), package)
    assert caught.value.details == [{"code": "AUDIT_CANDIDATE_INVALID", "entity_path": "/"}]


def test_audit_reference_diagnostics_are_limited_to_ten_without_values():
    _, package = synthetic_context_and_package()
    report = fixture_report(package)
    report["findings"][0]["sources"] = [{"source_id": "PRIVATE_REF_SENTINEL", "anchor": f"L{index + 1}"}
                                        for index in range(12)]
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
        validate_model_audit(report, package)
    assert caught.value.details == [{"code": "AUDIT_REFERENCE_MISMATCH", "entity_path": f"/findings/0/sources/{index}"}
                                    for index in range(10)]
    assert "PRIVATE_REF_SENTINEL" not in canonical_json(caught.value.details)


@pytest.mark.parametrize("code,path", [
    ("AUDIT_SCHEMA_INVALID", "/findings/199/sources/99/anchor"),
    ("AUDIT_COVERAGE_INVALID", "/coverage"), ("AUDIT_DUPLICATE_FINDING_ID", "/findings/199/id"),
    ("AUDIT_TARGET_INVALID", "/findings/199/target"), ("AUDIT_TARGET_NOT_FOUND", "/findings/199/target"),
    ("AUDIT_REFERENCE_MISMATCH", "/findings/199/sources/99"), ("AUDIT_CANDIDATE_INVALID", "/"),
])
def test_audit_diagnostic_whitelist_accepts_only_two_fields_and_bounded_positions(code, path):
    diagnostic = {"code": code, "entity_path": path}
    assert validate_output_diagnostics([diagnostic]) == [diagnostic]
    with pytest.raises(ValueError, match="^AUTHORING_DIAGNOSTICS_INVALID$"):
        validate_output_diagnostics([diagnostic | {"message": "PRIVATE_MESSAGE_SENTINEL"}])


@pytest.mark.parametrize("code,path", [
    ("AUDIT_PRIVATE_UNKNOWN", "/"), ("AUDIT_SCHEMA_INVALID", "/PRIVATE_FIELD_SENTINEL"),
    ("AUDIT_SCHEMA_INVALID", "/findings/200"), ("AUDIT_SCHEMA_INVALID", "/findings/0/sources/100"),
    ("AUDIT_SCHEMA_INVALID", "/findings/0/target/PRIVATE_MODEL_ID"),
    ("AUDIT_SCHEMA_INVALID", "/findings/0/sources/0/text"),
    ("AUDIT_COVERAGE_INVALID", "/findings/0/category"), ("AUDIT_DUPLICATE_FINDING_ID", "/findings/0/target"),
    ("AUDIT_TARGET_INVALID", "/findings/0/target/id"), ("AUDIT_TARGET_NOT_FOUND", "/findings/00/target"),
    ("AUDIT_REFERENCE_MISMATCH", "/findings/0/sources/0/source_id"),
    ("AUDIT_REFERENCE_MISMATCH", "/findings/0/sources/0\n"), ("AUDIT_CANDIDATE_INVALID", "/candidate"),
])
def test_audit_diagnostics_reject_wrong_codes_paths_indices_and_private_data(code, path):
    with pytest.raises(ValueError, match="^AUTHORING_DIAGNOSTICS_INVALID$") as caught:
        validate_output_diagnostics([{"code": code, "entity_path": path}])
    assert "PRIVATE_" not in str(caught.value)


def test_audit_v3_complete_example_is_a_valid_wire_report_but_never_task_evidence():
    from src.schemas.script_review import ManualAuditReport

    prompt = (authoring_model.PROMPT_DIRECTORY / "audit_system_v3.txt").read_text("utf-8")
    example = next(json.loads(line) for line in prompt.splitlines() if line.startswith('{"schema_version":"script-audit/1.0"'))
    fragment = next(json.loads(line) for line in prompt.splitlines() if line.startswith('{"introduction":'))
    assert set(example) == {"schema_version", "summary", "coverage", "findings"}
    ManualAuditReport.model_validate(example)
    assert set(example["coverage"]) == AUDIT_CATEGORIES
    assert example["findings"][0]["target"] == {"collection": "introduction", "id": None}
    assert example["findings"][0]["sources"] == fragment["introduction"]["sources"]
    assert "不属于本次材料或候选包" in prompt
    assert "不能把示例 ID、引用或结论复制到本次结果" in prompt
    for fixture_text in ("PRIVATE_a_SENTINEL", "钟的维修卡", "挂钟", "维修记录"):
        assert fixture_text not in prompt
    _, package = synthetic_context_and_package()
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
        validate_model_audit(example, package)
    assert caught.value.details == [{"code": "AUDIT_REFERENCE_MISMATCH", "entity_path": "/findings/0/sources/0"}]
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$"):
        validate_model_audit({"output": fixture_report(package)}, package)
