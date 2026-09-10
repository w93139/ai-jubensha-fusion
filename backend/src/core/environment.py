"""Load the repository's private environment file deterministically."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ENV_FILE = REPOSITORY_ROOT / ".env"
_TRUTHY_VALUES = {"1", "true", "t", "yes", "y"}


def load_project_environment(env_file: Path = REPOSITORY_ENV_FILE) -> bool:
    """Load the repo-root ``.env`` without replacing process variables.

    ``PYTHON_DOTENV_DISABLED`` is checked here rather than relying only on a
    particular python-dotenv version, so offline tests can reliably opt out.
    """
    disabled = os.getenv("PYTHON_DOTENV_DISABLED", "").strip().lower()
    if disabled in _TRUTHY_VALUES:
        return False
    return load_dotenv(dotenv_path=env_file, override=False)
