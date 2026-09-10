"""Opt-in, synthetic-only Compiler/Audit smoke through the persistent runner.

This script never accepts source paths, prompts, production database settings,
or commercial materials. It sends only the immutable invented fixture below.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND.parent
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT.parent / "private-data" / "import-jobs" / "authoring-smoke"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
# Importing schemas or the runner must not auto-load the application's .env.
# The explicit selected-provider loader below does not populate os.environ.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from src.fusion.authoring_model import (  # noqa: E402
    AUDIT_CATEGORIES, AuthoringModel, AuthoringModelError, parse_compiler_output, validate_model_audit,
)
from src.fusion.authoring_sources import prepare_authoring_sources  # noqa: E402
from src.fusion.package_validation import canonical_json, content_hash  # noqa: E402
from src.fusion.provider_smoke import SelectedSmokeConfig, SmokeConfigurationError, load_selected_config  # noqa: E402
from src.fusion.source_bundles import SourceBundleStore  # noqa: E402
from src.schemas.authoring import AuthoringRequest  # noqa: E402


FIXTURE_ID = "synthetic-authoring-smoke-v1"
FIXTURE_TITLE = "虚构展馆维修记录核对"
MAX_TOTAL_COST_CNY = Decimal("0.10")
QUALITY_CHECKS_VERSION = "authoring-smoke-quality/1.1"
FIXTURE_TEXT = """# 合成资料范围
这是一份独立创作的接口测试材料，不来自任何商业剧本。下列内容是本测试的完整来源，没有图片、OCR、缺页或编辑补充。
# 开场介绍
展馆的挂钟停止走动，两位值班员一起核对维修记录。
# 角色甲
值班员甲
甲是可玩的值班员，只知道本人的开场事实和公开资料，不知道乙的私密事实或系统真相。
甲的开场事实是本人亲见的事实，入场阶段仅甲可知，允许甲自行分享，没有必须公开或额外解锁条件。
我在十九点看到维修员走进展馆。
# 角色乙
值班员乙
乙是可玩的值班员，只知道本人的开场事实和公开资料，不知道甲的私密事实或系统真相。
乙的开场事实是本人亲见的事实，入场阶段仅乙可知，允许乙自行分享，没有必须公开或额外解锁条件。
我在十九点十分看到维修员离开展馆。
# 入场阶段
入场
这是初始阶段，下一阶段为复盘。双方阅读本人事实与公开维修卡，可以交流核对记录；入场结束后进入复盘，不附加其他条件。
# 公开证据
公开维修卡在入场阶段向所有角色开放并允许公开分享，没有前置证据条件。
维修卡写着：十九点至十九点十分更换挂钟电池。
# 复盘阶段
复盘
这是唯一末阶段，没有下一阶段；进行结算并结束本测试。
# 系统真相
以下是主持者确认的客观真相，仅供系统结算使用，在结算前不得作为任何角色知识或公开证据。
挂钟停止走动是因为维修员正在更换电池。
# 结算说明
结算绑定复盘阶段，并引用上面的唯一系统真相。结算时使用的说明正文如下。
对照公开维修卡说明挂钟停走的原因，然后结束复盘。
# 规则范围
本测试只有两名可玩角色和上述两个阶段，没有其他角色、受害者或额外剧情。
不使用资源费用、行动次数、关键词回忆、隐藏自知、随机判定、投票、分支结局或胜负评分；知识和证据仅受上文写明的阶段与可见性约束。
测试完成条件是顺序进入复盘并按结算说明核对唯一真相；没有附加行动、次数、触发词或额外奖励。
"""
FIXTURE_HASH = sha256(FIXTURE_TEXT.encode("utf-8")).hexdigest()
EXPECTED_NAMES = frozenset({"值班员甲", "值班员乙"})
EXPECTED_INTRODUCTION = "展馆的挂钟停止走动，两位值班员一起核对维修记录。"
EXPECTED_SETTLEMENT = "对照公开维修卡说明挂钟停走的原因，然后结束复盘。"
EXPECTED_PHASE_TITLES = ("入场", "复盘")
EXPECTED_TRUTH = "挂钟停止走动是因为维修员正在更换电池。"
EXPECTED_EVIDENCE = "维修卡写着：十九点至十九点十分更换挂钟电池。"
EXPECTED_FACTS = {"值班员甲": "我在十九点看到维修员走进展馆。",
                  "值班员乙": "我在十九点十分看到维修员离开展馆。"}


class AuthoringSmokeError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _private_write(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def make_synthetic_bundle(root: Path) -> tuple[SourceBundleStore, AuthoringRequest]:
    """Create only invented fixture text inside a new owner-only directory."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
        raise AuthoringSmokeError("SMOKE_DIRECTORY_MUST_BE_PRIVATE")
    inputs = root / "synthetic-inputs"
    inputs.mkdir(mode=0o700)
    _private_write(inputs / "fixture.md", FIXTURE_TEXT)
    source_store = SourceBundleStore(root / "source-bundles")
    frozen = source_store.freeze(inputs, {"schema_version": "source-plan/1.0", "script_key": FIXTURE_ID,
                                         "edition": "synthetic-v1", "notes": ["独立合成接口验收资料；不代表商业剧本或人工批准。"],
                                         "sources": [{"relative_path": "fixture.md", "kind": "original", "material_type": "host"}]})
    request = AuthoringRequest(idempotency_key=FIXTURE_ID, bundle_hash=frozen["bundle_hash"],
                                source_ids=[frozen["sources"][0]["id"]], title=FIXTURE_TITLE,
                                content_version="synthetic-v1", player_count=2)
    return source_store, request


