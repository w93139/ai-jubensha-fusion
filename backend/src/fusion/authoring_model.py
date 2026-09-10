"""One bounded Compiler or Audit call. No tools, retries, or publication rights."""
from __future__ import annotations

from src.fusion.audit_runtime import audit_runtime_contract, candidate_observations
from src.fusion.citation_audit import prepare_citation_contract, assemble_citation_audit, CitationAuditError
from src.schemas.citation_audit import CitationAuditDraft

import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re
from time import monotonic
from typing import Any, Literal, NoReturn

from pydantic import Field, ValidationError, model_validator

from src.fusion.authoring_sources import (
    MAX_MATERIAL_TEXT_BYTES, MAX_SOURCE_TEXT_BYTES, TEXT_MEDIA_TYPES, AuthoringSourceError, locate_text,
)
from src.fusion.budget import UsageAmount, normalize_provider_usage
from src.fusion.package_validation import canonical_json, content_hash, parse_package_json, validate_package
from src.fusion.provider_smoke import SelectedSmokeConfig
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.schemas.authoring import CompileOutput, CompilerDraftOutput, CompilerDraftOutputV12, CompilerTextDraft, CompilerTextConfirmation, parse_authoring_request
from src.fusion.authoring_rule_plan import assemble_text_slots, locked_projection, slot_catalog
from src.fusion.audit_references import assemble_indexed_audit, audit_source_catalog, IndexedReferenceError
from src.schemas.indexed_audit import IndexedAuditDraft
from src.schemas.bounded_audit import BoundedAuditDraft
from src.schemas.script_package import PackageModel, ScriptPackageV11, ScriptPackageV12, SourceFileV11, SourceReference, parse_script_package
from src.schemas.script_review import ManualAuditReport, ManualAuditReportV12, finding_entity, parse_audit_report
from src.services.llm_service import LLMMessage, OpenAILLMService


AUTHORING_CONTRACT_VERSION = "authoring-model/1.1"
REQUEST_CONTRACT_VERSION = "bailian-authoring-json/1.1"
AUTHORING_CONTRACT_VERSION_V12 = "authoring-model/1.2"
REQUEST_CONTRACT_VERSION_V12 = "bailian-authoring-json/1.2"
MAX_INPUT_TOKENS = 32768
MAX_CALL_COST_CNY = Decimal("0.05")
MODEL_TIMEOUT_SECONDS = 90
OUTPUT_TOKEN_LIMITS = {"COMPILE": 8192, "AUDIT": 4096}
AUDIT_CATEGORIES = frozenset({"PROVENANCE", "TIMELINE", "EVIDENCE", "KNOWLEDGE_BOUNDARY", "PLAYABILITY"})
PROMPT_DIRECTORY = Path(__file__).parent / "prompts"
COMPILE_SCHEMA_TAIL_MARKER = "\n<<<COMPILER_AFTER_SCHEMA>>>\n"
Step = Literal["COMPILE", "AUDIT"]
BINDING_DIAGNOSTIC_PATHS = frozenset({"/title", "/schema_version", "/script_key", "/content_version", "/player_count", "/sources"})
DIAGNOSTIC_COLLECTION_LIMITS = {"sources": 500, "characters": 8, "phases": 100,
                                "knowledge": 5000, "evidence": 5000, "truth": 1000}


def _diagnostic_index(value: str, limit: int) -> bool:
    return re.fullmatch(r"0|[1-9][0-9]{0,3}", value) is not None and int(value) < limit


def _diagnostic_entity(parts: list[str], collections: tuple[str, ...]) -> bool:
    return (len(parts) == 2 and parts[0] in collections
            and _diagnostic_index(parts[1], DIAGNOSTIC_COLLECTION_LIMITS[parts[0]]))


def _package_diagnostic_path(code: str, path: str) -> bool:
    """Match each known rule to fixed fields and bounded numeric positions."""
    if not path.startswith("/"):
        return False
    parts = path[1:].split("/")
    rule = code.removeprefix("PACKAGE_")
    if rule == "SCHEMA_INVALID":
        return path == "/"
    if rule == "DUPLICATE_ID":
        return parts[-1] == "id" and _diagnostic_entity(parts[:-1], tuple(DIAGNOSTIC_COLLECTION_LIMITS))
    if rule in {"SOURCE_NOT_FOUND", "SOURCE_PAGE_OUT_OF_RANGE", "SOURCE_LOCATION_MISSING"}:
        return (len(parts) >= 3 and parts[-2] == "sources" and _diagnostic_index(parts[-1], 100)
                and (parts[:-2] in (["introduction"], ["settlement", "instructions"])
                     or _diagnostic_entity(parts[:-2], ("characters", "phases", "knowledge", "evidence", "truth"))))
    if rule in {"DUPLICATE_SOURCE_PATH", "ORIGINAL_SOURCE_MISSING", "INVALID_SOURCE_LINK"}:
        return _diagnostic_entity(parts, ("sources",))
    if rule == "CHARACTER_COUNT_MISMATCH":
        return path == "/player_count"
    if rule == "PHASE_NOT_FOUND":
        return (path == "/initial_phase_id"
                or (parts[-1] == "next_phase_id" and _diagnostic_entity(parts[:-1], ("phases",)))
                or (parts[-2:] == ["release", "phase_id"]
                    and _diagnostic_entity(parts[:-2], ("knowledge", "evidence"))))
    if rule in {"PHASE_CYCLE", "UNREACHABLE_PHASE"}:
        return path == "/phases"
    if rule == "INVALID_SETTLEMENT_PHASE":
        return path == "/settlement/phase_id"
    if rule == "TRUTH_NOT_FOUND":
        return path == "/settlement/truth_ids"
    if rule in {"DUPLICATE_REFERENCE", "EVIDENCE_NOT_FOUND", "UNSATISFIABLE_RELEASE"}:
        return ((rule == "DUPLICATE_REFERENCE" and path == "/settlement/truth_ids")
                or (parts[-1] == "release" and _diagnostic_entity(parts[:-1], ("knowledge", "evidence"))))
    if rule in {"INVALID_PUBLIC_SCOPE", "INVALID_PRIVATE_SCOPE"}:
        return _diagnostic_entity(parts, ("knowledge", "evidence"))
    if rule == "UNREACHABLE_EVIDENCE":
        return path == "/evidence"
    if rule == "INITIAL_KNOWLEDGE_MISSING":
        return _diagnostic_entity(parts, ("characters",))
    return False


