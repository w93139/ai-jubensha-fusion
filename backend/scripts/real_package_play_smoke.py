"""Explicit, single-call synthetic package-play smoke entry point."""
from pathlib import Path
import os
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.dont_write_bytecode = True
# Only the selected, bounded loader in the harness may read configuration.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from src.fusion.package_play_smoke import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
