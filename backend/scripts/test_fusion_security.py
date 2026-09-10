"""Run the focused safety suite without .env, a live database, or network access.

Usage from the repository root:
    backend/.venv/bin/python backend/scripts/test_fusion_security.py
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.dont_write_bytecode = True
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for name in (
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "TTS_API_KEY", "MINIMAX_GROUP_ID", "REDIS_URL",
    "ARK_API_KEY", "ARK_BASE_URL", "ARK_CHARACTER_MODEL",
    "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_QWEN_MODEL",
    "ENABLE_PAID_MODEL_CALLS", "GAME_TOKEN_BUDGET", "GAME_COST_BUDGET_CNY",
    "DEEPSEEK_INPUT_COST_PER_MILLION", "DEEPSEEK_CACHED_INPUT_COST_PER_MILLION",
    "DEEPSEEK_OUTPUT_COST_PER_MILLION", "DEEPSEEK_PRICING_VERSION",
    "ARK_CHARACTER_INPUT_COST_PER_MILLION", "ARK_CHARACTER_CACHED_INPUT_COST_PER_MILLION",
    "ARK_CHARACTER_OUTPUT_COST_PER_MILLION", "ARK_CHARACTER_PRICING_VERSION",
    "DASHSCOPE_QWEN_INPUT_COST_PER_MILLION", "DASHSCOPE_QWEN_CACHED_INPUT_COST_PER_MILLION",
    "DASHSCOPE_QWEN_OUTPUT_COST_PER_MILLION", "DASHSCOPE_QWEN_PRICING_VERSION",
    "FUSION_PLAYER_PROVIDER", "LLM_PROVIDER", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "LLM_THINKING_MODE",
    "LLM_REASONING_EFFORT", "LLM_TEMPERATURE", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES",
    "LLM_MAX_TOKENS", "FUSION_PLAYER_MAX_INPUT_BYTES",
    "ENABLE_DOUBAO_ASR", "DOUBAO_ASR_API_KEY", "DOUBAO_ASR_APP_ID", "DOUBAO_ASR_ACCESS_TOKEN",
    "DOUBAO_ASR_STATE_DIR", "DOUBAO_ASR_DAILY_SECONDS", "DOUBAO_ASR_PLAY_SECONDS", "DOUBAO_ASR_TIMEOUT_SECONDS",
    "SPEECH_INPUT_PROVIDER", "ENABLE_QWEN_ASR", "ENABLE_QWEN_REALTIME_ASR", "QWEN_ASR_API_KEY", "SPEECH_INPUT_STATE_DIR",
    "SPEECH_INPUT_DAILY_SECONDS", "SPEECH_INPUT_PLAY_SECONDS", "SPEECH_INPUT_TIMEOUT_SECONDS",
):
    os.environ.pop(name, None)


def no_network(*args, **kwargs):
    raise AssertionError("The offline Fusion suite must not open network connections")


def main() -> int:
    import pytest

    suite = BACKEND / "tests" / "fusion_security"
    with (
        TemporaryDirectory(prefix='fusion-asr-offline-') as asr_state,
        patch.dict(os.environ, {'DOUBAO_ASR_STATE_DIR': str(Path(asr_state).resolve() / 'speech-input')}),
        patch("dotenv.load_dotenv", return_value=False),
        patch("socket.socket.connect", no_network),
        patch("socket.socket.connect_ex", no_network),
        patch("socket.create_connection", no_network),
    ):
        return pytest.main([
            str(suite), str(BACKEND / "tests" / "test_fusion_service.py"),
            str(BACKEND / "tests" / "test_fusion_rules.py"),
            str(BACKEND / "tests" / "media_safety"),
            f"--confcutdir={suite}", "-p", "no:cacheprovider", "-q", *sys.argv[1:],
        ])


if __name__ == "__main__":
    raise SystemExit(main())
