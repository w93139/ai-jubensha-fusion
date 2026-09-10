"""Export a reviewed backend snapshot; network writes require explicit --sync --push."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
from typing import Sequence


REPOSITORY = "w93139/ai-jubensha-backend"
REMOTE_URL = f"https://github.com/{REPOSITORY}.git"
MANIFEST = "snapshot-manifest.json"
SCHEMA = "backend-snapshot/1.0"
README_SOURCE = "docs/development/BACKEND_GITHUB_BACKUP.md"
TOOL_FILES = ("scripts/sync_backend_github.py", "scripts/test_sync_backend_github.py")
EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".data", "data", "log", "logs", "uploads",
    "output", "outputs", "comfyui_outputs", "comfyui_temp", "private-data",
    "script-library", "source-materials", "backups", "ocr", "ocr文件库",
    ".runtime", ".transcripts", ".task_outputs",
}
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".ini", ".mako", ".lock", ".sh", ".yml", ".yaml", ".cfg", ".conf"}
SPECIAL_BACKEND = {"backend/Dockerfile", "backend/.dockerignore", "backend/static/placeholder.txt"}
MAX_FILE_BYTES = 20_000_000
GENERATED_IGNORE = """# This repository stores code snapshots, never runtime data.
.env
.env.*
!.env.example
.venv/
__pycache__/
.pytest_cache/
.data/
private-data/
backups/
*.db
*.sqlite*
*.dump
*.log
*.pyc
.DS_Store
"""
# Tests construct synthetic matches from fragments, so the scanner does not
# mistake its own test fixture source for a committed service credential.
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,}|(?:AKIA|ASIA)[A-Z0-9]{16})(?![A-Za-z0-9])"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{15,}"),
    re.compile(r'''(?im)(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[=:]\s*["'][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}["']'''),
)


class BackupError(Exception):
    """Only safe, value-free diagnostics may be supplied to this exception."""


def command(args: Sequence[str], cwd: Path | None = None) -> bytes:
    env = dict(os.environ)
    # A caller's alternate Git index/worktree must not redirect the export.
    for name in list(env):
        if name.startswith("GIT_") and name not in {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}:
            env.pop(name)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GH_HOST": "github.com"})
    try:
        result = subprocess.run(list(args), cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError("命令不可用或超时；未输出命令中的路径、凭据或服务响应。") from exc
    if result.returncode:
        raise BackupError("Git/GitHub 操作失败；请检查权限、网络或远端分叉。可用同版本重试，禁止强推。")
    return result.stdout


def git(directory: Path, *args: str, network: bool = False) -> bytes:
    options = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "tag.gpgSign=false"]
    if network:
        options += ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", "-c", "credential.useHttpPath=true"]
    return command([*options, *args], directory)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_relative(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and "\\" not in name and all(
        part not in {"", ".", ".."} for part in name.split("/")
    ) and not any(ord(character) < 32 for character in name)


def reject_link_chain(path: Path) -> None:
    for current in (path, *path.parents):
        if current.is_symlink():
            raise BackupError("路径包含符号链接，已拒绝读取或写入。")


def allowed_backend(name: str) -> bool:
    if not safe_relative(name) or not name.startswith("backend/"):
        return False
    path = PurePosixPath(name)
    if any(part.lower() in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.name.startswith(".env") or path.name == ".DS_Store" or path.name.startswith("._"):
        return False
    if name in SPECIAL_BACKEND:
        return True
    if path.suffix.lower() == ".txt":
        return path.parent == PurePosixPath("backend/src/fusion/prompts")
    return path.suffix.lower() in TEXT_SUFFIXES


def allowed_output(name: str) -> bool:
    return allowed_backend(name) or name in {
        ".env.example", "LICENSE", ".gitignore", "README.md", "VERSION", *TOOL_FILES,
    } or (safe_relative(name) and PurePosixPath(name).parent == PurePosixPath("docs/contracts")
          and name.endswith(".json"))


def inspect_text(name: str, data: bytes) -> None:
    if len(data) > MAX_FILE_BYTES or b"\0" in data:
        raise BackupError(f"非文本或过大文件，拒绝导出：{name}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackupError(f"非 UTF-8 文本，拒绝导出：{name}") from exc
    if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
        raise BackupError(f"文件含二进制控制字符：{name}")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise BackupError(f"检测到密钥形状，拒绝导出：{name}")


def validate_version(version: str) -> None:
    if not re.fullmatch(r"m3-\d{8}\.(?:0|[1-9]\d*)", version):
        raise BackupError("版本格式必须为 m3-YYYYMMDD.N。")
    try:
        datetime.strptime(version[3:11], "%Y%m%d")
    except ValueError as exc:
        raise BackupError("版本中的日期无效。") from exc


def collect_snapshot(source: Path, version: str) -> tuple[dict[str, bytes], dict[str, object], list[str]]:
    validate_version(version)
    if Path(git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve() != source:
        raise BackupError("来源必须是工程 Git 根目录。")
    candidates = git(source, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "backend").decode().split("\0")
    selected = {name for name in candidates if name and allowed_backend(name)}
    excluded = sorted({name for name in candidates if name and name not in selected})
    # --cached also lists tracked files deleted from the current working tree.
    # Mirror the current reviewed files; required dependencies are checked below.
    for name in tuple(selected):
        reject_link_chain(source / name)
        if not (source / name).exists():
            selected.remove(name)
            excluded.append(name)
    contracts = source / "docs/contracts"
    reject_link_chain(contracts)
    if not contracts.is_dir():
        raise BackupError("缺少必需契约目录：docs/contracts")
    selected.update(str(path.relative_to(source).as_posix()) for path in contracts.glob("*.json"))
    required = {"backend/main.py", "backend/pyproject.toml", "backend/uv.lock", "backend/scripts/test_fusion_security.py"}
    if not required <= selected or not any(name.startswith("docs/contracts/") for name in selected):
        raise BackupError("后端入口、锁文件、安全测试入口或契约不完整。")
    copies = {name: name for name in selected}
    copies.update({name: name for name in (".env.example", "LICENSE", *TOOL_FILES)})
    copies["README.md"] = README_SOURCE
    files: dict[str, bytes] = {}
    for target_name, source_name in sorted(copies.items()):
        if not allowed_output(target_name):
            raise BackupError("候选文件不在显式白名单内。")
        path = source / source_name
        reject_link_chain(path)
        if not path.is_file():
            raise BackupError(f"缺少备份依赖：{source_name}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise BackupError(f"无法读取备份依赖：{source_name}") from exc
        inspect_text(target_name, data)
        files[target_name] = data
    files[".gitignore"] = GENERATED_IGNORE.encode()
    files["VERSION"] = (version + "\n").encode()
    manifest = {
        "schema_version": SCHEMA, "repository": REPOSITORY, "version": version,
        "files": [{"path": name, "sha256": digest(data), "bytes": len(data)} for name, data in sorted(files.items())],
    }
    files[MANIFEST] = json_bytes(manifest)
    return files, manifest, excluded


def read_manifest(directory: Path) -> dict[str, object]:
    reject_link_chain(directory / MANIFEST)
    try:
        value = json.loads((directory / MANIFEST).read_bytes())
    except (OSError, ValueError) as exc:
        raise BackupError("目标不是已管理的后端快照；拒绝接管现有目录。") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "repository", "version", "files"}:
        raise BackupError("目标快照清单格式无效。")
    if value["schema_version"] != SCHEMA or value["repository"] != REPOSITORY:
        raise BackupError("目标快照身份不匹配。")
    validate_version(value["version"] if isinstance(value["version"], str) else "")
    rows = value["files"]
    if not isinstance(rows, list) or not rows:
        raise BackupError("目标快照文件清单无效。")
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}:
            raise BackupError("目标快照文件条目无效。")
        name = row["path"]
        if not isinstance(name, str) or not allowed_output(name) or name in names:
            raise BackupError("目标快照含越界或重复路径。")
        if not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or type(row["bytes"]) is not int or row["bytes"] < 0:
            raise BackupError("目标快照摘要无效。")
        names.add(name)
    if not {".gitignore", "README.md", "VERSION", *TOOL_FILES} <= names:
        raise BackupError("目标快照缺少管理文件。")
    return value


def validate_destination(source: Path, destination: Path) -> dict[str, object] | None:
    reject_link_chain(source)
    reject_link_chain(destination)
    if source == destination or source in destination.parents or destination in source.parents:
        raise BackupError("来源与快照目录不得重叠。")
    if not destination.exists():
        return None
    if not destination.is_dir():
        raise BackupError("快照目标不是目录。")
    if not any(destination.iterdir()):
        return None
    if not (destination / ".git").is_dir() or (destination / ".git").is_symlink():
        raise BackupError("目标必须是工具创建的独立 Git 快照仓库。")
    for parent, directories, names in os.walk(destination / ".git", followlinks=False):
        if any((Path(parent) / name).is_symlink() for name in directories + names):
            raise BackupError("目标 Git 元数据含符号链接，拒绝更新。")
    if (destination / ".git/index.lock").exists():
        raise BackupError("目标 Git 索引正在被其他操作占用。")
    if (destination / ".git/commondir").exists() or (destination / ".git/objects/info/alternates").exists():
        raise BackupError("目标共享其他 Git 仓库，拒绝更新。")
    if Path(git(destination, "rev-parse", "--show-toplevel").decode().strip()).resolve() != destination:
        raise BackupError("目标 Git 根目录不匹配。")
    if git(destination, "branch", "--show-current").decode().strip() != "main":
        raise BackupError("目标必须处于 main 分支。")
    manifest = read_manifest(destination)
    managed = {row["path"] for row in manifest["files"]} | {MANIFEST}
    actual: set[str] = set()
    for parent, directories, names in os.walk(destination, followlinks=False):
        directory = Path(parent)
        if directory == destination:
            directories[:] = [name for name in directories if name != ".git"]
        for name in directories + names:
            path = directory / name
            if path.is_symlink():
                raise BackupError("目标含符号链接，拒绝更新。")
        for name in names:
            path = directory / name
            if not path.is_file():
                raise BackupError("目标含非普通文件，拒绝更新。")
            actual.add(path.relative_to(destination).as_posix())
    if actual != managed:
        raise BackupError("目标含未知文件或缺失管理文件；不删除未知文件。")
    tracked = set(filter(None, git(destination, "ls-files", "-z").decode().split("\0")))
    if tracked != managed or git(destination, "status", "--porcelain", "--untracked-files=all"):
        raise BackupError("目标工作区或索引不干净，拒绝覆盖。")
    for row in manifest["files"]:
        data = (destination / row["path"]).read_bytes()
        if len(data) != row["bytes"] or digest(data) != row["sha256"]:
            raise BackupError(f"目标文件与清单不符：{row['path']}")
    if (destination / "VERSION").read_text().strip() != manifest["version"]:
        raise BackupError("目标版本与清单不符。")
    if git(destination, "show", f"HEAD:{MANIFEST}") != (destination / MANIFEST).read_bytes():
        raise BackupError("目标清单尚未提交。")
    return manifest


def tag_target(directory: Path, version: str) -> str | None:
    if not git(directory, "tag", "--list", version).strip():
        return None
    if git(directory, "cat-file", "-t", f"refs/tags/{version}").strip() != b"tag":
        raise BackupError("已有版本不是 annotated tag，拒绝改写。")
    return git(directory, "rev-parse", f"refs/tags/{version}^{{commit}}").decode().strip()


def validate_version_binding(directory: Path, old: dict[str, object] | None, files: dict[str, bytes], version: str) -> bool:
    if old is None:
        return False
    current = (directory / MANIFEST).read_bytes()
    same = current == files[MANIFEST]
    if old["version"] == version and not same:
        raise BackupError("同版本源码已变化；请使用新版本，禁止改写旧 tag。")
    target = tag_target(directory, version)
    if target is not None:
        head = git(directory, "rev-parse", "HEAD").decode().strip()
        if not same or target != head or git(directory, "show", f"refs/tags/{version}:{MANIFEST}") != files[MANIFEST]:
            raise BackupError("已有 tag 与当前输入不一致，拒绝重写或回退。")
    return same


def write_files(directory: Path, files: dict[str, bytes], previous: set[str]) -> None:
    for name in sorted(previous - files.keys()):
        path = directory / name
        path.unlink()
        parent = path.parent
        while parent != directory:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)


def create_commit(directory: Path, message: str) -> None:
    # Explicit identity for generated artifacts, scoped to this command only.
    # GIT_AUTHOR_* / GIT_COMMITTER_* environment overrides retain Git semantics.
    command(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
             "-c", "user.name=Backend snapshot", "-c", "user.email=backend-snapshot@users.noreply.github.com",
             "commit", "-m", message], directory)


def ensure_tag(directory: Path, version: str) -> None:
    if tag_target(directory, version) is None:
        command(["git", "-c", "core.hooksPath=/dev/null", "-c", "tag.gpgSign=false",
                 "-c", "user.name=Backend snapshot", "-c", "user.email=backend-snapshot@users.noreply.github.com",
                 "tag", "-a", version, "-m", f"Verified backend snapshot {version}"], directory)


def sync_local(source: Path, directory: Path, files: dict[str, bytes], version: str, message: str,
               old: dict[str, object] | None) -> str:
    same = validate_version_binding(directory, old, files, version)
    if same:
        ensure_tag(directory, version)
        return git(directory, "rev-parse", "HEAD").decode().strip()
    if old is None:
        directory.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".backend-snapshot-", dir=directory.parent))
        try:
            git(temporary, "init", "--initial-branch=main")
            git(temporary, "remote", "add", "origin", REMOTE_URL)
            write_files(temporary, files, set())
            git(temporary, "add", "--", *sorted(files))
            if collect_snapshot(source, version)[0] != files:
                raise BackupError("来源在导出期间变化；请完成开发后重新检查。")
            create_commit(temporary, message)
            ensure_tag(temporary, version)
            if directory.exists():
                if any(directory.iterdir()):
                    raise BackupError("目标在导出期间出现未知文件，拒绝替换。")
                directory.rmdir()
            temporary.rename(directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    else:
        previous = {row["path"]: (directory / row["path"]).read_bytes() for row in old["files"]}
        previous[MANIFEST] = (directory / MANIFEST).read_bytes()
        original_head = git(directory, "rev-parse", "HEAD").strip()
        try:
            write_files(directory, files, set(previous))
            git(directory, "add", "--all", "--", *sorted(set(files) | set(previous)))
            if collect_snapshot(source, version)[0] != files:
                raise BackupError("来源在导出期间变化；请完成开发后重新检查。")
            create_commit(directory, message)
        except Exception:
            # Roll back only this tool's changes, from a previously clean tree.
            # If Git actually committed before failing, keep the valid snapshot
            # so the identical version can finish its tag on the next attempt.
            if git(directory, "rev-parse", "HEAD").strip() == original_head:
                write_files(directory, previous, set(files))
                git(directory, "read-tree", "HEAD")
            raise
        ensure_tag(directory, version)
    return git(directory, "rev-parse", "HEAD").decode().strip()


def verify_github_private() -> None:
    try:
        value = json.loads(command(["gh", "repo", "view", REPOSITORY, "--json", "nameWithOwner,isPrivate"]))
    except ValueError as exc:
        raise BackupError("无法核验 GitHub 仓库隐私，未推送。") from exc
    if not isinstance(value, dict) or value.get("nameWithOwner") != REPOSITORY or value.get("isPrivate") is not True:
        raise BackupError("GitHub 目标不是指定的私有仓库，未推送。")


def push_snapshot(directory: Path, version: str) -> dict[str, str]:
    verify_github_private()
    if git(directory, "remote", "get-url", "--all", "origin").decode().splitlines() != [REMOTE_URL] or git(directory, "remote", "get-url", "--push", "--all", "origin").decode().splitlines() != [REMOTE_URL]:
        raise BackupError("目标 origin 与固定私有仓库不匹配，未推送。")
    head = git(directory, "rev-parse", "HEAD").decode().strip()
    if tag_target(directory, version) != head:
        raise BackupError("当前 tag 未绑定本地快照，未推送。")
    tag = git(directory, "rev-parse", f"refs/tags/{version}").decode().strip()
    git(directory, "push", "--atomic", "origin", "HEAD:refs/heads/main", f"refs/tags/{version}:refs/tags/{version}", network=True)
    raw = read_remote_refs(directory, version)
    refs = {}
    for line in raw.decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[1]] = parts[0]
    if refs.get("refs/heads/main") != head or refs.get(f"refs/tags/{version}") != tag or refs.get(f"refs/tags/{version}^{{}}") != head:
        raise BackupError("推送后远端 main/tag 摘要不一致；未宣称备份完成。")
    verify_github_private()
    return {"main": head, "tag": tag}


def read_remote_refs(directory: Path, version: str) -> bytes:
    # Uploads may succeed while the immediately following remote read fails.
    # Retry only this read; never add an implicit second push or force update.
    for attempt in range(3):
        try:
            return git(directory, "ls-remote", "origin", "refs/heads/main",
                       f"refs/tags/{version}", f"refs/tags/{version}^{{}}", network=True)
        except BackupError:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise BackupError("远端版本未完成核验。")


@contextmanager
def snapshot_lock(directory: Path):
    """Serialize our writers without leaving a stale lock after a killed process."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    path = directory.parent / f".{directory.name}.backend-sync.lock"
    reject_link_chain(path)
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("该快照已有同步任务运行，未执行第二次同步。") from exc
        try:
            yield
        finally:
            # Remove while still locked; no mutation follows its removal. An
            # interrupted process instead leaves an unlocked, reusable file.
            path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--message", default="")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.push and not args.sync:
            raise BackupError("--push 必须与 --sync 同时指定。")
        source = args.source_root.absolute()
        destination = args.snapshot_dir.absolute()
        reject_link_chain(source)
        reject_link_chain(destination)
        source, destination = source.resolve(), destination.resolve()
        old = validate_destination(source, destination)
        files, manifest, excluded = collect_snapshot(source, args.version)
        same = validate_version_binding(destination, old, files, args.version)
        message = args.message or f"Backend snapshot {args.version}"
        if len(message) > 2_000:
            raise BackupError("提交说明过长。")
        inspect_text("--message", message.encode())
        summary: dict[str, object] = {
            "mode": "CHECK_ONLY", "repository": REPOSITORY, "version": args.version,
            "files": len(files), "bytes": sum(map(len, files.values())),
            "manifest_sha256": digest(files[MANIFEST]), "same_version_and_input": same,
            "categories": {"backend": sum(name.startswith("backend/") for name in files),
                           "contracts": sum(name.startswith("docs/contracts/") for name in files),
                           "support": sum(not name.startswith(("backend/", "docs/contracts/")) for name in files)},
            "excluded_paths": excluded,
        }
        if args.sync:
            with snapshot_lock(destination):
                old = validate_destination(source, destination)
                summary["commit"] = sync_local(source, destination, files, args.version, message, old)
                validate_destination(source, destination)
                summary["mode"] = "LOCAL_SNAPSHOT"
                if args.push:
                    summary["remote_refs"] = push_snapshot(destination, args.version)
                    summary["mode"] = "PUSHED_AND_VERIFIED"
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except BackupError as exc:
        print(f"备份已停止：{exc}")
        return 1
    except (OSError, ValueError, TypeError):
        print("备份已停止：本地文件或清单无效；未输出私有路径、凭据或文件正文。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
