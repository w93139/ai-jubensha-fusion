"""Synthetic, offline tests: do not inspect the user's private materials."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import dev_preflight as doctor


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def make_ready_layout(self):
        python = self.root / "backend/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("synthetic executable; never launched", encoding="utf-8")
        python.chmod(0o700)
        for name in ("next", "react", "react-dom", "typescript"):
            target = self.root / "frontend/node_modules" / name / "package.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
        secret = self.root / ".env"
        secret.write_text("SYNTHETIC_SECRET=must-not-be-read", encoding="utf-8")
        secret.chmod(0o600)
        (self.root.parent / "private-data").mkdir(mode=0o700)

    def fake_runner(self, args, cwd):
        if "-I" in args:
            names = ("pytest", "uvicorn", "fastapi", "sqlalchemy", "pydantic", "jsonschema", "python-dotenv", "openai", "redis", "debugpy", "pillow")
            return doctor.CommandResult(0, json.dumps({"python": [3, 13, 15], "packages": dict.fromkeys(names, "1.2.3")}))
        if args[0] == "/fake/node":
            return doctor.CommandResult(0, "v24.19.0\n")
        if args[0] == "/fake/uv":
            return doctor.CommandResult(0, "uv 0.12.5 (synthetic)")
        if "rev-parse" in args:
            return doctor.CommandResult(0, "true\n")
        return doctor.CommandResult(0)

    def test_exit_codes_distinguish_blocker_and_warning(self):
        self.assertEqual(doctor.exit_code([]), 0)
        self.assertEqual(doctor.exit_code([doctor.Finding("WARN", "x", "x")]), 2)
        self.assertEqual(doctor.exit_code([doctor.Finding("WARN", "x", "x"), doctor.Finding("BLOCK", "y", "y")]), 1)

    def test_ready_synthetic_layout_never_reads_env(self):
        self.make_ready_layout()
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name == ".env":
                raise AssertionError("must not read .env")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            findings = doctor.collect(self.root, self.fake_runner, lambda name: "/fake/" + name)
        self.assertEqual(doctor.exit_code(findings), 0)
        self.assertNotIn("SYNTHETIC_SECRET", repr(findings))

    def test_missing_python_is_coding_blocker(self):
        findings = doctor.collect(self.root, self.fake_runner, lambda name: None)
        self.assertEqual(doctor.exit_code(findings), 1)
        self.assertTrue(any(item.check == "Python" and item.level == "BLOCK" for item in findings))

    def test_old_absolute_shebang_warns_without_running_it(self):
        script = self.root / "pytest"
        script.write_text("#!/old/location/python\nraise RuntimeError('must not execute')\n", encoding="utf-8")
        findings = doctor.check_shebang(script, self.root / "backend/.venv/bin/python")
        self.assertEqual(findings[0].level, "WARN")

    def test_python3_shebang_in_same_environment_is_accepted(self):
        environment = self.root / "backend/.venv/bin"
        environment.mkdir(parents=True)
        python = environment / "python"
        python.write_text("synthetic", encoding="utf-8")
        python3 = environment / "python3"
        python3.symlink_to(python.name)
        script = environment / "pytest"
        script.write_text(f"#!{python3}\n", encoding="utf-8")
        findings = doctor.check_shebang(script, python)
        self.assertEqual(findings[0].level, "OK")

    def test_private_metadata_permissions_and_symlink(self):
        secret = self.root / ".env"
        secret.write_text("synthetic", encoding="utf-8")
        secret.chmod(0o644)
        self.assertEqual(doctor.check_private_metadata(secret, "env")[0].level, "WARN")
        link = self.root / "link"
        link.symlink_to(secret)
        self.assertIn("符号链接", doctor.check_private_metadata(link, "link")[0].message)

    def test_missing_debugpy_docker_code_are_warnings_not_blockers(self):
        self.make_ready_layout()

        def runner(args, cwd):
            result = self.fake_runner(args, cwd)
            if "-I" in args:
                data = json.loads(result.stdout)
                data["packages"]["debugpy"] = None
                return doctor.CommandResult(0, json.dumps(data))
            return result

        findings = doctor.collect(self.root, runner, lambda name: None if name in ("docker", "code") else "/fake/" + name)
        self.assertEqual(doctor.exit_code(findings), 2)
        self.assertFalse(any(item.level == "BLOCK" for item in findings))

    def test_tracked_env_is_blocker_and_never_printed(self):
        self.make_ready_layout()

        def runner(args, cwd):
            return doctor.CommandResult(0, ".env\0") if "ls-files" in args else self.fake_runner(args, cwd)

        findings = doctor.collect(self.root, runner, lambda name: "/fake/" + name)
        self.assertEqual(doctor.exit_code(findings), 1)
        self.assertTrue(any(item.check == "Git 私密文件" and item.level == "BLOCK" for item in findings))

    def test_malformed_python_probe_is_a_blocker(self):
        self.make_ready_layout()
        findings = doctor.collect(self.root, lambda args, cwd: doctor.CommandResult(0, "not-json"), lambda name: "/fake/" + name)
        self.assertTrue(any(item.check == "Python" and item.level == "BLOCK" for item in findings))

    def test_command_timeout_is_bounded_and_does_not_expose_stderr(self):
        with patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired(["synthetic"], 5)) as run:
            result = doctor.run_command(["synthetic"], self.root)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "")
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_unsupported_node_version_is_a_blocker(self):
        self.make_ready_layout()

        def runner(args, cwd):
            return doctor.CommandResult(0, "v18.20.0") if args[0] == "/fake/node" else self.fake_runner(args, cwd)

        findings = doctor.collect(self.root, runner, lambda name: "/fake/" + name)
        self.assertTrue(any(item.check == "Node" and item.level == "BLOCK" for item in findings))


class TaskConfigurationTests(unittest.TestCase):
    """Read only the new, non-secret task configuration; never launch tasks."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.configuration = json.loads((root / ".vscode/tasks.json").read_text(encoding="utf-8"))
        cls.tasks = {item["label"]: item for item in cls.configuration["tasks"]}

    def test_commands_are_process_tasks_with_explicit_cwd_and_no_autostart(self):
        for item in self.tasks.values():
            self.assertNotEqual(item.get("runOptions", {}).get("runOn"), "folderOpen")
            if "command" in item:
                self.assertEqual(item["type"], "process")
                self.assertTrue(item["options"]["cwd"].startswith("${workspaceFolder}"))

    def test_only_offline_suite_is_default(self):
        defaults = [item for item in self.tasks.values() if isinstance(item.get("group"), dict) and item["group"].get("isDefault")]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["group"]["kind"], "test")
        self.assertFalse(any("启动" in label for label in defaults[0]["dependsOn"]))

    def test_manual_start_is_confirmed_localhost_and_does_not_use_old_shebangs(self):
        starts = [item for label, item in self.tasks.items() if label.startswith("手动启动：")]
        self.assertEqual(len(starts), 2)
        for item in starts:
            dependencies = item["dependsOn"] if isinstance(item["dependsOn"], list) else [item["dependsOn"]]
            self.assertIn("确认：手动启动服务（输入 START）", dependencies)
            self.assertIn("127.0.0.1", item["args"])
        backend = next(item for item in starts if "后端" in item["label"])
        self.assertEqual(backend["args"][:3], ["-m", "uvicorn", "main:app"])
        self.assertEqual(backend["options"]["env"]["ENABLE_MEDIA_FEATURES"], "false")
        self.assertEqual(backend["options"]["env"]["DB_PORT"], "55432")
        self.assertIn("环境：准备本地依赖并迁移", backend["dependsOn"])
        self.assertEqual(backend["options"]["env"]["REDIS_URL"], "redis://127.0.0.1:56379/0")
        environment = self.tasks["环境：准备本地依赖并迁移"]
        self.assertEqual(environment["dependsOrder"], "sequence")
        self.assertEqual(environment["dependsOn"][-1], "数据库：升级结构到最新版")

    def test_local_service_urls_are_consistent(self):
        backend = self.tasks["手动启动：后端 127.0.0.1:8010（有副作用）"]
        frontend = self.tasks["手动启动：前端 127.0.0.1:3001（有副作用）"]
        self.assertEqual(frontend["options"]["env"]["NEXT_PUBLIC_API_URL"], "http://127.0.0.1:8010")
        self.assertIn("http://127.0.0.1:3001", backend["options"]["env"]["CORS_ORIGINS"])

    def test_confirmation_rejects_empty_or_wrong_input(self):
        script = self.tasks["确认：手动启动服务（输入 START）"]["args"][-1]
        for answer, expected in (("", 1), ("start", 1), ("START", 0)):
            with self.subTest(answer=answer), patch("builtins.input", return_value=answer):
                with self.assertRaises(SystemExit) as raised:
                    exec(compile(script, "<synthetic-task-confirmation>", "exec"), {})
                self.assertEqual(raised.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