def validate_smoke_artifacts(context: dict, package: dict, audit: dict) -> dict:
    """Check the complete fixed fixture, after unmodified source/contract checks.

    Required facts existing somewhere is insufficient: an additional public
    copy could leak a private fact or the truth. Match each allowed collection
    exactly while leaving generated IDs, ordering, and optional defaults free.
    """
    parsed = parse_compiler_output({"status": "CANDIDATE", "package": package, "blockers": []}, context)
    package = parsed["package"]
    report = validate_model_audit(audit, package)
    if (package["schema_version"] != "script-package/1.1"
            or any(package[key] != context[key] for key in
                   ("script_key", "title", "content_version", "player_count", "sources"))):
        raise AuthoringSmokeError("SMOKE_FIXED_METADATA_BINDING_FAILED")
    if (package["script_key"] != FIXTURE_ID or package["title"] != FIXTURE_TITLE
            or package["player_count"] != 2 or len(package["characters"]) != 2
            or {item["name"] for item in package["characters"]} != EXPECTED_NAMES
            or len(package["phases"]) != 2 or not package["evidence"] or not package["truth"]):
        raise AuthoringSmokeError("SMOKE_PACKAGE_SHAPE_FAILED")
    if package["introduction"]["text"] != EXPECTED_INTRODUCTION:
        raise AuthoringSmokeError("SMOKE_INTRODUCTION_MAPPING_FAILED")
    if package["settlement"]["instructions"]["text"] != EXPECTED_SETTLEMENT:
        raise AuthoringSmokeError("SMOKE_SETTLEMENT_MAPPING_FAILED")
    phases = {item["id"]: item for item in package["phases"]}
    initial = package["initial_phase_id"]
    ending = package["settlement"]["phase_id"]
    if (initial == ending or phases[initial]["next_phase_id"] != ending or phases[ending]["next_phase_id"] is not None
            or (phases[initial]["title"], phases[ending]["title"]) != EXPECTED_PHASE_TITLES):
        raise AuthoringSmokeError("SMOKE_PHASE_ORDER_FAILED")
    names = {item["id"]: item["name"] for item in package["characters"]}
    if len(package["knowledge"]) != len(EXPECTED_FACTS):
        raise AuthoringSmokeError("SMOKE_PRIVATE_FACT_MAPPING_FAILED")
    for name, fact in EXPECTED_FACTS.items():
        if not any(item["text"] == fact and item["kind"] == "FACT" and item["visibility"] == "CHARACTER_PRIVATE"
                   and names.get(item["character_id"]) == name and item["release"]["phase_id"] == initial
                   and not item["release"].get("required_public_evidence_ids") and item["disclosure"] == "MAY_SHARE"
                   for item in package["knowledge"]):
            raise AuthoringSmokeError("SMOKE_PRIVATE_FACT_MAPPING_FAILED")
    if len(package["evidence"]) != 1 or not any(item["text"] == EXPECTED_EVIDENCE and item["visibility"] == "PUBLIC" and item["character_id"] is None
               and item["release"]["phase_id"] == initial and item["disclosure"] == "PUBLIC"
               and not item["release"].get("required_public_evidence_ids") for item in package["evidence"]):
        raise AuthoringSmokeError("SMOKE_EVIDENCE_MAPPING_FAILED")
    if (len(package["truth"]) != 1 or package["truth"][0]["text"] != EXPECTED_TRUTH
            or package["truth"][0]["visibility"] != "SYSTEM_TRUTH"
            or package["settlement"]["truth_ids"] != [package["truth"][0]["id"]]):
        raise AuthoringSmokeError("SMOKE_TRUTH_MAPPING_FAILED")
    if any(EXPECTED_TRUTH in item["text"] for collection in ("knowledge", "evidence") for item in package[collection]):
        raise AuthoringSmokeError("SMOKE_TRUTH_LEAKED")
    if set(report["coverage"]) != AUDIT_CATEGORIES:
        raise AuthoringSmokeError("SMOKE_AUDIT_COVERAGE_FAILED")
    return {"quality_checks_version": QUALITY_CHECKS_VERSION,
            "fixed_metadata_binding_verified": True, "candidate_package_hash": content_hash(package),
            "characters": 2, "phases": 2, "evidence_count": len(package["evidence"]),
            "truth_count": len(package["truth"]), "audit_coverage": report["coverage"],
            "audit_findings": len(report["findings"]),
            "audit_blockers": sum(item["severity"] == "BLOCKER" for item in report["findings"]),
            "publication_ready": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合成资料 Compiler→核验→Audit 单次验收；最多两次付费调用，总上限¥0.10。")
    parser.add_argument("--allow-paid", required=True, action="store_true",
                        help="显式允许本次固定合成资料测试可能扣费；没有任何自动重试。")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT,
                        help="私有测试产物父目录；拒绝 Git 仓库内部，每次创建独立目录。")
    return parser