def _audit_diagnostic_path(code: str, path: str) -> bool:
    """Audit diagnostics identify bounded report positions, never target IDs."""
    if code == "AUDIT_CITATION_SCHEMA_INVALID":
        return path == "/"
    if code == "AUDIT_CITATION_REFERENCE_INVALID":
        return re.fullmatch(r"/findings/[0-9]/citation_indexes(?:/(?:0|[1-9][0-9]?))?", path) is not None
    if code == "AUDIT_BOUNDED_SCHEMA_INVALID":
        return (path in {"/", "/schema_version", "/status", "/summary", "/coverage", "/findings"}
                or re.fullmatch(r"/findings/[0-9](?:/(?:id|category|severity|message|target|source_indexes))?", path) is not None
                or re.fullmatch(r"/findings/[0-9]/target/(?:collection|id)", path) is not None
                or re.fullmatch(r"/findings/[0-9]/source_indexes/(?:0|[1-9][0-9]?)", path) is not None)
    if code == "AUDIT_CANDIDATE_INVALID":
        return path == "/"
    if code == "AUDIT_COVERAGE_INVALID":
        return path == "/coverage"
    if code == "AUDIT_SCHEMA_INVALID" and path in {"/", "/schema_version", "/summary", "/findings"}:
        return True
    parts = path[1:].split("/") if path.startswith("/") else []
    if len(parts) < 2 or parts[0] != "findings" or not _diagnostic_index(parts[1], 200):
        return False
    tail = parts[2:]
    if code == "AUDIT_DUPLICATE_FINDING_ID":
        return tail == ["id"]
    if code in {"AUDIT_TARGET_INVALID", "AUDIT_TARGET_NOT_FOUND"}:
        return tail == ["target"]
    if code == 'AUDIT_SOURCE_INDEX_UNAVAILABLE':
        return len(tail) == 2 and tail[0] == 'source_indexes' and _diagnostic_index(tail[1], 100)
    is_reference = len(tail) >= 2 and tail[0] == "sources" and _diagnostic_index(tail[1], 100)
    if code == "AUDIT_REFERENCE_MISMATCH":
        return len(tail) == 2 and is_reference
    if code == "AUDIT_SCHEMA_INVALID":
        return (not tail or (len(tail) == 1 and tail[0] in {"id", "category", "severity", "target", "message", "sources"})
                or (len(tail) == 2 and tail[0] == "target" and tail[1] in {"collection", "id"})
                or (is_reference and (len(tail) == 2 or (len(tail) == 3 and tail[2] in {"source_id", "page", "anchor"}))))
    return False


class OutputDiagnostic(PackageModel):
    """A fixed field position and reason; never copies text or model IDs."""
    code: Literal[
        "TEXT_NOT_IN_MATERIALS", "TEXT_OUTSIDE_REFERENCES", "INPUT_BINDING_MISMATCH",
        "PACKAGE_SCHEMA_INVALID", "PACKAGE_DUPLICATE_ID", "PACKAGE_SOURCE_NOT_FOUND",
        "PACKAGE_SOURCE_PAGE_OUT_OF_RANGE", "PACKAGE_SOURCE_LOCATION_MISSING", "PACKAGE_DUPLICATE_SOURCE_PATH",
        "PACKAGE_ORIGINAL_SOURCE_MISSING", "PACKAGE_INVALID_SOURCE_LINK", "PACKAGE_CHARACTER_COUNT_MISMATCH",
        "PACKAGE_PHASE_NOT_FOUND", "PACKAGE_PHASE_CYCLE", "PACKAGE_UNREACHABLE_PHASE",
        "PACKAGE_INVALID_SETTLEMENT_PHASE", "PACKAGE_DUPLICATE_REFERENCE", "PACKAGE_TRUTH_NOT_FOUND",
        "PACKAGE_INVALID_PUBLIC_SCOPE", "PACKAGE_INVALID_PRIVATE_SCOPE", "PACKAGE_EVIDENCE_NOT_FOUND",
        "PACKAGE_UNSATISFIABLE_RELEASE", "PACKAGE_UNREACHABLE_EVIDENCE", "PACKAGE_INITIAL_KNOWLEDGE_MISSING",
        "AUDIT_CITATION_SCHEMA_INVALID", "AUDIT_CITATION_REFERENCE_INVALID", "AUDIT_SCHEMA_INVALID", "AUDIT_BOUNDED_SCHEMA_INVALID", "AUDIT_COVERAGE_INVALID", "AUDIT_DUPLICATE_FINDING_ID",
        "AUDIT_TARGET_INVALID", "AUDIT_TARGET_NOT_FOUND", "AUDIT_REFERENCE_MISMATCH", "AUDIT_CANDIDATE_INVALID", "AUDIT_SOURCE_INDEX_UNAVAILABLE",
    ]
    entity_path: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def reason_matches_path(self) -> "OutputDiagnostic":
        if self.code.startswith("PACKAGE_"):
            valid = _package_diagnostic_path(self.code, self.entity_path)
        elif self.code.startswith("AUDIT_"):
            valid = _audit_diagnostic_path(self.code, self.entity_path)
        elif self.code == "INPUT_BINDING_MISMATCH":
            valid = self.entity_path in BINDING_DIAGNOSTIC_PATHS
        else:
            parts = self.entity_path[1:].split("/") if self.entity_path.startswith("/") else []
            valid = (self.entity_path in {"/introduction/text", "/settlement/instructions/text"}
                     or (bool(parts) and parts[-1] == "name" and _diagnostic_entity(parts[:-1], ("characters",)))
                     or (bool(parts) and parts[-1] == "text"
                         and _diagnostic_entity(parts[:-1], ("knowledge", "evidence", "truth"))))
        if not valid:
            raise ValueError("diagnostic reason and path differ")
        return self


class OutputDiagnostics(PackageModel):
    items: list[OutputDiagnostic] = Field(max_length=10)


def validate_response_fingerprint(value: Any) -> dict | None:
    """Optional in old receipts; contains digests only, never provider text/IDs."""
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"content_sha256", "response_id_sha256"}
            or any(item is not None and (type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None)
                   for item in value.values())):
        raise ValueError("AUTHORING_RESPONSE_FINGERPRINT_INVALID")
    return dict(value)


def validate_response_finish(value: Any) -> str | None:
    if value is not None and (type(value) is not str or value not in {
            "stop", "length", "content_filter", "tool_calls", "function_call", "OTHER"}):
        raise ValueError("AUTHORING_RESPONSE_FINISH_INVALID")
    return value


def safe_response_finish(value: Any) -> str | None:
    if value is None:
        return None
    return value if type(value) is str and value in {
        "stop", "length", "content_filter", "tool_calls", "function_call"} else "OTHER"


def _response_digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def validate_output_diagnostics(value: Any) -> list[dict]:
    """Shared strict receipt validation; invalid input has a fixed safe error."""
    try:
        parsed = OutputDiagnostics.model_validate({"items": value})
        return [item.model_dump() for item in parsed.items]
    except (ValueError, TypeError):
        raise ValueError("AUTHORING_DIAGNOSTICS_INVALID") from None


