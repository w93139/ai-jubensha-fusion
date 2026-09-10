"""Opt-in real smoke for an explicit frozen rule plan, not the legacy Compiler.

The prepared synthetic plan is an input artifact. It never repairs model output
or loads an earlier failed candidate. The unchanged fixture quality gate remains.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from time import monotonic

from src.fusion import investigation_authoring_smoke as smoke
from src.fusion.investigation_authoring_smoke import (
    REPOSITORY, FIXTURE_ID, FIXTURE_HASH, QUALITY_VERSION, MAX_TOTAL_COST_CNY, MAX_CALL_COST_CNY,
    InvestigationSmokeError, _require, _json, new_directory, _database, _accounting,
    read_paid_authorization, validate_candidate,
)
from src.fusion.authoring_model import AuthoringModelError, AUDIT_CATEGORIES, validate_model_audit
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_validation import content_hash
from src.fusion.provider_smoke import SelectedSmokeConfig, SmokeConfigurationError, load_selected_config
from src.schemas.authoring import AuthoringRequestV13, AuthoringRequestV14, AuthoringRequestV15, AuthoringRequestV16, AuthoringRequestV17, AuthoringRequestV18, AuthoringRequestV19, AuthoringRequestV110, AuthoringRequestV111, AuthoringRequestV112

DEFAULT_OUTPUT_ROOT = REPOSITORY.parent / "private-data/import-jobs/frozen-rule-plan-smoke"
CONFIRM_PAID = "TWO_SYNTHETIC_FROZEN_RULE_PLAN_CALLS"

def prepared_rule_plan(context):
    """Explicit synthetic author input, frozen before dispatch; never an output repair."""
    identifier = lambda value: value
    source_id = context["sources"][0]["id"]
    refs = lambda heading: [{"source_id": source_id, "anchor": heading}]
    a, b, start, end, find, unlock, key, card = map(identifier, ("a", "b", "start", "end", "find", "unlock", "key", "card"))
    package = {"schema_version": "script-package/1.2", **deepcopy({name: context[name] for name in
        ("script_key", "content_version", "title", "player_count", "sources")})}
    package.update({"introduction": {"text": smoke.INTRODUCTION, "sources": refs("开场介绍")},
        "characters": [{"id": a, "name": smoke.NAMES[0], "sources": refs("角色甲")},
                       {"id": b, "name": smoke.NAMES[1], "sources": refs("角色乙")}],
        "initial_phase_id": start, "phases": [
            {"id": start, "title": smoke.PHASE_NAMES[0], "next_phase_id": end, "sources": refs("入场阶段")},
            {"id": end, "title": smoke.PHASE_NAMES[1], "next_phase_id": None, "sources": refs("复盘阶段")}],
        "knowledge": [{"id": identifier("fact-" + local), "text": smoke.PRIVATE_FACTS[name], "kind": "FACT",
            "visibility": "CHARACTER_PRIVATE", "character_id": character, "disclosure": "KEEP_PRIVATE",
            "release": {"phase_id": start}, "sources": refs(heading)}
            for local, character, name, heading in (("a", a, smoke.NAMES[0], "角色甲"), ("b", b, smoke.NAMES[1], "角色乙"))],
        "evidence": [
            {"id": key, "text": smoke.KEY_TEXT, "visibility": "PUBLIC", "character_id": None, "disclosure": "PUBLIC",
             "release": {"phase_id": start, "required_action_ids": [find]}, "sources": refs("公开钥匙证据")},
            {"id": card, "text": smoke.CARD_TEXT, "visibility": "CHARACTER_PRIVATE", "character_id": b, "disclosure": "MAY_SHARE",
             "release": {"phase_id": start, "required_action_ids": [unlock]}, "sources": refs("乙的柜内证据")}],
        "truth": [{"id": identifier("truth"), "text": smoke.TRUTH_TEXT, "visibility": "SYSTEM_TRUTH", "sources": refs("系统真相")}],
        "settlement": {"phase_id": end, "truth_ids": [identifier("truth")],
            "instructions": {"text": smoke.SETTLEMENT, "sources": refs("结算说明")}},
        "mechanics": {"phase_budgets": [
            {"phase_id": start, "points": 3, "advance_policy": "REQUIRE_EXHAUSTED", "origin": "SOURCE_EXPLICIT", "sources": refs("入场阶段")},
            {"phase_id": end, "points": 0, "advance_policy": "ALLOW_REMAINING", "origin": "SOURCE_EXPLICIT", "sources": refs("复盘阶段")}],
            "actions": [
                {"id": find, "label": smoke.ACTION_LABELS[0], "cost": 1, "phase_ids": [start], "allowed_character_ids": [a, b],
                 "origin": "SOURCE_EXPLICIT", "sources": refs("调查动作一")},
                {"id": unlock, "label": smoke.ACTION_LABELS[1], "cost": 2, "phase_ids": [start], "allowed_character_ids": [a, b],
                 "required_action_ids": [find], "required_public_evidence_ids": [key],
                 "origin": "SOURCE_EXPLICIT", "sources": refs("调查动作二")}]}})
    return package

def make_fixture(root, *, confirm_frozen_text=False, indexed_audit=False, bounded_audit=False, direct_audit_schema=False, strict_audit_schema=False, portable_audit_patterns=False, typed_audit_schema=False, runtime_audit_context=False, citation_audit=False):
    store, request, base_context = smoke.make_fixture(root)
    plan = prepared_rule_plan(base_context)
    validate_candidate(base_context, plan)
    request = AuthoringRequestV13.model_validate(request.model_dump() | {"rule_plan": plan})
    if confirm_frozen_text:
        request = AuthoringRequestV14.model_validate(request.model_dump() | {"compiler_mode": "CONFIRM_FROZEN_TEXT"})
    if indexed_audit:
        request = AuthoringRequestV15.model_validate(request.model_dump() | {"audit_mode": "TARGET_SOURCE_INDEXES"})
    if bounded_audit:
        request = AuthoringRequestV16.model_validate(request.model_dump() | {"audit_mode": "BOUNDED_TARGET_SOURCE_INDEXES"})
    if direct_audit_schema:
        request = AuthoringRequestV17.model_validate(request.model_dump() | {"audit_mode": "DIRECT_BOUNDED_SOURCE_INDEXES"})
    if strict_audit_schema:
        request = AuthoringRequestV18.model_validate(request.model_dump() | {"audit_mode": "STRICT_BOUNDED_SOURCE_INDEXES"})
    if portable_audit_patterns:
        request = AuthoringRequestV19.model_validate(request.model_dump() | {"audit_mode": "PORTABLE_STRICT_SOURCE_INDEXES"})
    if typed_audit_schema:
        request = AuthoringRequestV110.model_validate(request.model_dump() | {"audit_mode": "TYPED_STRICT_SOURCE_INDEXES"})
    if runtime_audit_context:
        request = AuthoringRequestV111.model_validate(request.model_dump() | {"audit_mode": "RUNTIME_CONTEXT_SOURCE_INDEXES"})
    if citation_audit:
        request = AuthoringRequestV112.model_validate(request.model_dump() | {"audit_mode": "CITATION_CATALOG"})
    context = prepare_authoring_sources(store, request)
    _json(root / "rule-plan-input.json", plan)
    return store, request, context


class ObservedRulePlanModel(smoke.ObservedAuthoringModel):
    def __init__(self, config, context, root, authorized):
        super().__init__(config, context, root, authorized)
        self.rule_plan_enabled = True
        self.confirm_frozen_text = context.get("compiler_mode") == "CONFIRM_FROZEN_TEXT"
        self.indexed_audit = context.get("audit_mode") in {"TARGET_SOURCE_INDEXES", "BOUNDED_TARGET_SOURCE_INDEXES", "DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.bounded_audit = context.get("audit_mode") in {"BOUNDED_TARGET_SOURCE_INDEXES", "DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.direct_audit_schema = context.get("audit_mode") in {"DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.strict_audit_schema = context.get("audit_mode") in {"STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.portable_audit_patterns = context.get("audit_mode") in {"PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.typed_audit_schema = context.get("audit_mode") in {"TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.runtime_audit_context = context.get("audit_mode") in {"RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}
        self.citation_audit = context.get("audit_mode") == "CITATION_CATALOG"
        self._validate_config()
        validate_candidate(context, context["rule_plan"])


async def run_smoke(config: SelectedSmokeConfig, *, output_root: Path = DEFAULT_OUTPUT_ROOT,
                    execute: bool = False, paid_authorized: bool = False, confirm_frozen_text: bool = False,
                    indexed_audit: bool = False, bounded_audit: bool = False, direct_audit_schema: bool = False, strict_audit_schema: bool = False, portable_audit_patterns: bool = False, typed_audit_schema: bool = False, runtime_audit_context: bool = False, citation_audit: bool = False) -> tuple[int, dict]:
    from src.fusion.authoring_jobs import AuthoringJobStore
    from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
    from src.fusion.package_import import PackageImportService
    root = new_directory(output_root)
    receipt = {"schema_version": "frozen-rule-plan-smoke/1.9" if citation_audit else "frozen-rule-plan-smoke/1.8" if runtime_audit_context else "frozen-rule-plan-smoke/1.7" if typed_audit_schema else "frozen-rule-plan-smoke/1.6" if portable_audit_patterns else "frozen-rule-plan-smoke/1.5" if strict_audit_schema else "frozen-rule-plan-smoke/1.4" if direct_audit_schema else "frozen-rule-plan-smoke/1.3" if bounded_audit else "frozen-rule-plan-smoke/1.2" if indexed_audit else "frozen-rule-plan-smoke/1.1" if confirm_frozen_text else "frozen-rule-plan-smoke/1.0", "quality_version": QUALITY_VERSION,
        "fixture_id": FIXTURE_ID, "fixture_hash": FIXTURE_HASH, "data_class": "synthetic_noncommercial",
        "publication_ready": False, "runtime_ready": False, "artifact_directory": str(root),
        "mode": "execute" if execute else "preview", "status": "FAILED", "result_code": "SMOKE_LOCAL_FAILURE",
        "maximum_model_requests": 2 if execute else 0, "model_requests": 0, "calls_started": [],
        "maximum_total_reservation_cny": str(MAX_TOTAL_COST_CNY), "maximum_call_reservation_cny": str(MAX_CALL_COST_CNY),
        "charged_cost_cny": "0", "total_reserved_cny": "0", "paid_authorized": paid_authorized}
    engine, model, jobs, job_id = None, None, None, None
    started = monotonic()
    try:
        _require(not indexed_audit or confirm_frozen_text, "SMOKE_CONTRACT_MISMATCH")
        _require(not bounded_audit or indexed_audit, "SMOKE_CONTRACT_MISMATCH")
        _require(not direct_audit_schema or bounded_audit, "SMOKE_CONTRACT_MISMATCH")
        _require(not strict_audit_schema or direct_audit_schema, "SMOKE_CONTRACT_MISMATCH")
        _require(not portable_audit_patterns or strict_audit_schema, "SMOKE_CONTRACT_MISMATCH")
        _require(not typed_audit_schema or portable_audit_patterns, "SMOKE_CONTRACT_MISMATCH")
        _require(not runtime_audit_context or typed_audit_schema, "SMOKE_CONTRACT_MISMATCH")
        _require(not citation_audit or runtime_audit_context, "SMOKE_CONTRACT_MISMATCH")
        if citation_audit:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True, typed_audit_schema=True, runtime_audit_context=True, citation_audit=True)
        elif runtime_audit_context:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True, typed_audit_schema=True, runtime_audit_context=True)
        elif typed_audit_schema:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True, typed_audit_schema=True)
        elif portable_audit_patterns:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True)
        elif strict_audit_schema:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True)
        elif direct_audit_schema:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True)
        elif bounded_audit:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True)
        elif indexed_audit:
            store, request, context = make_fixture(root, confirm_frozen_text=True, indexed_audit=True)
        else:
            store, request, context = make_fixture(root, confirm_frozen_text=True) if confirm_frozen_text else make_fixture(root)
        model = ObservedRulePlanModel(config, context, root, paid_authorized if execute else False)
        receipt["rule_plan_hash"] = content_hash(context["rule_plan"])
        receipt["rule_plan_is_input_not_approval"] = True
        prepared = model.prepare("COMPILE", context)
        receipt.update({"model": model.snapshot(), "context_hash": content_hash(context), "bundle_hash": request.bundle_hash,
            "compile_prepared_hash": content_hash({"prompt_hash": prepared.prompt_hash, "contract_hash": prepared.contract_hash,
                "input_tokens": prepared.input_tokens, "reservation": prepared.reservation.to_metadata()}),
            "compile_reservation": prepared.reservation.to_metadata(), "compile_input_token_bound": prepared.input_tokens,
            "audit_reservation_not_yet_known": True, "audit_maximum_allowed_reservation_cny": str(MAX_CALL_COST_CNY),
            "compile_plus_audit_ceiling_cny": str(prepared.reservation.cost_cny + MAX_CALL_COST_CNY)})
        _json(root / "preview.json", receipt | {"status": "PREVIEW", "result_code": "PREVIEW_READY"})
        if execute:
            _require(paid_authorized is True, "SMOKE_PAID_CALLS_DISABLED")
        engine, factory = _database(root)
        jobs = AuthoringJobStore(factory)
        submitted = await submit_authoring_job(jobs, store, model, request, 1)
        job_id = submitted["id"]
        model.jobs, model.job_id = jobs, job_id
        if not execute:
            _require(submitted["state"] == "QUEUED" and not submitted["attempts"], "SMOKE_PREVIEW_DISPATCHED")
            receipt.update(status="PREVIEW", result_code="PREVIEW_READY")
        else:
            result = await AuthoringRunner(jobs, store, model).run(job_id, allow_paid=True)
            restored = AuthoringJobStore(factory).get(job_id)
            _require(restored == result, "SMOKE_JOB_RELOAD_FAILED")
            receipt["job_reload_verified"] = True
            receipt["result_code"] = result["error_code"] or "SMOKE_WORKFLOW_INCOMPLETE"
            if result["state"] == "COMPLETED":
                attempts = {item["step"]: item for item in result["attempts"]}
                _require(set(attempts) == {"COMPILE", "AUDIT"} and model.calls_started == ["COMPILE", "AUDIT"]
                         and all(item["status"] == "SUCCEEDED" and item["receipt"]["usage_known"] for item in attempts.values()),
                         "SMOKE_ATTEMPT_LEDGER_MISMATCH")
                package = attempts["COMPILE"]["output"]["package"]
                with factory() as db:
                    candidate = PackageImportService(db).get_version(result["candidate_version_id"])
                _require(candidate["package"] == package, "SMOKE_CANDIDATE_RELOAD_FAILED")
                report = store.get_report(result["source_report_hash"])
                _require(report["valid"] and not report["issues"] and not report["issues_truncated"]
                         and report["package_hash"] == content_hash(package) and report["bundle_hash"] == request.bundle_hash
                         and report["verifier_version"] == "source-verifier/1.2", "SMOKE_SOURCE_VERIFICATION_FAILED")
                quality = validate_candidate(context, package)
                audit = validate_model_audit(attempts["AUDIT"]["output"], package)
                _require(set(audit["coverage"]) == AUDIT_CATEGORIES, "SMOKE_AUDIT_COVERAGE_FAILED")
                quality.update(audit_findings=len(audit["findings"]), audit_blockers=sum(
                    item["severity"] == "BLOCKER" for item in audit["findings"]))
                _json(root / "candidate.json", package)
                _json(root / "audit.json", audit)
                receipt["quality"] = quality
                receipt["result_code"] = "SMOKE_AUDIT_BLOCKERS_FOUND" if quality["audit_blockers"] else "PASSED"
                if receipt["result_code"] == "PASSED":
                    receipt["status"] = "PASSED"
    except (InvestigationSmokeError, AuthoringModelError, SmokeConfigurationError) as exc:
        receipt["result_code"] = exc.code
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        receipt["result_code"] = "SMOKE_LOCAL_FAILURE"
    finally:
        if model is not None:
            receipt.update(calls_started=model.calls_started, model_requests=len(model.calls_started),
                           total_reserved_cny=str(model.total_reserved))
        if jobs is not None and job_id is not None:
            try:
                receipt.update(_accounting(AuthoringJobStore(jobs.session_factory).get(job_id)))
            except (Exception, KeyboardInterrupt):
                # Absence of a verified receipt is never evidence of zero cost.
                receipt.update(status="FAILED", result_code="SMOKE_LEDGER_UNREADABLE", charged_cost_cny=None,
                               usage_known=False, accounting_basis="UNAVAILABLE_LEDGER")
            else:
                if Decimal(receipt["charged_cost_cny"]) > MAX_TOTAL_COST_CNY:
                    receipt.update(status="FAILED", result_code="SMOKE_ACCOUNTED_COST_EXCEEDED")
        if engine is not None:
            engine.dispose()
        receipt["duration_ms"] = max(0, int((monotonic() - started) * 1000))
        try:
            _json(root / "receipt.json", receipt)
        except OSError:
            receipt.update(status="FAILED", result_code="SMOKE_RECEIPT_WRITE_FAILED")
    return (0 if receipt["status"] in {"PREVIEW", "PASSED"} else 3), receipt

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="固定规则草案1.3摘录验收；默认预审零SDK，执行最多Compiler/Audit各一次。")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-frozen-text", action="store_true", help="显式选择 1.4 固定正文检查；旧 1.3 不升级")
    parser.add_argument("--indexed-audit", action="store_true", help="显式选择 1.5 来源编号审核")
    parser.add_argument("--confirm-paid", choices=(CONFIRM_PAID,))
    parser.add_argument("--bounded-audit", action="store_true", help="显式选择 1.6 简短审核，配合 --indexed-audit")
    parser.add_argument("--direct-audit-schema", action="store_true", help="显式选择 1.7 顶层审核格式，配合 --bounded-audit")
    parser.add_argument("--strict-audit-schema", action="store_true", help="显式选择 1.8 服务商格式约束，配合 --direct-audit-schema")
    parser.add_argument("--portable-audit-patterns", action="store_true", help="显式选择 1.9 字符串约束兼容，配合 --strict-audit-schema")
    parser.add_argument("--typed-audit-schema", action="store_true", help="显式选择 1.10，配合 --portable-audit-patterns")
    parser.add_argument("--runtime-audit-context", action="store_true", help="显式选择 1.11，配合 --typed-audit-schema")
    parser.add_argument("--citation-audit", action="store_true", help="显式选择 1.12，配合 --runtime-audit-context")
    args = parser.parse_args(argv)
    try:
        _require(not args.execute or args.confirm_paid == CONFIRM_PAID, "SMOKE_EXPLICIT_CONFIRMATION_REQUIRED")
        authorized = read_paid_authorization() if args.execute else False
        _require(not args.execute or authorized, "SMOKE_PAID_CALLS_DISABLED")
        config = load_selected_config("aliyun_bailian")
        code, receipt = asyncio.run(run_smoke(config, output_root=args.output_root, execute=args.execute,
                                              paid_authorized=authorized, confirm_frozen_text=args.confirm_frozen_text,
                                              indexed_audit=args.indexed_audit, bounded_audit=args.bounded_audit, direct_audit_schema=args.direct_audit_schema, strict_audit_schema=args.strict_audit_schema, portable_audit_patterns=args.portable_audit_patterns, typed_audit_schema=args.typed_audit_schema, runtime_audit_context=args.runtime_audit_context, citation_audit=args.citation_audit))
    except (InvestigationSmokeError, AuthoringModelError, SmokeConfigurationError) as exc:
        code, receipt = 2, {"status": "REFUSED", "result_code": exc.code, "model_requests": 0, "publication_ready": False}
    except (Exception, KeyboardInterrupt):
        code, receipt = 3, {"status": "FAILED", "result_code": "SMOKE_LOCAL_FAILURE", "publication_ready": False}
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return code
