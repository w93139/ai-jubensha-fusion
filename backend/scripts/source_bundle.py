"""Local, non-billable CLI for explicitly selected private source material.

No dotenv, application/database startup, source scripts, or model SDKs.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.dont_write_bytecode = True

from src.fusion.package_validation import parse_package_json
from src.fusion.source_bundles import SourceBundleError, SourceBundleStore


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结或核验本机私有剧本来源；不调用模型")
    parser.add_argument("--store", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--input-root", type=Path, required=True)
    freeze.add_argument("--plan", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True)
    args = parser.parse_args()
    try:
        store = SourceBundleStore(args.store)
        if args.command == "freeze":
            plan = parse_package_json(args.plan.read_bytes())
            result = store.freeze(args.input_root, plan)
            print(f"来源快照已保存；文件数 {result['file_count']}；字节数 {result['total_bytes']}")
            print(f"bundle_hash={result['bundle_hash']}")
        else:
            result = store.verify(args.bundle)
            print(f"核验 {'通过' if result['valid'] else '阻断'}；已检查 {result['checked_files']}；问题数 {len(result['issues'])}")
            print(f"report_hash={result['report_hash']}")
            if not result["valid"]:
                return 1
        print("只完成来源文件处理；未批准发布、未导入运行时、未验证 OCR 正确性。")
        return 0
    except (SourceBundleError, ValueError, OSError):
        print("来源操作失败；检查选取计划、文件类型、权限及快照完整性。未输出正文或系统路径。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