def _package_output_diagnostics(report: dict) -> list[dict]:
    """Project rule failures only; messages, IDs, sources and unknowns stay out."""
    issues = report.get("issues")
    if type(issues) is not list:
        return []
    diagnostics, seen = [], set()
    # The deterministic validator bounds its report at 200 issues. Keep that
    # bound even if a future implementation supplies a different report shape.
    for item in issues[:200]:
        if type(item) is not dict or type(item.get("code")) is not str or item.get("severity") != "ERROR":
            continue
        code = "PACKAGE_" + item["code"]
        path = "/" if code == "PACKAGE_SCHEMA_INVALID" else item.get("path")
        try:
            diagnostic = OutputDiagnostic.model_validate({"code": code, "entity_path": path})
        except (ValueError, TypeError):
            continue
        pair = (diagnostic.code, diagnostic.entity_path)
        if pair not in seen:
            seen.add(pair)
            diagnostics.append(diagnostic.model_dump())
        if len(diagnostics) == 10:
            break
    return diagnostics


def _audit_record_diagnostics(document: dict) -> list[dict]:
    """Project the report-level checks that Pydantic reports at its root."""
    diagnostics = []
    coverage = document.get("coverage")
    if (type(coverage) is not list or len(coverage) != 5
            or any(type(category) is not str for category in coverage)
            or set(coverage) != AUDIT_CATEGORIES):
        diagnostics.append({"code": "AUDIT_COVERAGE_INVALID", "entity_path": "/coverage"})
    findings = document.get("findings")
    if type(findings) is list:
        seen = set()
        for index, finding in enumerate(findings[:200]):
            if type(finding) is not dict or type(finding.get("id")) is not str:
                continue
            if finding["id"] in seen:
                diagnostics.append({"code": "AUDIT_DUPLICATE_FINDING_ID", "entity_path": f"/findings/{index}/id"})
            seen.add(finding["id"])
            if len(diagnostics) == 10:
                break
    return diagnostics


def _audit_schema_path(location: tuple) -> str:
    """Unknown names collapse to the nearest approved parent, never an echo."""
    known = {"schema_version", "summary", "findings", "id", "category", "severity", "target", "message",
             "sources", "collection", "source_id", "page", "anchor"}
    parts = []
    for part in location[:8]:
        if type(part) is str and part in known:
            parts.append(part)
        elif type(part) is int and 0 <= part < 200:
            parts.append(str(part))
        else:
            break
    while parts:
        path = "/" + "/".join(parts)
        if _audit_diagnostic_path("AUDIT_SCHEMA_INVALID", path):
            return path
        parts.pop()
    return "/"


def _audit_schema_diagnostics(error: ValidationError, document: dict) -> list[dict]:
    diagnostics = _audit_record_diagnostics(document)
    for issue in error.errors(include_input=False, include_context=False, include_url=False)[:200]:
        if len(diagnostics) == 10:
            break
        location = issue["loc"]
        if location and location[0] == "coverage":
            diagnostic = {"code": "AUDIT_COVERAGE_INVALID", "entity_path": "/coverage"}
        else:
            path = _audit_schema_path(location)
            if (len(location) == 3 and location[0] == "findings" and location[-1] == "target"
                    and issue["type"] == "value_error" and _audit_diagnostic_path("AUDIT_TARGET_INVALID", path)):
                diagnostic = {"code": "AUDIT_TARGET_INVALID", "entity_path": path}
            elif not location and diagnostics:
                # Root-level uniqueness/coverage failures have specific safe
                # positions above, so do not obscure them with a generic root.
                continue
            else:
                diagnostic = {"code": "AUDIT_SCHEMA_INVALID", "entity_path": path}
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    return validate_output_diagnostics(diagnostics)


class AuthoringModelError(ValueError):
    """Only fixed error codes and content-free usage receipts leave this layer."""
    def __init__(self, code: str, receipt: dict | None = None, *, details: list[dict] | None = None) -> None:
        self.code = code
        self.receipt = receipt or {}
        self.details = validate_output_diagnostics([] if details is None else details)
        super().__init__(code)


def _document(raw: Any) -> dict:
    try:
        if isinstance(raw, str):
            value = parse_package_json(raw.encode("utf-8"))
        elif isinstance(raw, bytes):
            value = parse_package_json(raw)
        else:
            value = parse_package_json(canonical_json(raw).encode("utf-8"))
        if type(value) is not dict:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AuthoringModelError("OUTPUT_JSON_INVALID") from None


def _context(document: dict) -> dict:
    """Validate even direct adapter callers before their data enters a prompt."""
    try:
        context = _document(document)
        required = {"script_key", "bundle_hash", "source_ids", "title", "content_version", "player_count",
                    "sources", "materials", "notes"}
        if "package_contract" in context:
            required.add("package_contract")
        if "rule_plan" in context:
            required.add("rule_plan")
        if "compiler_mode" in context:
            required.add("compiler_mode")
        if "audit_mode" in context:
            required.add("audit_mode")
        if set(context) != required:
            raise ValueError
        request = {"idempotency_key": context["script_key"], **{key: context[key] for key in
                   ("bundle_hash", "source_ids", "title", "content_version", "player_count")}}
        if "package_contract" in context:
            request["package_contract"] = context["package_contract"]
        if "rule_plan" in context:
            request["rule_plan"] = context["rule_plan"]
        if "compiler_mode" in context:
            request["compiler_mode"] = context["compiler_mode"]
        if "audit_mode" in context:
            request["audit_mode"] = context["audit_mode"]
        parse_authoring_request(request)
        if not isinstance(context["sources"], list) or not 1 <= len(context["sources"]) <= 500:
            raise ValueError
        sources = [SourceFileV11.model_validate(source) for source in context["sources"]]
        by_id = {source.id: source for source in sources}
        if len(by_id) != len(sources):
            raise ValueError
        for source in sources:
            if any(key not in by_id or by_id[key].kind != "original" for key in source.original_source_ids):
                raise ValueError
        materials = context["materials"]
        if not isinstance(materials, list) or len(materials) != len(context["source_ids"]):
            raise ValueError
        total = 0
        for source_id, item in zip(context["source_ids"], materials):
            if (not isinstance(item, dict) or set(item) != {"source_id", "text"}
                    or item["source_id"] != source_id or source_id not in by_id
                    or by_id[source_id].media_type not in TEXT_MEDIA_TYPES
                    or not isinstance(item["text"], str) or not item["text"].strip()):
                raise ValueError
            length = len(item["text"].encode("utf-8"))
            if length > MAX_SOURCE_TEXT_BYTES:
                raise ValueError
            total += length
        needed_ids = set(context["source_ids"])
        for source_id in context["source_ids"]:
            needed_ids.update(by_id[source_id].original_source_ids)
        if needed_ids != set(by_id) or total > MAX_MATERIAL_TEXT_BYTES:
            raise ValueError
        if (not isinstance(context["notes"], list) or len(context["notes"]) > 30
                or any(not isinstance(note, str) or not note.strip() or len(note) > 500 for note in context["notes"])):
            raise ValueError
        if "rule_plan" in context:
            # Validate the prepared draft against the actual frozen source input,
            # without recursively applying its own locked-rule comparison.
            base_context = {key: value for key, value in context.items() if key not in {"rule_plan", "compiler_mode", "audit_mode"}}
            parse_compiler_output({"status": "CANDIDATE", "package": context["rule_plan"], "blockers": []}, base_context)
        return context
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise AuthoringModelError("AUTHORING_CONTEXT_INVALID") from None


