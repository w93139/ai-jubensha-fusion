"""Process one explicitly selected durable job, without migrating or seeding."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser(description="执行一个编译/审核任务；最多两次受控模型调用，无自动重试")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--allow-paid", action="store_true", required=True)
    parser.add_argument("--package-contract", choices=("script-package/1.1", "script-package/1.2"),
                        default="script-package/1.1", help="必须与排队任务的候选契约一致；旧任务不会自动升级")
    parser.add_argument("--frozen-rule-plan", action="store_true", help="仅用于携带 rule_plan 的新任务；不升级旧任务")
    parser.add_argument("--confirm-frozen-text", action="store_true", help="保留草案正文，AI 只检查；用于 1.4/1.5/1.6，必须匹配任务标记")
    parser.add_argument("--indexed-audit", action="store_true", help="来源编号审核：1.5，或配合 --bounded-audit 的 1.6")
    parser.add_argument("--bounded-audit", action="store_true", help="显式选择 1.6 简短审核，必须配合 --indexed-audit")
    parser.add_argument("--direct-audit-schema", action="store_true", help="显式选择 1.7 顶层审核格式，配合 --bounded-audit")
    parser.add_argument("--strict-audit-schema", action="store_true", help="显式选择 1.8 服务商格式约束，配合 --direct-audit-schema")
    parser.add_argument("--portable-audit-patterns", action="store_true", help="显式选择 1.9 字符串约束兼容，配合 --strict-audit-schema")
    parser.add_argument("--typed-audit-schema", action="store_true", help="显式选择 1.10；服务商不使用正则，本地仍完整校验")
    parser.add_argument("--runtime-audit-context", action="store_true", help="显式选择 1.11，向审核提供程序规则；配合 --typed-audit-schema")
    parser.add_argument("--citation-audit", action="store_true", help="显式选择 1.12 引用目录审核；配合 --runtime-audit-context")
    args = parser.parse_args()
    if args.citation_audit and not args.runtime_audit_context:
        parser.error("引用目录审核必须选择 --runtime-audit-context")
    if args.runtime_audit_context and not args.typed_audit_schema:
        parser.error("运行规则审核必须选择 --typed-audit-schema")
    if args.typed_audit_schema and not args.portable_audit_patterns:
        parser.error("类型审核格式必须选择 --portable-audit-patterns")
    if args.portable_audit_patterns and not args.strict_audit_schema:
        parser.error("字符串约束兼容必须选择 --strict-audit-schema")
    if args.strict_audit_schema and not args.direct_audit_schema:
        parser.error("严格审核格式必须选择 --direct-audit-schema")
    if args.direct_audit_schema and not args.bounded_audit:
        parser.error("顶层审核格式必须选择 --bounded-audit")
    if args.bounded_audit and not args.indexed_audit:
        parser.error("简短审核必须显式选择 --indexed-audit")
    if args.frozen_rule_plan and args.package_contract != "script-package/1.2":
        parser.error("冻结规则草案必须显式选择 script-package/1.2")
    if args.confirm_frozen_text and not args.frozen_rule_plan:
        parser.error("固定正文检查必须显式选择 --frozen-rule-plan")
    if args.indexed_audit and not args.confirm_frozen_text:
        parser.error("来源编号审核必须显式选择 --confirm-frozen-text")
    if args.job_id <= 0:
        parser.error("任务编号必须大于零")
    from src.core.environment import load_project_environment
    from src.db.session import db_manager
    from src.fusion.authoring_jobs import AuthoringJobStore
    from src.fusion.authoring_model import AuthoringModel
    from src.fusion.authoring_runner import AuthoringRunner
    from src.fusion.provider_smoke import load_selected_config
    from src.fusion.source_bundles import SourceBundleStore

    try:
        load_project_environment()
        model = AuthoringModel(load_selected_config("aliyun_bailian"), package_contract=args.package_contract,
                               rule_plan_enabled=args.frozen_rule_plan, confirm_frozen_text=args.confirm_frozen_text,
                               indexed_audit=args.indexed_audit, bounded_audit=args.bounded_audit, direct_audit_schema=args.direct_audit_schema, strict_audit_schema=args.strict_audit_schema, portable_audit_patterns=args.portable_audit_patterns, typed_audit_schema=args.typed_audit_schema, runtime_audit_context=args.runtime_audit_context, citation_audit=args.citation_audit)
        db_manager.initialize()  # Existing configured database; no create_all or migration.
        result = asyncio.run(AuthoringRunner(AuthoringJobStore(db_manager.get_session), SourceBundleStore(), model)
                             .run(args.job_id, allow_paid=args.allow_paid))
        print(json.dumps({key: result[key] for key in
                          ("id", "state", "step", "error_code", "charged_cost_cny", "publication_ready")}, ensure_ascii=False))
        return 0 if result["state"] == "COMPLETED" else 1
    except Exception:
        print("任务未完成；请在管理员任务页检查状态和安全收据。不会自动重发未知调用。", file=sys.stderr)
        return 2
    finally:
        db_manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
