"""Opt-in acceptance of the current investigation proposal model protocol."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    from src.fusion.investigation_authoring_smoke import read_paid_authorization
    from src.fusion.proposal_probe import run_probe
    from src.fusion.provider_smoke import load_selected_config
    parser = argparse.ArgumentParser(description="默认零调用预览；执行固定八次虚构调查建议，不自动重试。")
    parser.add_argument("--provider", choices=["volcengine_ark", "aliyun_bailian"], default="volcengine_ark")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-hash")
    args = parser.parse_args(argv)
    try:
        authorized = read_paid_authorization() if args.execute else False
        if args.execute and (not args.expected_hash or not authorized):
            raise ValueError("PAID_GATE_CLOSED")
        root = Path(__file__).resolve().parents[2].parent / "private-data/import-jobs/proposal-probe"
        result = asyncio.run(run_probe(load_selected_config(args.provider), root, execute=args.execute,
            expected_hash=args.expected_hash, paid_authorized=authorized))
        print(json.dumps({key: result[key] for key in ("status", "suite_hash", "directory", "model",
            "model_requests", "accounted_cost_cny", "semantic_status")}, ensure_ascii=False))
        return 0 if result["status"] in ("PREVIEW", "COLLECTED") else 2
    except Exception:
        print("调查建议验收未完成；请检查配置及私有记录。不会自动重发。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