def _entity_refs(package: ScriptPackageV11) -> list[tuple[str | None, list[SourceReference], str | None]]:
    entities = [(package.introduction.text, package.introduction.sources, "/introduction/text"),
                (package.settlement.instructions.text, package.settlement.instructions.sources, "/settlement/instructions/text")]
    entities.extend((item.name, item.sources, f"/characters/{index}/name") for index, item in enumerate(package.characters))
    # Phase titles are labels, but their cited rules still must be authorized.
    entities.extend((None, item.sources, None) for item in package.phases)
    for collection in ("knowledge", "evidence", "truth"):
        entities.extend((item.text, item.sources, f"/{collection}/{index}/text")
                        for index, item in enumerate(getattr(package, collection)))
    if isinstance(package, ScriptPackageV12):
        entities.extend((None, item.sources, None) for item in package.mechanics.phase_budgets)
        entities.extend((None, item.sources, None) for item in package.mechanics.actions)
    return entities


def _same_source_metadata(actual: Any, expected: list[dict]) -> bool:
    """Compare strict declarations, treating omitted optional nulls alike.

    This never rewrites either raw document. Both array order and each original
    source ID's position remain significant, as do all non-null field values.
    """
    if type(actual) is not list or len(actual) != len(expected):
        return False
    try:
        return ([SourceFileV11.model_validate(item).model_dump(exclude_none=True) for item in actual]
                == [SourceFileV11.model_validate(item).model_dump(exclude_none=True) for item in expected])
    except (ValueError, TypeError):
        return False


def parse_compiler_output(raw: Any, context: dict) -> dict:
    context = _context(context)
    try:
        result = CompileOutput.model_validate(_document(raw))
    except ValueError:
        raise AuthoringModelError("COMPILER_OUTPUT_INVALID") from None
    materials = {item["source_id"]: item["text"] for item in context["materials"]}
    if result.status == "BLOCKED":
        try:
            for blocker in result.blockers:
                for reference in blocker.sources:
                    locate_text(reference, materials)
        except AuthoringSourceError:
            raise AuthoringModelError("COMPILER_REFERENCE_UNAUTHORIZED") from None
        return result.model_dump()
    document = result.package
    contract = context.get("package_contract", "script-package/1.1")
    expected = {"schema_version": contract, **{key: context[key] for key in
                                                        ("script_key", "title", "content_version", "player_count")}}
    binding_diagnostics = [{"code": "INPUT_BINDING_MISMATCH", "entity_path": "/" + key}
                           for key, value in expected.items() if document.get(key) != value]
    if not _same_source_metadata(document.get("sources"), context["sources"]):
        binding_diagnostics.append({"code": "INPUT_BINDING_MISMATCH", "entity_path": "/sources"})
    if binding_diagnostics:
        raise AuthoringModelError("COMPILER_INPUT_BINDING_MISMATCH", details=binding_diagnostics)
    report = validate_package(document)
    if not report["valid"]:
        details = _package_output_diagnostics(report)
        if contract == "script-package/1.2" and not details:
            details = [{"code": "PACKAGE_SCHEMA_INVALID", "entity_path": "/"}]
        raise AuthoringModelError("COMPILER_PACKAGE_INVALID", details=details)
    package = parse_script_package(document)
    if "rule_plan" in context and locked_projection(document) != locked_projection(context["rule_plan"]):
        raise AuthoringModelError("COMPILER_RULE_PLAN_MISMATCH")
    if context.get("compiler_mode") == "CONFIRM_FROZEN_TEXT" and document != context["rule_plan"]:
        raise AuthoringModelError("COMPILER_FROZEN_TEXT_MISMATCH")
    diagnostics = []
    for quote, references, entity_path in _entity_refs(package):
        try:
            cited = [locate_text(reference, materials) for reference in references]
        except AuthoringSourceError:
            raise AuthoringModelError("COMPILER_REFERENCE_UNAUTHORIZED") from None
        if quote is None:
            continue
        in_materials = any(quote in text for text in materials.values())
        in_references = any(quote in text for text in cited)
        if (not in_materials or not in_references) and len(diagnostics) < 10:
            diagnostics.append({"code": "TEXT_NOT_IN_MATERIALS" if not in_materials else "TEXT_OUTSIDE_REFERENCES",
                                "entity_path": entity_path})
    if diagnostics:
        raise AuthoringModelError("COMPILER_TEXT_NOT_EXTRACTIVE", details=diagnostics)
    if isinstance(package, ScriptPackageV12):
        # Public action labels must also be supported by their authorized text.
        # Keep the legacy diagnostic whitelist unchanged; new fields use a safe root.
        for action in package.mechanics.actions:
            cited = [locate_text(reference, materials) for reference in action.sources]
            if not any(action.label in text for text in cited):
                raise AuthoringModelError("COMPILER_TEXT_NOT_EXTRACTIVE", details=[{
                    "code": "PACKAGE_SCHEMA_INVALID", "entity_path": "/"}])
    # Preserve the candidate's exact raw values rather than injecting defaults.
    return {"status": "CANDIDATE", "package": document, "blockers": []}


def parse_compiler_draft(raw: Any, context: dict) -> dict:
    """Assemble server-owned metadata, then enforce the unchanged domain rules.

    Validation never serializes the draft models back into the artifact: raw
    content values and the frozen source declarations are copied losslessly.
    Historical full-package outputs belong to parse_compiler_output only.
    """
    context = _context(context)
    if "rule_plan" in context:
        try:
            document = _document(raw)
            preserve_text = context.get("compiler_mode") == "CONFIRM_FROZEN_TEXT"
            draft = (CompilerTextConfirmation if preserve_text else CompilerTextDraft).model_validate(document)
            package = (assemble_text_slots(context["rule_plan"], document["slots"], preserve_text=preserve_text)
                       if draft.status == "CANDIDATE" else None)
        except ValueError:
            raise AuthoringModelError("COMPILER_TEXT_DRAFT_INVALID") from None
        return parse_compiler_output({"status": draft.status, "package": package,
                                      "blockers": deepcopy(document["blockers"])}, context)
    try:
        document = _document(raw)
        model = CompilerDraftOutputV12 if context.get("package_contract") == "script-package/1.2" else CompilerDraftOutput
        result = model.model_validate(document)
    except ValueError:
        raise AuthoringModelError("COMPILER_DRAFT_INVALID") from None
    package = None
    if result.status == "CANDIDATE":
        package = {"schema_version": context.get("package_contract", "script-package/1.1"), **deepcopy({key: context[key] for key in
                   ("script_key", "title", "content_version", "player_count", "sources")}),
                   **deepcopy(document["content"])}
    return parse_compiler_output({"status": result.status, "package": package,
                                  "blockers": deepcopy(document["blockers"])}, context)


