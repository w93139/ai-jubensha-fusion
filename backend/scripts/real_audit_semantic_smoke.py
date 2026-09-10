"""Synthetic Audit-only evaluation; no app initialization or automatic retries."""
import os
from pathlib import Path
import sys

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fusion.audit_semantic_smoke import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