class SmokeAuthoringModel(AuthoringModel):
    """An additional harness limit, independent of the persistent job budget."""
    def __init__(self, config: SelectedSmokeConfig) -> None:
        super().__init__(config)
        self.calls_started: list[str] = []
        self.call_receipts: list[dict] = []
        self.total_reserved_cny = Decimal("0")
        self.compile_package_hash: str | None = None

    async def call(self, step: str, context: dict, package: dict | None = None, *, prepared: Any = None) -> dict:
        expected = ("COMPILE", "AUDIT")
        if len(self.calls_started) >= 2 or step != expected[len(self.calls_started)]:
            raise AuthoringModelError("SMOKE_CALL_SEQUENCE_REFUSED")
        if step == "AUDIT" and (self.compile_package_hash is None or content_hash(package) != self.compile_package_hash):
            raise AuthoringModelError("SMOKE_AUDIT_PACKAGE_REFUSED")
        fresh = self.prepare(step, context, package)
        if prepared is not None and prepared != fresh:
            raise AuthoringModelError("AUTHORING_PREPARATION_CHANGED")
        if self.total_reserved_cny + fresh.reservation.cost_cny > MAX_TOTAL_COST_CNY:
            raise AuthoringModelError("SMOKE_TOTAL_RESERVATION_EXCEEDED")
        self.calls_started.append(step)
        self.total_reserved_cny += fresh.reservation.cost_cny
        try:
            result = await super().call(step, context, package, prepared=fresh)
        except AuthoringModelError as error:
            if error.receipt:
                self.call_receipts.append(error.receipt)
            raise
        except BaseException:
            # Interruption can happen after dispatch but before an SDK receipt;
            # it is still potentially billed and must retain the reservation.
            self.call_receipts.append({"step": step, "provider": self.config.profile.name, "model": self.config.model,
                                       "prompt_hash": fresh.prompt_hash, "contract_hash": fresh.contract_hash,
                                       "reservation": fresh.reservation.to_metadata(), "usage_known": False,
                                       "usage": None, "charged_cost_cny": str(fresh.reservation.cost_cny),
                                       "result_code": "SMOKE_INTERRUPTED_USAGE_UNKNOWN"})
            raise
        self.call_receipts.append(result["receipt"])
        if step == "COMPILE" and result["output"]["status"] == "CANDIDATE":
            self.compile_package_hash = content_hash(result["output"]["package"])
        return result