def validate_model_audit(raw: Any, package: dict) -> dict:
    try:
        if not validate_package(package)["valid"]:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise AuthoringModelError("AUDIT_OUTPUT_INVALID", details=[{
            "code": "AUDIT_CANDIDATE_INVALID", "entity_path": "/"}]) from None
    try:
        document = _document(raw)
    except ValueError:
        raise AuthoringModelError("AUDIT_OUTPUT_INVALID", details=[{
            "code": "AUDIT_SCHEMA_INVALID", "entity_path": "/"}]) from None
    try:
        report = (parse_audit_report(document, package["schema_version"])
                  if package["schema_version"] == "script-package/1.2" else ManualAuditReport.model_validate(document))
    except ValidationError as error:
        raise AuthoringModelError("AUDIT_OUTPUT_INVALID", details=_audit_schema_diagnostics(error, document)) from None
    except ValueError:
        raise AuthoringModelError("AUDIT_OUTPUT_INVALID", details=[{
            "code": "AUDIT_SCHEMA_INVALID", "entity_path": "/schema_version"}]) from None
    diagnostics = _audit_record_diagnostics(document)
    for index, finding in enumerate(report.findings):
        if len(diagnostics) == 10:
            break
        entity = finding_entity(package, finding.target)
        if entity is None:
            diagnostics.append({"code": "AUDIT_TARGET_NOT_FOUND", "entity_path": f"/findings/{index}/target"})
            continue
        allowed = {(ref["source_id"], ref.get("page"), ref.get("anchor")) for ref in entity["sources"]}
        for reference_index, reference in enumerate(finding.sources):
            if (reference.source_id, reference.page, reference.anchor) not in allowed:
                diagnostics.append({"code": "AUDIT_REFERENCE_MISMATCH",
                                    "entity_path": f"/findings/{index}/sources/{reference_index}"})
            if len(diagnostics) == 10:
                break
    if diagnostics:
        raise AuthoringModelError("AUDIT_OUTPUT_INVALID", details=diagnostics)
    return report.model_dump()


def _bounded_schema_diagnostics(error: ValidationError) -> list[dict]:
    diagnostics = []
    known = {"schema_version", "status", "summary", "coverage", "findings", "id", "category",
             "severity", "message", "target", "collection", "source_indexes"}
    for issue in error.errors(include_input=False, include_context=False, include_url=False)[:200]:
        parts = []
        for part in issue["loc"][:6]:
            if type(part) is str and part in known:
                parts.append(part)
            elif type(part) is int and 0 <= part < 100:
                parts.append(str(part))
            else:
                break
        while parts and not _audit_diagnostic_path("AUDIT_BOUNDED_SCHEMA_INVALID", "/" + "/".join(parts)):
            parts.pop()
        item = {"code": "AUDIT_BOUNDED_SCHEMA_INVALID", "entity_path": "/" + "/".join(parts)}
        if item not in diagnostics:
            diagnostics.append(item)
        if len(diagnostics) == 10:
            break
    return validate_output_diagnostics(diagnostics)


def parse_bounded_audit(raw: Any, package: dict) -> dict:
    try:
        draft = BoundedAuditDraft.model_validate(_document(raw))
    except ValidationError as error:
        raise AuthoringModelError("AUDIT_BOUNDED_OUTPUT_INVALID", details=_bounded_schema_diagnostics(error)) from None
    except (ValueError, TypeError):
        raise AuthoringModelError("AUDIT_BOUNDED_OUTPUT_INVALID") from None
    if draft.status != "COMPLETE":
        raise AuthoringModelError("AUDIT_INCOMPLETE")
    document = draft.model_dump(exclude={"status"})
    document["schema_version"] = "indexed-audit-draft/1.0"
    return parse_indexed_audit(document, package)


def parse_indexed_audit(raw: Any, package: dict) -> dict:
    try:
        if not isinstance(package, dict) or not validate_package(package)["valid"]:
            raise ValueError
        assembled = assemble_indexed_audit(_document(raw), package)
    except IndexedReferenceError as exc:
        raise AuthoringModelError('AUDIT_INDEXED_OUTPUT_INVALID',
                                  details=validate_output_diagnostics(exc.diagnostics)) from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise AuthoringModelError("AUDIT_INDEXED_OUTPUT_INVALID") from None
    return validate_model_audit(assembled, package)


def parse_citation_audit(raw: Any, package: dict, binding: dict) -> dict:
    try:
        return assemble_citation_audit(_document(raw), package,
            expected_package_hash=binding["package_hash"], expected_catalog_hash=binding["catalog_hash"])
    except CitationAuditError as exc:
        if exc.code == "CITATION_INCOMPLETE":
            raise AuthoringModelError("AUDIT_INCOMPLETE") from None
        code = "AUDIT_CITATION_REFERENCE_INVALID" if exc.code in {"CITATION_INDEX_UNAVAILABLE", "CITATION_TARGET_MIXED"} else "AUDIT_CITATION_SCHEMA_INVALID"
        raise AuthoringModelError("AUDIT_CITATION_OUTPUT_INVALID", details=[{"code":code, "entity_path":exc.path if code == "AUDIT_CITATION_REFERENCE_INVALID" else "/"}]) from None
    except (ValueError, KeyError, TypeError):
        raise AuthoringModelError("AUDIT_CITATION_OUTPUT_INVALID", details=[{"code":"AUDIT_CITATION_SCHEMA_INVALID", "entity_path":"/"}]) from None


def _schema(step: Step, package_contract: str = "script-package/1.1") -> dict:
    if package_contract not in {"script-package/1.1", "script-package/1.2"}:
        raise AuthoringModelError("AUTHORING_CONTRACT_INVALID")
    if step == "COMPILE":
        model = CompilerDraftOutputV12 if package_contract == "script-package/1.2" else CompilerDraftOutput
    else:
        model = ManualAuditReportV12 if package_contract == "script-package/1.2" else ManualAuditReport
    return {"output": model.model_json_schema()}


def _available_locators(context: dict) -> list[dict]:
    """Unique usable Markdown headings, derived without copying body text."""
    result = []
    for material in context["materials"]:
        headings = [line.lstrip("#").strip() for line in material["text"].splitlines() if re.match(r"^#{1,6}\s", line)]
        counts = Counter(headings)
        anchors = [heading for heading in headings if counts[heading] == 1 and 1 <= len(heading) <= 256
                   and re.fullmatch(r"L([1-9][0-9]*)(?:-L([1-9][0-9]*))?", heading) is None]
        if anchors:
            result.append({"source_id": material["source_id"], "anchors": anchors})
    return result


