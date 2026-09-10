"""CLI wrapper for the synthetic-only, one-shot provider smoke test.

Run from the repository root.  Omitting any required confirmation argument
causes argparse to exit before the .env file is read or a client is created.
"""
from __future__ import annotations

from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from src.fusion.provider_smoke import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
