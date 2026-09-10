"""CLI wrapper for the synthetic PostgreSQL + one-paid-call Fusion check."""
from __future__ import annotations

from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.dont_write_bytecode = True

from src.fusion.live_vertical_smoke import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