def _prompt(step: Step, package_contract: str = "script-package/1.1") -> str:
    if step not in OUTPUT_TOKEN_LIMITS:
        raise AuthoringModelError("AUTHORING_STEP_INVALID")
    try:
        if package_contract == "script-package/1.1":
            filename = "compiler_system_v4.txt" if step == "COMPILE" else "audit_system_v3.txt"
        elif package_contract == "script-package/1.2":
            filename = "compiler_system_v7.txt" if step == "COMPILE" else "audit_system_v4.txt"
        else:
            raise AuthoringModelError("AUTHORING_CONTRACT_INVALID")
        prompt = (PROMPT_DIRECTORY / filename).read_text("utf-8")
        if filename == "compiler_system_v7.txt":
            sections = prompt.split(COMPILE_SCHEMA_TAIL_MARKER)
            if len(sections) != 2 or any(not section.strip() for section in sections):
                raise AuthoringModelError("AUTHORING_PROMPT_UNAVAILABLE")
        return prompt
    except (OSError, UnicodeError):
        raise AuthoringModelError("AUTHORING_PROMPT_UNAVAILABLE") from None


@dataclass(frozen=True)
class PreparedAuthoringCall:
    step: Step
    messages: tuple[LLMMessage, ...]
    prompt_hash: str
    contract_hash: str
    input_tokens: int
    max_completion_tokens: int
    reservation: UsageAmount
    context: dict
    package: dict | None
    request_contract: dict


def portable_audit_schema() -> dict:
    # JSON Schema pattern uses search semantics. Explicit spans retain that
    # language even when a provider decoder interprets the pattern as fullmatch.
    schema = BoundedAuditDraft.model_json_schema()
    for node in (schema["properties"]["summary"], schema["$defs"]["BoundedFinding"]["properties"]["message"]):
        if node["pattern"] != r"\S":
            raise AuthoringModelError("AUTHORING_CONTRACT_INVALID")
        node["pattern"] = r"[\s\S]*\S[\s\S]*"
    return schema


def typed_audit_schema() -> dict:
    # Provider grammar omits regex only. The unchanged local parser still
    # enforces every pattern, length, target, reference and completeness rule.
    def project(node):
        if isinstance(node, dict):
            return {key: project(value) for key, value in node.items() if key != "pattern"}
        if isinstance(node, list):
            return [project(value) for value in node]
        return node
    return project(BoundedAuditDraft.model_json_schema())


def authoring_response_format(version: str, step: str, package: dict | None = None) -> dict:
    if version == "authoring-model/1.12" and step == "AUDIT":
        if package is None:
            raise AuthoringModelError("AUTHORING_PACKAGE_REQUIRED")
        return {"type": "json_schema", "json_schema": {"name": "citation_audit", "strict": True,
            "schema": prepare_citation_contract(package)["provider_schema_candidate"]}}
    if version in {"authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11"} and step == "AUDIT":
        schema = typed_audit_schema() if version in {"authoring-model/1.10", "authoring-model/1.11"} else portable_audit_schema() if version == "authoring-model/1.9" else BoundedAuditDraft.model_json_schema()
        return {"type": "json_schema", "json_schema": {"name": "bounded_audit", "strict": True, "schema": schema}}
    return {"type": "json_object"}