def sanitized_receipt(model: SmokeAuthoringModel, *, result_code: str, quality: dict | None = None) -> dict:
    """Do not serialize the job, raw model output, or arbitrary exception text."""
    charged = sum((Decimal(item["charged_cost_cny"]) for item in model.call_receipts), Decimal("0"))
    return {"schema_version": "real-authoring-smoke/1.0", "created_at": datetime.now(timezone.utc).isoformat(),
            "quality_checks_version": QUALITY_CHECKS_VERSION,
            "fixture_id": FIXTURE_ID, "fixture_hash": FIXTURE_HASH, "data_class": "synthetic_noncommercial",
            "provider": model.config.profile.name, "model": model.config.model, "result_code": result_code,
            "model_contract": model.snapshot()["schema_version"],
            "request_contract": model.snapshot()["request_contract"],
            "calls_started": list(model.calls_started), "max_calls": 2, "max_total_cost_cny": str(MAX_TOTAL_COST_CNY),
            "total_reserved_cny": str(model.total_reserved_cny), "charged_cost_cny": str(charged),
            "usage_known": len(model.call_receipts) == len(model.calls_started)
            and all(item["usage_known"] for item in model.call_receipts),
            "calls": list(model.call_receipts), "quality": quality, "publication_ready": False}


def create_output_directory(base: Path) -> Path:
    base = Path(base).absolute()
    if base.is_symlink() or base.resolve().is_relative_to(REPOSITORY_ROOT.resolve()):
        raise AuthoringSmokeError("SMOKE_OUTPUT_MUST_BE_OUTSIDE_REPOSITORY")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not base.is_dir() or base.stat().st_mode & 0o077:
        raise AuthoringSmokeError("SMOKE_DIRECTORY_MUST_BE_PRIVATE")
    prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


