"""Explicit synthetic-only v1.2 authoring smoke; default preview never calls SDK."""
import os
from pathlib import Path
import sys

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.dont_write_bytecode = True
backend = str(Path(__file__).resolve().parents[1])
if backend not in sys.path:
    sys.path.insert(0, backend)

from src.fusion.investigation_authoring_smoke import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