class AuthoringModel:
    def __init__(self, config: SelectedSmokeConfig, package_contract: str = "script-package/1.1",
                 *, rule_plan_enabled: bool = False, confirm_frozen_text: bool = False,
                 indexed_audit: bool = False, bounded_audit: bool = False, direct_audit_schema: bool = False, strict_audit_schema: bool = False, portable_audit_patterns: bool = False, typed_audit_schema: bool = False, runtime_audit_context: bool = False, citation_audit: bool = False) -> None:
        self.config = config
        self.package_contract = package_contract
        self.rule_plan_enabled = rule_plan_enabled
        self.confirm_frozen_text = confirm_frozen_text
        self.indexed_audit = indexed_audit
        self.bounded_audit = bounded_audit
        self.direct_audit_schema = direct_audit_schema
        self.strict_audit_schema = strict_audit_schema
        self.portable_audit_patterns = portable_audit_patterns
        self.typed_audit_schema = typed_audit_schema
        self.runtime_audit_context = runtime_audit_context
        self.citation_audit = citation_audit
        self._validate_config()

    def _prompt(self, step: Step) -> str:
        if self.runtime_audit_context and step == "AUDIT":
            try:
                prompt = (PROMPT_DIRECTORY / ("audit_citation_system_v1.txt" if self.citation_audit else "audit_runtime_system_v1.txt")).read_text("utf-8")
                return prompt + "\n服务端固定运行规则 runtime_contract：\n" + canonical_json(audit_runtime_contract())
            except (OSError, UnicodeError):
                raise AuthoringModelError("AUTHORING_PROMPT_UNAVAILABLE") from None
        if self.indexed_audit and step == "AUDIT":
            try:
                return (PROMPT_DIRECTORY / ("audit_bounded_system_v1.txt" if self.bounded_audit else "audit_indexed_system_v1.txt")).read_text("utf-8")
            except (OSError, UnicodeError):
                raise AuthoringModelError("AUTHORING_PROMPT_UNAVAILABLE") from None
        if self.rule_plan_enabled and step == "COMPILE":
            try:
                filename = "compiler_text_confirmation_v1.txt" if self.confirm_frozen_text else "compiler_text_system_v1.txt"
                return (PROMPT_DIRECTORY / filename).read_text("utf-8")
            except (OSError, UnicodeError):
                raise AuthoringModelError("AUTHORING_PROMPT_UNAVAILABLE") from None
        # The one-argument legacy path remains available to archived call fixtures.
        return _prompt(step) if self.package_contract == "script-package/1.1" else _prompt(step, self.package_contract)

    def _schema(self, step: Step) -> dict:
        if self.citation_audit and step == "AUDIT":
            return CitationAuditDraft.model_json_schema()
        if self.portable_audit_patterns and step == "AUDIT":
            return portable_audit_schema()
        if self.direct_audit_schema and step == "AUDIT":
            return BoundedAuditDraft.model_json_schema()
        if self.indexed_audit and step == "AUDIT":
            return {"output": (BoundedAuditDraft if self.bounded_audit else IndexedAuditDraft).model_json_schema()}
        if self.rule_plan_enabled and step == "COMPILE":
            return (CompilerTextConfirmation if self.confirm_frozen_text else CompilerTextDraft).model_json_schema()
        return _schema(step) if self.package_contract == "script-package/1.1" else _schema(step, self.package_contract)

    def _versions(self) -> tuple[str, str]:
        if self.citation_audit:
            return "authoring-model/1.12", "bailian-authoring-json/1.12"
        if self.runtime_audit_context:
            return "authoring-model/1.11", "bailian-authoring-json/1.11"
        if self.typed_audit_schema:
            return "authoring-model/1.10", "bailian-authoring-json/1.10"
        if self.portable_audit_patterns:
            return "authoring-model/1.9", "bailian-authoring-json/1.9"
        if self.strict_audit_schema:
            return "authoring-model/1.8", "bailian-authoring-json/1.8"
        if self.direct_audit_schema:
            return "authoring-model/1.7", "bailian-authoring-json/1.7"
        if self.bounded_audit:
            return "authoring-model/1.6", "bailian-authoring-json/1.6"
        if self.indexed_audit:
            return "authoring-model/1.5", "bailian-authoring-json/1.5"
        if self.confirm_frozen_text:
            return "authoring-model/1.4", "bailian-authoring-json/1.4"
        if self.rule_plan_enabled:
            return "authoring-model/1.3", "bailian-authoring-json/1.3"
        return ((AUTHORING_CONTRACT_VERSION_V12, REQUEST_CONTRACT_VERSION_V12)
                if self.package_contract == "script-package/1.2"
                else (AUTHORING_CONTRACT_VERSION, REQUEST_CONTRACT_VERSION))

    def _validate_config(self) -> None:
        try:
            config = self.config
            profile = PLAYER_PROVIDER_PROFILES["aliyun_bailian"]
            if (self.package_contract not in {"script-package/1.1", "script-package/1.2"}
                    or type(self.rule_plan_enabled) is not bool
                    or type(self.confirm_frozen_text) is not bool
                    or type(self.indexed_audit) is not bool
                    or type(self.bounded_audit) is not bool
                    or type(self.direct_audit_schema) is not bool
                    or type(self.strict_audit_schema) is not bool
                    or type(self.portable_audit_patterns) is not bool
                    or type(self.citation_audit) is not bool
                    or (self.citation_audit and not self.runtime_audit_context)
                    or type(self.runtime_audit_context) is not bool
                    or (self.runtime_audit_context and not self.typed_audit_schema)
                    or type(self.typed_audit_schema) is not bool
                    or (self.typed_audit_schema and not self.portable_audit_patterns)
                    or (self.portable_audit_patterns and not self.strict_audit_schema)
                    or (self.strict_audit_schema and not self.direct_audit_schema)
                    or (self.direct_audit_schema and not self.bounded_audit)
                    or (self.bounded_audit and not self.indexed_audit)
                    or (self.indexed_audit and not self.confirm_frozen_text)
                    or (self.confirm_frozen_text and not self.rule_plan_enabled)
                    or (self.rule_plan_enabled and self.package_contract != "script-package/1.2")
                    or config.profile != profile or config.base_url not in profile.allowed_base_urls
                    or config.model not in profile.allowed_models or not isinstance(config.api_key, str)
                    or not config.api_key.strip() or config.api_key.upper().startswith("CHANGE_ME")):
                raise ValueError
            prices = config.pricing
            if (not prices.paid_calls_enabled or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", prices.pricing_version)
                    or any(not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0 for rate in
                           (prices.input_rate_cny, prices.cached_input_rate_cny, prices.output_rate_cny))
                    or prices.cached_input_rate_cny > prices.input_rate_cny):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise AuthoringModelError("AUTHORING_CONFIG_INVALID") from None

    def snapshot(self) -> dict:
        self._validate_config()
        model_version, request_version = self._versions()
        return {"schema_version": model_version, "provider": self.config.profile.name,
                "model": self.config.model, "base_url": self.config.base_url,
                "pricing_version": self.config.pricing.pricing_version,
                "input_rate_cny": str(self.config.pricing.input_rate_cny),
                "cached_input_rate_cny": str(self.config.pricing.cached_input_rate_cny),
                "output_rate_cny": str(self.config.pricing.output_rate_cny),
                "timeout_seconds": MODEL_TIMEOUT_SECONDS, "max_completion_tokens": dict(OUTPUT_TOKEN_LIMITS),
                "max_input_tokens": MAX_INPUT_TOKENS, "max_call_cost_cny": str(MAX_CALL_COST_CNY),
                "prompt_hashes": {step: sha256(self._prompt(step).encode("utf-8")).hexdigest() for step in OUTPUT_TOKEN_LIMITS},
                "schema_hashes": {step: content_hash(self._schema(step)) for step in OUTPUT_TOKEN_LIMITS},
                "request_contract": request_version}

    def prepare(self, step: Step, context: dict, package: dict | None = None) -> PreparedAuthoringCall:
        self._validate_config()
        prompt = self._prompt(step)
        context = _context(context)
        if (context.get("package_contract", "script-package/1.1") != self.package_contract
                or ("rule_plan" in context) != self.rule_plan_enabled
                or (context.get("compiler_mode") == "CONFIRM_FROZEN_TEXT") != self.confirm_frozen_text
                or (context.get("audit_mode") in {"TARGET_SOURCE_INDEXES", "BOUNDED_TARGET_SOURCE_INDEXES", "DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.indexed_audit
                or (context.get("audit_mode") in {"BOUNDED_TARGET_SOURCE_INDEXES", "DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.bounded_audit
                or (context.get("audit_mode") in {"DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.direct_audit_schema
                or (context.get("audit_mode") in {"STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.strict_audit_schema
                or (context.get("audit_mode") in {"PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.portable_audit_patterns
                or (context.get("audit_mode") in {"TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.typed_audit_schema
                or (context.get("audit_mode") in {"RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) != self.runtime_audit_context
                or (context.get("audit_mode") == "CITATION_CATALOG") != self.citation_audit):
            raise AuthoringModelError("AUTHORING_CONTRACT_MISMATCH")
        if step == "COMPILE" and package is not None:
            raise AuthoringModelError("AUTHORING_PACKAGE_UNEXPECTED")
        if step == "AUDIT":
            if package is None:
                raise AuthoringModelError("AUTHORING_PACKAGE_REQUIRED")
            package = parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)["package"]
        schema = self._schema(step)
        citation_contract = None
        if self.citation_audit and step == "AUDIT":
            try:
                citation_contract = prepare_citation_contract(package)
            except CitationAuditError as exc:
                raise AuthoringModelError(exc.code) from None
            schema = citation_contract["local_schema"]
        user = {"task": step, "input": context}
        if self.rule_plan_enabled and step == "AUDIT":
            # The already checked candidate carries these exact rules. Avoid
            # sending a second copy; the full plan remains in prepared.context.
            user["input"] = {key: value for key, value in context.items() if key != "rule_plan"}
            user["rule_plan_hash"] = content_hash(context["rule_plan"])
        if step == "COMPILE":
            user["input"] = {key: context[key] for key in ("title", "player_count", "materials", "notes")}
            user["available_locators"] = _available_locators(context)
            if self.rule_plan_enabled:
                user["text_slots"] = slot_catalog(context["rule_plan"], include_text=self.confirm_frozen_text)
            if self.package_contract == "script-package/1.2":
                by_id = {source["id"]: source for source in context["sources"]}
                user["available_source_origins"] = [{"source_id": source_id, "kind": by_id[source_id]["kind"]}
                                                    for source_id in context["source_ids"]]
        if package is not None:
            user["candidate"] = package
            if self.runtime_audit_context:
                user["candidate_observations"] = candidate_observations(package)
            if citation_contract is not None:
                user["citation_catalog"] = citation_contract["catalog"]
            elif self.indexed_audit:
                user["audit_source_catalog"] = audit_source_catalog(package)
        system = prompt + "\nJSON Schema：\n" + canonical_json(schema)
        if self.package_contract == "script-package/1.2" and step == "COMPILE" and COMPILE_SCHEMA_TAIL_MARKER in prompt:
            # Historical prompts without the marker retain their exact rendering.
            sections = prompt.split(COMPILE_SCHEMA_TAIL_MARKER)
            if len(sections) != 2 or any(not section.strip() for section in sections):
                raise AuthoringModelError("AUTHORING_PROMPT_UNAVAILABLE")
            system = sections[0] + "\nJSON Schema：\n" + canonical_json(schema) + "\n" + sections[1]
        messages = (LLMMessage("system", system),
                    LLMMessage("user", canonical_json(user)))
        # One token per UTF-8 byte plus a fixed chat envelope is a conservative
        # upper bound; schema and instructions count before any network request.
        response_format = authoring_response_format(self._versions()[0], step, package)
        input_tokens = sum(len(message.content.encode("utf-8")) for message in messages) + 512
        if response_format["type"] == "json_schema":
            input_tokens += len(canonical_json(response_format).encode("utf-8"))
        if input_tokens > MAX_INPUT_TOKENS:
            raise AuthoringModelError("AUTHORING_INPUT_TOO_LARGE")
        output_tokens = OUTPUT_TOKEN_LIMITS[step]
        reservation = self.config.pricing.amount(input_tokens, output_tokens + 16)
        if reservation.cost_cny > MAX_CALL_COST_CNY:
            raise AuthoringModelError("AUTHORING_RESERVATION_TOO_LARGE")
        request_contract = {"version": self._versions()[1], "model": self.config.model,
                            "response_format": response_format, "temperature": 0,
                            "max_completion_tokens": output_tokens,
                            "extra_body": {"enable_thinking": False, "preserve_thinking": False},
                            "client_max_retries": 0, "timeout_seconds": MODEL_TIMEOUT_SECONDS,
                            "schema_hash": content_hash(schema)}
        if citation_contract is not None:
            request_contract["citation_binding"] = {key: citation_contract[key] for key in ("schema_version", "package_hash", "catalog_hash")}
        contract_hash = content_hash({"request": request_contract, "configuration": self.snapshot()})
        return PreparedAuthoringCall(step, messages, sha256(prompt.encode("utf-8")).hexdigest(), contract_hash,
                                     input_tokens, output_tokens, reservation, context, deepcopy(package), request_contract)

    async def call(self, step: Step, context: dict, package: dict | None = None,
                   *, prepared: PreparedAuthoringCall | None = None) -> dict:
        fresh = self.prepare(step, context, package)
        if prepared is not None and prepared != fresh:
            raise AuthoringModelError("AUTHORING_PREPARATION_CHANGED")
        prepared = fresh
        model_version, request_version = self._versions()
        receipt = {"schema_version": model_version, "provider": self.config.profile.name,
                   "model": self.config.model, "step": step, "prompt_hash": prepared.prompt_hash,
                   "contract_hash": prepared.contract_hash, "request_contract": request_version,
                   "pricing_version": self.config.pricing.pricing_version,
                   "reservation": prepared.reservation.to_metadata(), "usage_known": False, "usage": None,
                   "charged_cost_cny": str(prepared.reservation.cost_cny),
                   "estimated_cost_cny": str(prepared.reservation.cost_cny), "latency_ms": 0,
                   "output_diagnostics": [], "response_fingerprint": None, "response_finish": None}
        started = monotonic()

        def fail(code: str, details: list[dict] | None = None) -> NoReturn:
            receipt.update(result_code=code, latency_ms=max(0, int((monotonic() - started) * 1000)))
            receipt["output_diagnostics"] = validate_output_diagnostics([] if details is None else details)
            raise AuthoringModelError(code, receipt, details=receipt["output_diagnostics"]) from None

        client = None
        try:
            client = OpenAILLMService(api_key=self.config.api_key, base_url=self.config.base_url,
                                      model=self.config.model, client_max_retries=0)
            response = await asyncio.wait_for(client.chat_completion(
                list(prepared.messages), max_completion_tokens=prepared.max_completion_tokens,
                temperature=0, response_format=deepcopy(prepared.request_contract["response_format"]),
                extra_body={"enable_thinking": False, "preserve_thinking": False},
            ), timeout=MODEL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            fail("AUTHORING_TIMEOUT_USAGE_UNKNOWN")
        except Exception:
            fail("AUTHORING_CALL_FAILED_USAGE_UNKNOWN")
        finally:
            # An independent SDK instance must not accumulate open HTTP pools.
            sdk = getattr(client, "_client", None)
            if sdk is not None:
                try:
                    await asyncio.wait_for(sdk.close(), timeout=2)
                except Exception:
                    pass
        receipt["latency_ms"] = max(0, int((monotonic() - started) * 1000))
        # Capture before usage and JSON parsing, including rejected responses.
        # This fingerprints SDK message.content, not the raw HTTP response.
        receipt["response_fingerprint"] = {
            "content_sha256": _response_digest(getattr(response, "content", None)),
            "response_id_sha256": _response_digest(getattr(response, "request_id", None)),
        }
        receipt["response_finish"] = safe_response_finish(getattr(response, "finish_reason", None))
        normalized = normalize_provider_usage(getattr(response, "usage", None))
        if normalized is None or normalized.total_tokens > 1_000_000_000:
            fail("AUTHORING_USAGE_UNKNOWN")
        amount = self.config.pricing.amount(normalized.prompt_tokens, normalized.completion_tokens,
                                           normalized.cached_prompt_tokens, normalized.reasoning_tokens)
        receipt.update(usage_known=True, usage=amount.to_metadata(), charged_cost_cny=str(amount.cost_cny),
                       estimated_cost_cny=str(amount.cost_cny))
        if (normalized.prompt_tokens > prepared.reservation.prompt_tokens
                or normalized.completion_tokens > prepared.reservation.completion_tokens
                or amount.cost_cny > prepared.reservation.cost_cny):
            fail("AUTHORING_USAGE_EXCEEDS_RESERVATION")
        if getattr(response, "model", None) != self.config.model:
            fail("AUTHORING_RESPONSE_MODEL_MISMATCH")
        if getattr(response, "tool_calls", None):
            fail("AUTHORING_TOOL_CALL_REFUSED")
        if getattr(response, "reasoning_content", None) or normalized.reasoning_tokens:
            fail("AUTHORING_REASONING_REFUSED")
        if getattr(response, "finish_reason", None) != "stop":
            fail("AUTHORING_FINISH_INVALID")
        try:
            output = (parse_compiler_draft(getattr(response, "content", None), prepared.context) if step == "COMPILE"
                      else parse_citation_audit(getattr(response, "content", None), prepared.package, prepared.request_contract["citation_binding"]) if self.citation_audit
                      else parse_bounded_audit(getattr(response, "content", None), prepared.package) if self.bounded_audit
                      else parse_indexed_audit(getattr(response, "content", None), prepared.package) if self.indexed_audit
                      else validate_model_audit(getattr(response, "content", None), prepared.package))
        except AuthoringModelError as error:
            fail(error.code, error.details)
        receipt["result_code"] = "PASSED"
        return {"output": output, "receipt": receipt}
