"""Isolated legacy TTS switch tests; no project imports, real env, or network.

The actual initializer is extracted from its AST and run with synthetic getenv
and a mock TTS factory. This covers the initialization gate and the existing
enabled branch, not WebSocket lifecycle, synthesis, or other media call sites.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[2] / "src/core/websocket_server.py"


class LegacyTTSSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        session_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "GameSession"
        )
        initializer = next(
            node for node in session_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_initialize_tts_manager"
        )
        cls.initializer_code = compile(
            ast.Module(body=[initializer], type_ignores=[]), str(SOURCE), "exec"
        )

    def run_initializer(self, values: dict[str, str]) -> tuple:
        factory = Mock(name="TTS factory")
        getenv = Mock(side_effect=lambda name, default=None: values.get(name, default))
        namespace = {
            "os": SimpleNamespace(getenv=getenv),
            "logger": Mock(),
            "GameTTSManager": factory,
        }
        exec(self.initializer_code, namespace)
        session = SimpleNamespace(session_id="test-session", tts_manager=None)
        namespace["_initialize_tts_manager"](session)
        return session, factory, getenv

    def test_disabled_switch_never_initializes_even_with_credentials(self) -> None:
        session, factory, getenv = self.run_initializer({
            "ENABLE_MEDIA_FEATURES": "false",
            "TTS_API_KEY": "synthetic-key",
            "MINIMAX_GROUP_ID": "synthetic-group",
        })
        self.assertIsNone(session.tts_manager)
        factory.assert_not_called()
        getenv.assert_called_once_with("ENABLE_MEDIA_FEATURES", "false")

    def test_missing_switch_defaults_to_disabled(self) -> None:
        session, factory, _ = self.run_initializer({
            "TTS_API_KEY": "synthetic-key", "MINIMAX_GROUP_ID": "synthetic-group",
        })
        self.assertIsNone(session.tts_manager)
        factory.assert_not_called()

    def test_only_true_enables_media(self) -> None:
        for value in ("", "0", "yes", "FaLsE"):
            with self.subTest(value=value):
                _, factory, getenv = self.run_initializer({
                    "ENABLE_MEDIA_FEATURES": value,
                    "TTS_API_KEY": "synthetic-key",
                    "MINIMAX_GROUP_ID": "synthetic-group",
                })
                factory.assert_not_called()
                getenv.assert_called_once_with("ENABLE_MEDIA_FEATURES", "false")

    def test_enabled_preserves_existing_provider_and_parameters(self) -> None:
        session, factory, _ = self.run_initializer({
            "ENABLE_MEDIA_FEATURES": "true",
            "TTS_API_KEY": "synthetic-key",
            "MINIMAX_GROUP_ID": "synthetic-group",
            "TTS_PROVIDER": "existing-provider",
        })
        factory.assert_called_once_with(
            api_key="synthetic-key", group_id="synthetic-group",
            model="speech-02-turbo", provider="existing-provider",
        )
        self.assertIs(session.tts_manager, factory.return_value)

    def test_true_is_case_insensitive_and_keeps_default_provider(self) -> None:
        _, factory, _ = self.run_initializer({
            "ENABLE_MEDIA_FEATURES": "TrUe",
            "TTS_API_KEY": "synthetic-key",
            "MINIMAX_GROUP_ID": "synthetic-group",
        })
        self.assertEqual(factory.call_args.kwargs["provider"], "minimax")

    def test_enabled_still_requires_existing_credentials(self) -> None:
        for credentials in ({}, {"TTS_API_KEY": "synthetic-key"}, {"MINIMAX_GROUP_ID": "synthetic-group"}):
            with self.subTest(credentials=sorted(credentials)):
                session, factory, _ = self.run_initializer({
                    "ENABLE_MEDIA_FEATURES": "true", **credentials,
                })
                self.assertIsNone(session.tts_manager)
                factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