async def execute_smoke(model: SmokeAuthoringModel, root: Path) -> tuple[int, dict]:
    """Run the real persistent chain against an isolated synthetic SQLite DB."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.engine import URL
    from sqlalchemy.orm import sessionmaker

    from src.db.base import SQLAlchemyBase
    from src.db.models import ScriptImportJob, ScriptPackageVersion, User
    from src.db.models.authoring_job import AuthoringAttempt, AuthoringJob
    from src.fusion.authoring_jobs import AuthoringJobStore
    from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
    from src.fusion.package_import import PackageImportService

    source_store, request = make_synthetic_bundle(root)
    database_path = root / "synthetic-authoring.sqlite3"
    _private_write(database_path, "")
    engine = create_engine(URL.create("sqlite", database=str(database_path)), connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def sqlite_constraints(connection: Any, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        SQLAlchemyBase.metadata.create_all(engine, tables=[item.__table__ for item in
                                                          (User, ScriptPackageVersion, ScriptImportJob, AuthoringJob, AuthoringAttempt)])
        with factory.begin() as session:
            actor = User(username=FIXTURE_ID, email=FIXTURE_ID + "@example.invalid", hashed_password="LOGIN_DISABLED_SYNTHETIC_FIXTURE",
                         is_active=True, is_admin=True, is_verified=False)
            session.add(actor)
            session.flush()
            actor_id = actor.id
        jobs = AuthoringJobStore(factory)
        submitted = await submit_authoring_job(jobs, source_store, model, request, actor_id)
        result = await AuthoringRunner(jobs, source_store, model).run(submitted["id"], allow_paid=True)
        # A new store and fresh sessions must read the persisted result exactly.
        restored = AuthoringJobStore(factory).get(submitted["id"])
        if restored != result:
            raise AuthoringSmokeError("SMOKE_JOB_RELOAD_FAILED")
        code = result["error_code"] or "SMOKE_WORKFLOW_INCOMPLETE"
        quality = None
        if result["state"] == "COMPLETED":
            attempts = {item["step"]: item for item in result["attempts"]}
            if (set(attempts) != {"COMPILE", "AUDIT"} or any(item["status"] != "SUCCEEDED" for item in attempts.values())
                    or model.calls_started != ["COMPILE", "AUDIT"] or len(model.call_receipts) != 2
                    or not all(item["usage_known"] for item in model.call_receipts)):
                raise AuthoringSmokeError("SMOKE_ATTEMPT_LEDGER_FAILED")
            context = prepare_authoring_sources(source_store, request)
            package = attempts["COMPILE"]["output"]["package"]
            with factory() as session:
                candidate = PackageImportService(session).get_version(result["candidate_version_id"])
            if candidate["package"] != package:
                raise AuthoringSmokeError("SMOKE_CANDIDATE_RELOAD_FAILED")
            verification = source_store.get_report(result["source_report_hash"])
            if (not verification["valid"] or verification["issues"] or verification["issues_truncated"]
                    or verification["package_hash"] != content_hash(package) or verification["bundle_hash"] != request.bundle_hash):
                raise AuthoringSmokeError("SMOKE_SOURCE_VERIFICATION_FAILED")
            try:
                quality = validate_smoke_artifacts(context, package, attempts["AUDIT"]["output"])
                code = "SMOKE_AUDIT_BLOCKERS_FOUND" if quality["audit_blockers"] else "PASSED"
            except AuthoringSmokeError as error:
                # A complete engineering workflow can still fail semantic
                # acceptance. Keep its job/receipt links without claiming pass.
                code = error.code
        receipt = sanitized_receipt(model, result_code=code, quality=quality)
        receipt.update(job_id=result["id"], job_state=result["state"], candidate_version_id=result["candidate_version_id"],
                       source_report_hash=result["source_report_hash"], persisted_job_cost_cny=result["charged_cost_cny"],
                       job_reload_verified=True)
        if Decimal(receipt["charged_cost_cny"]) > MAX_TOTAL_COST_CNY:
            receipt["result_code"] = "SMOKE_TOTAL_COST_EXCEEDED"
        if Decimal(receipt["charged_cost_cny"]) != Decimal(receipt["persisted_job_cost_cny"]):
            receipt["result_code"] = "SMOKE_COST_LEDGER_MISMATCH"
        return (0 if receipt["result_code"] == "PASSED" else 3), receipt
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = None
    model = None
    try:
        root = create_output_directory(args.output_root)
        # This is the only credential read; never construct the application's DB
        # or settings services, and never load any other model provider.
        config = load_selected_config("aliyun_bailian")
        model = SmokeAuthoringModel(config)
        code, receipt = asyncio.run(execute_smoke(model, root))
    except (AuthoringSmokeError, AuthoringModelError, SmokeConfigurationError) as error:
        result_code = error.code
        code = 3
        receipt = (sanitized_receipt(model, result_code=result_code) if model is not None else
                   {"schema_version": "real-authoring-smoke/1.0", "fixture_id": FIXTURE_ID,
                    "quality_checks_version": QUALITY_CHECKS_VERSION,
                    "result_code": result_code, "publication_ready": False})
    except (Exception, KeyboardInterrupt) as error:
        code = 3
        result_code = getattr(error, "code", None)
        if not isinstance(result_code, str) or re.fullmatch(r"(?:AUTHORING|SMOKE|SOURCE)_[A-Z0-9_]{1,80}", result_code) is None:
            result_code = "SMOKE_WORKFLOW_FAILED"
        receipt = (sanitized_receipt(model, result_code=result_code) if model is not None else
                   {"schema_version": "real-authoring-smoke/1.0", "fixture_id": FIXTURE_ID,
                    "quality_checks_version": QUALITY_CHECKS_VERSION,
                    "result_code": result_code, "publication_ready": False})
    if root is not None:
        try:
            _private_write(root / "receipt.json", canonical_json(receipt))
            receipt["receipt_path"] = str(root / "receipt.json")
        except OSError:
            code = 3
            receipt["receipt_write_failed"] = True
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
