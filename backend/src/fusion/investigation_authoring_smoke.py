"""Opt-in, fixed synthetic v1.2 Compiler/Audit acceptance with durable usage.

No source, prompt, model or database argument is accepted. This harness never
repairs a generated candidate and never approves or publishes one.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
from time import monotonic

from dotenv import dotenv_values

from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, AUDIT_CATEGORIES, parse_compiler_output, validate_model_audit
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_investigation_rules import PackageInvestigationRules
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.provider_smoke import SelectedSmokeConfig, SmokeConfigurationError, load_selected_config
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.authoring import AuthoringRequestV12


REPOSITORY = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = REPOSITORY.parent / "private-data/import-jobs/investigation-authoring-smoke"
CONFIRM_PAID = "TWO_SYNTHETIC_INVESTIGATION_AUTHORING_CALLS"
MAX_TOTAL_COST_CNY = Decimal("0.10")
MAX_CALL_COST_CNY = Decimal("0.05")
FIXTURE_ID = "synthetic-investigation-authoring-v1"
FIXTURE_TITLE = "虚构展馆钥匙调查"
QUALITY_VERSION = "investigation-authoring-smoke-quality/1.0"
INTRODUCTION = "两位值班员在展馆寻找共享钥匙，随后核对资料柜中的封条。"
SETTLEMENT = "说明共享钥匙如何打开资料柜，核对唯一系统真相后结束。"
NAMES = ("值班员甲", "值班员乙")
PHASE_NAMES = ("入场", "复盘")
ACTION_LABELS = ("寻找公开钥匙", "打开资料柜")
PRIVATE_FACTS = {NAMES[0]: "我把红色记录本放在自己的抽屉里。", NAMES[1]: "我把绿色铅笔放在自己的口袋里。"}
KEY_TEXT = "钥匙牌写着：这把共享钥匙可以打开资料柜。"
CARD_TEXT = "蓝色封条编号为七，背面画有三角形。"
TRUTH_TEXT = "资料柜中的蓝色封条是展馆维护人员留下的检修标记。"
FIXTURE_TEXT = f"""# 合成资料范围
这是独立创作的完整接口测试原始资料，不来自商业剧本，没有缺页、OCR、图片或编辑补充。只包含下列两名角色、两个阶段、两条私人事实、两张证据、两个调查动作和唯一系统真相。所有规则 origin 都是 SOURCE_EXPLICIT；不得增加其他内容、费用、前置或奖励。
# 开场介绍
{INTRODUCTION}
# 角色甲
{NAMES[0]}
这是可玩的角色甲；开始时仅知道下列本人事实，不知道乙的私人事实和系统真相。
本人的开场事实为 FACT，CHARACTER_PRIVATE，KEEP_PRIVATE，不得公开原文；入场阶段无任何证据或行动前置。
{PRIVATE_FACTS[NAMES[0]]}
# 角色乙
{NAMES[1]}
这是可玩的角色乙；开始时仅知道下列本人事实，不知道甲的私人事实和系统真相。
本人的开场事实为 FACT，CHARACTER_PRIVATE，KEEP_PRIVATE，不得公开原文；入场阶段无任何证据或行动前置。
{PRIVATE_FACTS[NAMES[1]]}
# 入场阶段
{PHASE_NAMES[0]}
这是 initial_phase，下一阶段是复盘。该阶段共享行动预算恰好3点，advance_policy为REQUIRE_EXHAUSTED，剩余0点才能由当前真人推进。点数各阶段独立、不结转；两个动作每局全局最多成功一次。
# 调查动作一
{ACTION_LABELS[0]}
消耗1点，仅在入场阶段可执行，两位角色甲和乙都明确获准执行；没有行动前置，也没有公开证据前置。成功后使下方钥匙证据公开。该钥匙是全局共享公开前置，不是个人物品，不存在转交规则。
# 公开钥匙证据
{KEY_TEXT}
这是一张证据，visibility=PUBLIC，character_id=null，disclosure=PUBLIC。解锁阶段下限为入场，唯一 required_action 是寻找公开钥匙，无 required_public_evidence。动作成功以前不得提前展示。
# 调查动作二
{ACTION_LABELS[1]}
消耗2点，仅在入场阶段可执行，两位角色甲和乙都明确获准执行。必须同时满足两个前置：已经完成寻找公开钥匙动作，并且上述钥匙证据已经公开。成功后只向乙发放下方蓝色封条证据；甲执行也不能直接读取乙的私人证据原文。
# 乙的柜内证据
{CARD_TEXT}
这是一张证据，visibility=CHARACTER_PRIVATE，character_id为乙，disclosure=MAY_SHARE，乙可以自行公开原文。解锁阶段下限为入场，唯一 required_action 是打开资料柜，没有额外公开证据前置。动作以前乙也不可读取；动作以后甲只能在乙分享后读取。
# 复盘阶段
{PHASE_NAMES[1]}
这是唯一末阶段，next_phase_id=null。共享预算0点，advance_policy=ALLOW_REMAINING，没有调查动作。预算不结转；只进行下方唯一结算。
# 系统真相
{TRUTH_TEXT}
这是一条 SYSTEM_TRUTH，不能作为角色知识或证据提前公开，只有明确结算后展示。
# 结算说明
结算绑定复盘，truth_ids只引用上面的唯一系统真相；不使用投票、分支结局、评分、随机、关键词、记忆转述或额外资源。结算正文如下。
{SETTLEMENT}
"""
FIXTURE_HASH = sha256(FIXTURE_TEXT.encode()).hexdigest()


class InvestigationSmokeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise InvestigationSmokeError(code)


def _write(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _json(path: Path, value: dict) -> None:
    _write(path, canonical_json(value))


def new_directory(base: Path) -> Path:
    base = Path(base).absolute()
    _require(not any(item.is_symlink() for item in (base, *base.parents)), "SMOKE_OUTPUT_SYMLINK_REFUSED")
    _require(not base.resolve().is_relative_to(REPOSITORY.resolve()), "SMOKE_OUTPUT_MUST_BE_PRIVATE")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require(base.is_dir() and not base.stat().st_mode & 0o077 and base.stat().st_uid == os.getuid(),
             "SMOKE_OUTPUT_MUST_BE_PRIVATE")
    return Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=base))


def make_fixture(root: Path) -> tuple[SourceBundleStore, AuthoringRequestV12, dict]:
    inputs = root / "synthetic-inputs"
    inputs.mkdir(mode=0o700)
    _write(inputs / "fixture.md", FIXTURE_TEXT)
    store = SourceBundleStore(root / "source-bundles")
    bundle = store.freeze(inputs, {"schema_version": "source-plan/1.0", "script_key": FIXTURE_ID,
        "edition": "synthetic-v1", "notes": ["独立虚构原始测试资料；没有商业正文或人工批准。"],
        "sources": [{"relative_path": "fixture.md", "kind": "original", "material_type": "host"}]})
    request = AuthoringRequestV12(idempotency_key=FIXTURE_ID, bundle_hash=bundle["bundle_hash"],
        source_ids=[bundle["sources"][0]["id"]], title=FIXTURE_TITLE, content_version="synthetic-v1",
        player_count=2, package_contract="script-package/1.2")
    return store, request, prepare_authoring_sources(store, request)


def validate_candidate(context: dict, package: dict) -> dict:
    # Exact, unmodified model/source checks come before our semantic fixture
    # assertions. IDs and equivalent locator choices remain model-generated.
    package = parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)["package"]
    _require(context["package_contract"] == package["schema_version"] == "script-package/1.2"
             and package["script_key"] == FIXTURE_ID and package["title"] == FIXTURE_TITLE,
             "SMOKE_METADATA_MISMATCH")
    _require(package["introduction"]["text"] == INTRODUCTION
             and package["settlement"]["instructions"]["text"] == SETTLEMENT, "SMOKE_NARRATIVE_MISMATCH")
    _require(len(package["characters"]) == 2 and {item["name"] for item in package["characters"]} == set(NAMES),
             "SMOKE_CHARACTER_MISMATCH")
    characters = {item["name"]: item["id"] for item in package["characters"]}
    phase_index = {item["id"]: item for item in package["phases"]}
    initial, ending = package["initial_phase_id"], package["settlement"]["phase_id"]
    _require(len(phase_index) == 2 and initial != ending and phase_index[initial]["title"] == PHASE_NAMES[0]
             and phase_index[ending]["title"] == PHASE_NAMES[1] and phase_index[initial]["next_phase_id"] == ending
             and phase_index[ending]["next_phase_id"] is None, "SMOKE_PHASE_MISMATCH")
    _require(len(package["knowledge"]) == 2, "SMOKE_PRIVATE_FACT_MISMATCH")
    for name, text in PRIVATE_FACTS.items():
        _require(any(item["text"] == text and item["kind"] == "FACT" and item["visibility"] == "CHARACTER_PRIVATE"
                     and item["character_id"] == characters[name] and item["disclosure"] == "KEEP_PRIVATE"
                     and item["release"]["phase_id"] == initial and not item["release"].get("required_action_ids")
                     and not item["release"].get("required_public_evidence_ids") for item in package["knowledge"]),
                 "SMOKE_PRIVATE_FACT_MISMATCH")
    actions = package["mechanics"]["actions"]
    _require(len(actions) == 2 and {item["label"] for item in actions} == set(ACTION_LABELS), "SMOKE_ACTION_MISMATCH")
    actions = {item["label"]: item for item in actions}
    find_key, open_case = (actions[label] for label in ACTION_LABELS)
    _require(len(package["evidence"]) == 2 and {item["text"] for item in package["evidence"]} == {KEY_TEXT, CARD_TEXT},
             "SMOKE_EVIDENCE_MISMATCH")
    evidence = {item["text"]: item for item in package["evidence"]}
    key, card = evidence[KEY_TEXT], evidence[CARD_TEXT]
    for action, cost in ((find_key, 1), (open_case, 2)):
        _require(action["cost"] == cost and action["phase_ids"] == [initial]
                 and set(action["allowed_character_ids"]) == set(characters.values())
                 and action["origin"] == "SOURCE_EXPLICIT", "SMOKE_ACTION_SCOPE_OR_COST_MISMATCH")
    _require(not find_key.get("required_action_ids") and not find_key.get("required_public_evidence_ids")
             and open_case.get("required_action_ids") == [find_key["id"]]
             and open_case.get("required_public_evidence_ids") == [key["id"]], "SMOKE_ACTION_PREREQUISITE_MISMATCH")
    for item, action in ((key, find_key), (card, open_case)):
        _require(item["release"]["phase_id"] == initial and item["release"].get("required_action_ids") == [action["id"]]
                 and not item["release"].get("required_public_evidence_ids"), "SMOKE_MATERIAL_PREREQUISITE_MISMATCH")
    _require(key["visibility"] == "PUBLIC" and key["character_id"] is None and key["disclosure"] == "PUBLIC"
             and card["visibility"] == "CHARACTER_PRIVATE" and card["character_id"] == characters[NAMES[1]]
             and card["disclosure"] == "MAY_SHARE", "SMOKE_MATERIAL_PERMISSION_MISMATCH")
    budgets = package["mechanics"]["phase_budgets"]
    _require(len(budgets) == 2 and {item["phase_id"] for item in budgets} == {initial, ending}, "SMOKE_BUDGET_MISMATCH")
    for budget in budgets:
        points, policy = (3, "REQUIRE_EXHAUSTED") if budget["phase_id"] == initial else (0, "ALLOW_REMAINING")
        _require(budget["points"] == points and budget["advance_policy"] == policy
                 and budget["origin"] == "SOURCE_EXPLICIT", "SMOKE_BUDGET_MISMATCH")
    _require(len(package["truth"]) == 1 and package["truth"][0]["text"] == TRUTH_TEXT
             and package["truth"][0]["visibility"] == "SYSTEM_TRUTH"
             and package["settlement"]["truth_ids"] == [package["truth"][0]["id"]], "SMOKE_TRUTH_MISMATCH")
    # Exercise both possible human selections. No LLM decides point spending,
    # private grants, sharing rights or settlement here.
    for human in characters.values():
        engine = PackageInvestigationRules(package, human)
        _require(not engine.view()["public_evidence"] and not engine.view()["private_evidence"], "SMOKE_EARLY_GRANT")
        for actor in characters.values():
            _require(not engine.role_context(actor)["materials"], "SMOKE_PRIVATE_MODEL_INPUT_LEAK")
        engine.apply("PERFORM_ACTION", {"action_id": find_key["id"]})
        _require(engine.view()["mechanics"]["remaining_points"] == 2
                 and engine.view()["private_evidence"] == [], "SMOKE_INVESTIGATION_FLOW_MISMATCH")
        engine.apply("PERFORM_ACTION", {"action_id": open_case["id"]})
        _require(engine.view()["mechanics"]["remaining_points"] == 0 and engine.view()["can_advance"],
                 "SMOKE_INVESTIGATION_FLOW_MISMATCH")
        if human == characters[NAMES[1]]:
            _require([item["id"] for item in engine.view()["private_evidence"]] == [card["id"]], "SMOKE_PRIVATE_GRANT_MISMATCH")
            engine.apply("SHARE_MATERIAL", {"collection": "evidence", "id": card["id"]})
        else:
            _require(not engine.view()["private_evidence"] and CARD_TEXT not in canonical_json(engine.view()), "SMOKE_OTHER_PRIVATE_LEAK")
            engine.apply_reply(characters[NAMES[1]], [{"collection": "evidence", "id": card["id"]}])
        _require(TRUTH_TEXT not in canonical_json(engine.view()), "SMOKE_EARLY_TRUTH")
        engine.apply("ADVANCE_PHASE")
        _require(engine.view()["mechanics"]["remaining_points"] == 0 and engine.view()["settlement"] is None,
                 "SMOKE_PHASE_BUDGET_MISMATCH")
        engine.apply("SETTLE")
        _require(engine.view()["settlement"] == {"text": SETTLEMENT,
            "truths": [{"id": package["truth"][0]["id"], "text": TRUTH_TEXT}]}, "SMOKE_SETTLEMENT_MISMATCH")
    return {"quality_version": QUALITY_VERSION, "package_hash": content_hash(package), "both_human_roles_completed": True,
            "exact_fixture_mapping": True, "source_checks_preserved": True, "private_grants_verified": True,
            "action_costs_and_prerequisites_verified": True, "unique_settlement_verified": True}


class ObservedAuthoringModel(AuthoringModel):
    def __init__(self, config: SelectedSmokeConfig, context: dict, root: Path, authorized: bool):
        super().__init__(config, package_contract="script-package/1.2")
        self.context_hash, self.root, self.authorized = content_hash(context), root, authorized
        self.jobs, self.job_id = None, None
        self.plans, self.calls_started = {}, []
        self.total_reserved = Decimal("0")
        self.compile_package_hash = None

    def prepare(self, step, context, package=None):
        if content_hash(context) != self.context_hash:
            raise AuthoringModelError("SMOKE_CONTEXT_CHANGED")
        prepared = super().prepare(step, context, package)
        if step == "AUDIT":
            try:
                validate_candidate(context, package)
            except InvestigationSmokeError as exc:
                raise AuthoringModelError(exc.code) from None
        existing = self.plans.get(step)
        if existing is not None and existing != prepared:
            raise AuthoringModelError("SMOKE_PREPARED_CHANGED")
        total = sum((item.reservation.cost_cny for name, item in self.plans.items() if name != step), Decimal("0"))
        if prepared.reservation.cost_cny > MAX_CALL_COST_CNY or total + prepared.reservation.cost_cny > MAX_TOTAL_COST_CNY:
            raise AuthoringModelError("SMOKE_RESERVATION_EXCEEDED")
        self.plans[step] = prepared
        return prepared

    async def call(self, step, context, package=None, *, prepared=None):
        if (self.authorized is not True or len(self.calls_started) >= 2
                or step != ("COMPILE", "AUDIT")[len(self.calls_started)]):
            raise AuthoringModelError("SMOKE_DISPATCH_REFUSED")
        fresh = self.prepare(step, context, package)
        if prepared != fresh or (step == "AUDIT" and content_hash(package) != self.compile_package_hash):
            raise AuthoringModelError("SMOKE_PREPARED_CHANGED")
        job = self.jobs.get(self.job_id)
        attempts = [item for item in job["attempts"] if item["step"] == step]
        expected = {"step": step, "prompt_hash": fresh.prompt_hash, "contract_hash": fresh.contract_hash,
            "input_tokens": fresh.input_tokens, "max_completion_tokens": fresh.max_completion_tokens,
            "reservation": fresh.reservation.to_metadata(), "request_contract": fresh.request_contract}
        if (len(attempts) != 1 or attempts[0]["status"] != "IN_FLIGHT" or attempts[0]["prepared"] != expected
                or len(job["attempts"]) != len(self.calls_started) + 1):
            raise AuthoringModelError("SMOKE_RESERVATION_NOT_DURABLE")
        _json(self.root / f"dispatch-{step.lower()}.json", {"step": step, "job_id": self.job_id,
            "attempt_id": attempts[0]["id"], "prepared_hash": content_hash(expected), "reservation_committed": True,
            "reservation": fresh.reservation.to_metadata(), "maximum_sdk_requests": 1})
        self.calls_started.append(step)
        self.total_reserved += fresh.reservation.cost_cny
        result = await super().call(step, context, package, prepared=fresh)
        if step == "COMPILE" and result["output"]["status"] == "CANDIDATE":
            self.compile_package_hash = content_hash(result["output"]["package"])
        return result


def _database(root):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from src.db.base import SQLAlchemyBase
    from src.db.models import ScriptImportJob, ScriptPackageVersion, User
    from src.db.models.authoring_job import AuthoringAttempt, AuthoringJob
    path = root / "synthetic-authoring.sqlite3"
    _write(path, "")
    engine = create_engine("sqlite:///" + str(path), connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def connect(connection, _record):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    SQLAlchemyBase.metadata.create_all(engine, tables=[model.__table__ for model in
        (User, ScriptPackageVersion, ScriptImportJob, AuthoringJob, AuthoringAttempt)])
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(id=1, username=FIXTURE_ID, email=FIXTURE_ID + "@example.invalid",
            hashed_password="SYNTHETIC_LOGIN_DISABLED", is_active=True, is_admin=True))
    return engine, factory


def _accounting(job: dict) -> dict:
    calls = []
    for attempt in job["attempts"]:
        receipt = attempt["receipt"]
        calls.append({"id": attempt["id"], "step": attempt["step"], "status": attempt["status"],
            "output_hash": attempt["output_hash"], "error_code": attempt["error_code"],
            "reservation": attempt["prepared"]["reservation"], "receipt": receipt})
    return {"job_id": job["id"], "job_state": job["state"], "candidate_version_id": job["candidate_version_id"],
        "source_report_hash": job["source_report_hash"], "charged_cost_cny": job["charged_cost_cny"],
        "accounting_basis": "KNOWN_USAGE_AND_UNSETTLED_RESERVATIONS",
        "usage_known": all(item["receipt"] is not None and item["receipt"]["usage_known"] for item in calls),
        "attempts": calls}


async def run_smoke(config: SelectedSmokeConfig, *, output_root: Path = DEFAULT_OUTPUT_ROOT,
                    execute: bool = False, paid_authorized: bool = False) -> tuple[int, dict]:
    from src.fusion.authoring_jobs import AuthoringJobStore
    from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
    from src.fusion.package_import import PackageImportService
    root = new_directory(output_root)
    receipt = {"schema_version": "investigation-authoring-smoke/1.0", "quality_version": QUALITY_VERSION,
        "fixture_id": FIXTURE_ID, "fixture_hash": FIXTURE_HASH, "data_class": "synthetic_noncommercial",
        "publication_ready": False, "runtime_ready": False, "artifact_directory": str(root),
        "mode": "execute" if execute else "preview", "status": "FAILED", "result_code": "SMOKE_LOCAL_FAILURE",
        "maximum_model_requests": 2 if execute else 0, "model_requests": 0, "calls_started": [],
        "maximum_total_reservation_cny": str(MAX_TOTAL_COST_CNY), "maximum_call_reservation_cny": str(MAX_CALL_COST_CNY),
        "charged_cost_cny": "0", "total_reserved_cny": "0", "paid_authorized": paid_authorized}
    engine, model, jobs, job_id = None, None, None, None
    started = monotonic()
    try:
        store, request, context = make_fixture(root)
        model = ObservedAuthoringModel(config, context, root, paid_authorized if execute else False)
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


def read_paid_authorization(env_path: Path = REPOSITORY / ".env") -> bool:
    override = os.environ.get("ENABLE_PAID_MODEL_CALLS")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on"}
    try:
        _require(env_path.is_file() and env_path.stat().st_size <= 1000000, "SMOKE_AUTHORIZATION_UNAVAILABLE")
        selected = [line for line in env_path.read_text("utf-8").splitlines()
                    if re.match(r"^\s*(?:export\s+)?ENABLE_PAID_MODEL_CALLS\s*=", line)]
        _require(len(selected) <= 1, "SMOKE_AUTHORIZATION_AMBIGUOUS")
        value = dotenv_values(stream=StringIO(selected[0]), interpolate=False).get("ENABLE_PAID_MODEL_CALLS") if selected else None
        return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}
    except (OSError, UnicodeError):
        raise InvestigationSmokeError("SMOKE_AUTHORIZATION_UNAVAILABLE") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="固定虚构1.2调查包创作验收；默认预审零SDK，执行最多Compiler/Audit各一次。")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-paid", choices=(CONFIRM_PAID,))
    args = parser.parse_args(argv)
    try:
        _require(not args.execute or args.confirm_paid == CONFIRM_PAID, "SMOKE_EXPLICIT_CONFIRMATION_REQUIRED")
        authorized = read_paid_authorization() if args.execute else False
        _require(not args.execute or authorized, "SMOKE_PAID_CALLS_DISABLED")
        config = load_selected_config("aliyun_bailian")
        code, receipt = asyncio.run(run_smoke(config, output_root=args.output_root, execute=args.execute, paid_authorized=authorized))
    except (InvestigationSmokeError, AuthoringModelError, SmokeConfigurationError) as exc:
        code, receipt = 2, {"status": "REFUSED", "result_code": exc.code, "model_requests": 0, "publication_ready": False}
    except (Exception, KeyboardInterrupt):
        code, receipt = 3, {"status": "FAILED", "result_code": "SMOKE_LOCAL_FAILURE", "publication_ready": False}
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return code
