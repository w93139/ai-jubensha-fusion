"""Explicit bounded synthetic role behavior probe; never loads real scripts."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from src.fusion.role_decision_probe import run_probe
    from src.fusion.provider_smoke import load_selected_config
    from src.fusion.investigation_authoring_smoke import read_paid_authorization
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['volcengine_ark','aliyun_bailian'],default='volcengine_ark')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--expected-hash')
    args=parser.parse_args()
    try:
        if args.execute and (not args.expected_hash or not read_paid_authorization()): raise ValueError('PAID_GATE_CLOSED')
        root=Path(__file__).resolve().parents[2].parent/'private-data/import-jobs/role-decision-probe'
        result=asyncio.run(run_probe(load_selected_config(args.provider),root,execute=args.execute,expected_hash=args.expected_hash))
        print(json.dumps({k:result[k] for k in ('status','suite_hash','directory','model','model_requests','accounted_cost_cny','semantic_status')},ensure_ascii=False))
        return 0 if result['status'] in ('PREVIEW','COLLECTED') else 2
    except Exception:
        print('角色评估未完成；请检查私有记录。不会自动重发。',file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
