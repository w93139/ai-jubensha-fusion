"""Standard-library, synthetic filesystem/local-bare tests. No GitHub or model calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sync_backend_github as backup


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "snapshots/backend"
        self.version = "m3-20260908.1"
        self.raw_git(self.source, "init", "--initial-branch=main")
        self.raw_git(self.source, "remote", "add", "origin", "https://github.com/fictional/original-public.git")
        for name, data in {
            "backend/main.py": "# synthetic entry\n",
            "backend/pyproject.toml": '[project]\nname="synthetic"\nversion="0.0.0"\n',
            "backend/uv.lock": "version = 1\n",
            "backend/scripts/test_fusion_security.py": "# synthetic offline entry\n",
            "backend/src/example.py": "VALUE = 1\n",
            "backend/src/fusion/prompts/player.txt": "Synthetic generic prompt.\n",
            "backend/src/db/migrations/versions/example.py": "# synthetic migration\n",
            "backend/tests/test_example.py": "# synthetic test\n",
            "docs/contracts/example.schema.json": '{"type":"object"}\n',
            ".env.example": "API_KEY=\n",
            "LICENSE": "Synthetic fixture license.\n",
            backup.README_SOURCE: "# Synthetic backend instructions\n",
            backup.TOOL_FILES[0]: "# synthetic export script\n",
            backup.TOOL_FILES[1]: "# synthetic export test\n",
            ".gitignore": "*.db\n*.log\n.env\nbackend/ignored.py\n",
            "frontend/not-exported.ts": "// independent frontend\n",
        }.items():
            self.write(name, data)

    @staticmethod
    def raw_git(directory, *args):
        return subprocess.check_output([
            "git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgSign=false", "-c", "user.name=Synthetic test",
            "-c", "user.email=synthetic@example.invalid", *args,
        ], cwd=directory, stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, data):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        return path

    def source_commit(self):
        self.raw_git(self.source, "add", "--all")
        self.raw_git(self.source, "commit", "-m", "Synthetic source history")
        return self.raw_git(self.source, "rev-parse", "HEAD")

    def invoke(self, *flags, version=None, destination=None):
        output = io.StringIO()
        with redirect_stdout(output):
            status = backup.main([
                "--source-root", str(self.source), "--snapshot-dir", str(destination or self.destination),
                "--version", version or self.version, *flags,
            ])
        return status, output.getvalue()

    def sync(self, version=None):
        status, output = self.invoke("--sync", version=version)
        self.assertEqual(status, 0, output)
        return json.loads(output)

    def assert_clean(self):
        self.assertEqual(self.raw_git(self.destination, "status", "--porcelain"), "")
        backup.validate_destination(self.source, self.destination)

    def bare_transport(self):
        bare = self.root / "remote.git"
        self.raw_git(self.root, "init", "--bare", "--initial-branch=main", str(bare))
        actual_git = backup.git
        operations = []

        def routed(directory, *args, network=False):
            if args[0] in {"push", "ls-remote"}:
                operations.append(args)
                self.assertTrue(network)
                self.assertNotIn("--force", args)
                self.assertTrue(all(not argument.startswith("+") for argument in args))
                # The production URL remains fixed and checked. Only this test
                # runner routes its transport to a temporary local bare repo.
                args = tuple(str(bare) if argument == "origin" else argument for argument in args)
            return actual_git(directory, *args, network=network)

        return bare, operations, routed

    def test_default_check_has_no_writes_or_network(self):
        self.source_commit()
        before = self.raw_git(self.source, "status", "--porcelain")
        with patch.object(backup, "verify_github_private", side_effect=AssertionError("no network")):
            status, output = self.invoke()
        self.assertEqual(status, 0, output)
        summary = json.loads(output)
        self.assertEqual(summary["mode"], "CHECK_ONLY")
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.destination.parent.exists())
        self.assertEqual(before, self.raw_git(self.source, "status", "--porcelain"))
        self.assertNotIn(str(self.source), output)

    def test_explicit_exclusions_never_read_env_database_log_or_ignored_source(self):
        names = ["backend/.env", "backend/private.db", "backend/trace.log", "backend/.pytest_cache/nodeids", "backend/private-data/original.py", "backend/untrusted.bin", "backend/ignored.py"]
        for name in names:
            self.write(name, "DO_NOT_READ_OR_EXPORT")
        self.raw_git(self.source, "add", "--force", "--", *names[:-1])
        original = Path.read_bytes

        def guarded(path):
            if path in {self.source / name for name in names}:
                raise AssertionError("forbidden source read")
            return original(path)

        with patch.object(Path, "read_bytes", guarded):
            self.sync()
        self.assertTrue(all(not (self.destination / name).exists() for name in names))

    def test_snapshot_has_own_single_commit_manifest_contracts_and_source_origin_unchanged(self):
        source_head = self.source_commit()
        self.write("backend/src/example.py", "VALUE = 2\n")
        summary = self.sync()
        self.assertEqual(self.raw_git(self.destination, "rev-list", "--count", "HEAD"), "1")
        self.assertNotEqual(summary["commit"], source_head)
        self.assertEqual(self.raw_git(self.source, "rev-parse", "HEAD"), source_head)
        self.assertEqual(self.raw_git(self.source, "remote", "get-url", "origin"), "https://github.com/fictional/original-public.git")
        self.assertEqual(self.raw_git(self.destination, "remote", "get-url", "origin"), backup.REMOTE_URL)
        manifest = backup.read_manifest(self.destination)
        self.assertEqual(manifest["version"], self.version)
        self.assertNotIn(str(self.source), (self.destination / backup.MANIFEST).read_text())
        self.assertTrue((self.destination / "docs/contracts/example.schema.json").is_file())
        self.assertFalse((self.destination / "frontend").exists())
        self.assertEqual((self.destination / "README.md").read_bytes(), (self.source / backup.README_SOURCE).read_bytes())
        self.assert_clean()

    def test_identical_version_is_idempotent_even_after_unrelated_frontend_change(self):
        first = self.sync()
        tag = self.raw_git(self.destination, "rev-parse", self.version)
        self.write("frontend/not-exported.ts", "// changed independently\n")
        second = self.sync()
        self.assertEqual(first["commit"], second["commit"])
        self.assertEqual(tag, self.raw_git(self.destination, "rev-parse", self.version))
        self.assertTrue(second["same_version_and_input"])

    def test_same_version_different_source_is_rejected_without_changing_tag(self):
        first = self.sync()
        self.write("backend/src/example.py", "VALUE = 3\n")
        status, output = self.invoke("--sync")
        self.assertEqual(status, 1)
        self.assertIn("同版本", output)
        self.assertEqual(self.raw_git(self.destination, "rev-parse", "HEAD"), first["commit"])
        self.assertEqual((self.destination / "backend/src/example.py").read_text(), "VALUE = 1\n")
        self.assert_clean()

    def test_tracked_source_deletion_is_mirrored_without_git_rm(self):
        self.source_commit()
        first = self.sync()
        (self.source / "backend/src/example.py").unlink()
        self.write("backend/src/replacement.py", "VALUE = 4\n")
        second = self.sync(version="m3-20260908.2")
        self.assertFalse((self.destination / "backend/src/example.py").exists())
        self.assertTrue((self.destination / "backend/src/replacement.py").exists())
        self.assertEqual(self.raw_git(self.destination, "rev-parse", "HEAD^"), first["commit"])
        self.assertNotEqual(first["commit"], second["commit"])
        self.assert_clean()

    def test_deleted_required_dependency_and_missing_contracts_fail(self):
        self.source_commit()
        (self.source / "backend/uv.lock").unlink()
        self.assertEqual(self.invoke("--sync")[0], 1)
        self.assertFalse(self.destination.exists())
        self.write("backend/uv.lock", "version = 1\n")
        (self.source / "docs/contracts/example.schema.json").unlink()
        self.assertEqual(self.invoke("--sync")[0], 1)

    def test_unknown_files_including_gitignored_env_are_not_deleted_or_read(self):
        self.sync()
        for name in ["unknown.txt", ".env", "runtime/private.db"]:
            path = self.destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("UNKNOWN_FILE_KEEP")
            status, output = self.invoke("--sync", version="m3-20260908.2")
            self.assertEqual(status, 1)
            self.assertIn("未知文件", output)
            self.assertEqual(path.read_text(), "UNKNOWN_FILE_KEEP")
            path.unlink()

    def test_dirty_tracked_or_staged_output_is_rejected(self):
        self.sync()
        path = self.destination / "backend/src/example.py"
        path.write_text("LOCAL_EDIT_KEEP")
        self.assertEqual(self.invoke("--sync", version="m3-20260908.2")[0], 1)
        self.assertEqual(path.read_text(), "LOCAL_EDIT_KEEP")
        self.raw_git(self.destination, "add", "backend/src/example.py")
        self.assertEqual(self.invoke("--sync", version="m3-20260908.2")[0], 1)
        self.assertEqual(path.read_text(), "LOCAL_EDIT_KEEP")

    def test_existing_unmanaged_repository_and_overlap_are_rejected(self):
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        self.raw_git(unrelated, "init", "--initial-branch=main")
        (unrelated / "keep.txt").write_text("KEEP")
        self.assertEqual(self.invoke("--sync", destination=unrelated)[0], 1)
        self.assertEqual((unrelated / "keep.txt").read_text(), "KEEP")
        for destination in [self.source, self.source / "backup", self.source.parent]:
            self.assertEqual(self.invoke("--sync", destination=destination)[0], 1)

    def test_source_and_output_symlinks_are_rejected_before_content_read(self):
        secret = self.root / "private.txt"
        secret.write_text("DO_NOT_READ")
        link = self.source / "backend/src/link.py"
        link.symlink_to(secret)
        self.assertEqual(self.invoke("--sync")[0], 1)
        link.unlink()
        self.sync()
        manifest = self.destination / backup.MANIFEST
        manifest.unlink()
        manifest.symlink_to(secret)
        original = Path.read_bytes
        with patch.object(Path, "read_bytes", lambda path: (_ for _ in ()).throw(AssertionError("symlink read")) if path == manifest else original(path)):
            self.assertEqual(self.invoke("--sync")[0], 1)

    def test_binary_disguised_as_python_and_secret_shapes_are_blocked_without_values(self):
        sample = self.source / "backend/src/example.py"
        sample.write_bytes(b"\x00synthetic-binary")
        self.assertEqual(self.invoke("--sync")[0], 1)
        for value in ["sk-" + "A" * 40, "-----BEGIN " + "PRIVATE KEY-----", "ghp_" + "B" * 40]:
            sample.write_text('VALUE = "' + value + '"\n')
            status, output = self.invoke("--sync")
            self.assertEqual(status, 1)
            self.assertIn("backend/src/example.py", output)
            self.assertNotIn(value, output)
            self.assertFalse(self.destination.exists())

    def test_committed_manifest_escape_or_hash_tampering_is_rejected(self):
        self.sync()
        path = self.destination / backup.MANIFEST
        value = json.loads(path.read_text())
        value["files"][0]["path"] = "../outside.py"
        path.write_bytes(backup.json_bytes(value))
        self.raw_git(self.destination, "add", backup.MANIFEST)
        self.raw_git(self.destination, "commit", "-m", "Synthetic invalid manifest")
        self.assertEqual(self.invoke("--sync", version="m3-20260908.2")[0], 1)

    def test_commit_failure_restores_only_managed_files_and_index(self):
        first = self.sync()
        self.write("backend/src/new.py", "NEW = 1\n")
        (self.source / "backend/src/example.py").unlink()
        with patch.object(backup, "create_commit", side_effect=backup.BackupError("synthetic commit failure")):
            self.assertEqual(self.invoke("--sync", version="m3-20260908.2")[0], 1)
        self.assertEqual(self.raw_git(self.destination, "rev-parse", "HEAD"), first["commit"])
        self.assertFalse((self.destination / "backend/src/new.py").exists())
        self.assertTrue((self.destination / "backend/src/example.py").exists())
        self.assert_clean()

    def test_initial_commit_failure_does_not_leave_half_initialized_destination(self):
        with patch.object(backup, "create_commit", side_effect=backup.BackupError("synthetic failure")):
            self.assertEqual(self.invoke("--sync")[0], 1)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.destination.parent.glob(".backend-snapshot-*")))
        self.sync()

    def test_tag_failure_after_commit_can_resume_same_version_without_extra_commit(self):
        self.sync()
        self.write("backend/src/example.py", "VALUE = 5\n")
        with patch.object(backup, "ensure_tag", side_effect=backup.BackupError("synthetic tag failure")):
            self.assertEqual(self.invoke("--sync", version="m3-20260908.2")[0], 1)
        committed = self.raw_git(self.destination, "rev-parse", "HEAD")
        self.assert_clean()
        result = self.sync(version="m3-20260908.2")
        self.assertEqual(result["commit"], committed)
        self.assertEqual(self.raw_git(self.destination, "cat-file", "-t", "refs/tags/m3-20260908.2"), "tag")

    def test_source_change_during_export_does_not_publish_mixed_snapshot(self):
        collect = backup.collect_snapshot
        count = 0

        def changed(*args):
            nonlocal count
            count += 1
            if count == 2:
                self.write("backend/src/example.py", "VALUE = 6\n")
            return collect(*args)

        with patch.object(backup, "collect_snapshot", changed):
            self.assertEqual(self.invoke("--sync")[0], 1)
        self.assertFalse(self.destination.exists())

    def test_lightweight_tag_is_not_replaced(self):
        self.sync()
        self.raw_git(self.destination, "tag", "-d", self.version)
        self.raw_git(self.destination, "tag", self.version)
        self.assertEqual(self.invoke("--sync")[0], 1)
        self.assertEqual(self.raw_git(self.destination, "cat-file", "-t", self.version), "commit")

    def test_public_wrong_identity_or_malformed_github_response_blocks_before_git_push(self):
        for value in [{"nameWithOwner": backup.REPOSITORY, "isPrivate": False},
                      {"nameWithOwner": "fictional/wrong", "isPrivate": True},
                      {"nameWithOwner": backup.REPOSITORY, "isPrivate": "true"}, []]:
            with patch.object(backup, "command", return_value=json.dumps(value).encode()), patch.object(backup, "git", side_effect=AssertionError("must not push")):
                with self.assertRaises(backup.BackupError):
                    backup.push_snapshot(self.destination, self.version)

    def test_changed_origin_is_not_overwritten_or_pushed(self):
        self.sync()
        self.raw_git(self.destination, "remote", "set-url", "origin", "https://github.com/fictional/elsewhere.git")
        with patch.object(backup, "verify_github_private"):
            with self.assertRaises(backup.BackupError):
                backup.push_snapshot(self.destination, self.version)
        self.assertEqual(self.raw_git(self.destination, "remote", "get-url", "origin"), "https://github.com/fictional/elsewhere.git")

    def test_atomic_local_bare_push_checks_main_tag_object_and_peeled_commit_then_retries(self):
        result = self.sync()
        bare, operations, routed = self.bare_transport()
        with patch.object(backup, "verify_github_private") as private, patch.object(backup, "git", routed):
            first = backup.push_snapshot(self.destination, self.version)
            again = backup.push_snapshot(self.destination, self.version)
        self.assertEqual(private.call_count, 4)
        self.assertEqual(first, again)
        self.assertEqual(first["main"], result["commit"])
        self.assertEqual(self.raw_git(bare, "rev-parse", "refs/heads/main"), result["commit"])
        self.assertEqual(self.raw_git(bare, "rev-parse", f"refs/tags/{self.version}"), first["tag"])
        self.assertTrue(all("--atomic" in operation for operation in operations if operation[0] == "push"))

    def test_remote_divergence_rejects_atomically_without_new_remote_tag_or_forced_update(self):
        self.sync()
        bare, operations, routed = self.bare_transport()
        with patch.object(backup, "verify_github_private"), patch.object(backup, "git", routed):
            backup.push_snapshot(self.destination, self.version)
        other = self.root / "other"
        self.raw_git(self.root, "clone", str(bare), str(other))
        (other / "remote-only.txt").write_text("Synthetic remote divergence")
        self.raw_git(other, "add", "remote-only.txt")
        self.raw_git(other, "commit", "-m", "Synthetic divergent remote")
        self.raw_git(other, "push", "origin", "main")
        remote_head = self.raw_git(bare, "rev-parse", "main")
        self.write("backend/src/example.py", "VALUE = 7\n")
        self.sync(version="m3-20260908.2")
        with patch.object(backup, "verify_github_private"), patch.object(backup, "git", routed):
            with self.assertRaises(backup.BackupError):
                backup.push_snapshot(self.destination, "m3-20260908.2")
        self.assertEqual(self.raw_git(bare, "rev-parse", "main"), remote_head)
        self.assertEqual(self.raw_git(bare, "tag", "--list", "m3-20260908.2"), "")
        self.assert_clean()

    def test_remote_verification_mismatch_is_not_reported_as_success(self):
        self.sync()
        _, _, routed = self.bare_transport()

        def missing_ref(directory, *args, **kwargs):
            result = routed(directory, *args, **kwargs)
            return b"" if args[0] == "ls-remote" else result

        with patch.object(backup, "verify_github_private"), patch.object(backup, "git", missing_ref):
            with self.assertRaises(backup.BackupError):
                backup.push_snapshot(self.destination, self.version)

    def test_version_validation_and_push_requires_explicit_sync(self):
        for version in ["m3-20260230.1", "m4-20260230.1", "../escape", "m3-20260908.01",
                        "m4-20260910.01", "main", "m3-20260908.1;command", "m4-20260910.1;command",
                        "m2-20260910.1", "m5-20260910.1", "m34-20260910.1", "M4-20260910.1",
                        "m4-20260910.-1", "m4-20260910.", "m4-20260910.1\n", "m4-20260910.1２"]:
            self.assertEqual(self.invoke("--sync", version=version)[0], 1)
        self.assertEqual(self.invoke("--push")[0], 1)
        self.assertFalse(self.destination.exists())

    def test_m3_and_m4_versions_accept_valid_dates_and_nonnegative_sequence(self):
        for milestone in ('m3', 'm4'):
            for suffix in ('20260910.0', '20260910.1', '20260910.123', '20280229.1'):
                with self.subTest(version=f'{milestone}-{suffix}'):
                    backup.validate_version(f'{milestone}-{suffix}')
            with self.assertRaisesRegex(backup.BackupError, '版本中的日期无效'):
                backup.validate_version(f'{milestone}-20260229.1')
        with self.assertRaisesRegex(backup.BackupError, 'm3-YYYYMMDD.N 或 m4-YYYYMMDD.N'):
            backup.validate_version('m5-20260910.1')

    def test_m4_check_only_snapshot_manifest_preserves_version_without_writing_destination(self):
        version = 'm4-20260910.1'
        status, output = self.invoke(version=version)
        self.assertEqual(status, 0, output)
        self.assertEqual(json.loads(output)['version'], version)
        files, manifest, _ = backup.collect_snapshot(self.source, version)
        self.assertEqual(manifest['version'], version)
        self.assertEqual(files['VERSION'], (version + '\n').encode())
        self.assertFalse(self.destination.exists())

    def test_writers_lock_is_exclusive_and_stale_file_is_reusable(self):
        with backup.snapshot_lock(self.destination):
            with self.assertRaises(backup.BackupError):
                with backup.snapshot_lock(self.destination):
                    self.fail("second writer must not acquire")
        path = self.destination.parent / f".{self.destination.name}.backend-sync.lock"
        self.assertFalse(path.exists())
        path.write_bytes(b"")
        with backup.snapshot_lock(self.destination):
            pass
        self.assertFalse(path.exists())


class ReadbackRetryTests(unittest.TestCase):
    def test_transient_readback_retries_only_reads_and_returns_verified_input(self):
        with patch.object(backup, "git", side_effect=[backup.BackupError("offline"), b"refs"]) as git, patch.object(backup.time, "sleep") as sleep:
            self.assertEqual(backup.read_remote_refs(Path("synthetic"), "m3-20260908.2"), b"refs")
            self.assertEqual(git.call_count, 2)
            self.assertTrue(all(call.args[1] == "ls-remote" for call in git.call_args_list))
            sleep.assert_called_once_with(0.5)

    def test_persistent_readback_failure_stops_after_three_reads(self):
        with patch.object(backup, "git", side_effect=backup.BackupError("offline")) as git, patch.object(backup.time, "sleep") as sleep:
            with self.assertRaises(backup.BackupError):
                backup.read_remote_refs(Path("synthetic"), "m3-20260908.2")
            self.assertEqual(git.call_count, 3)
            self.assertTrue(all(call.args[1] == "ls-remote" for call in git.call_args_list))
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 1.0])


if __name__ == "__main__":
    unittest.main()
