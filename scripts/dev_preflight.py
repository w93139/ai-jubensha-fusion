"""Offline development preflight; never import the application or read .env.

Run with any Python 3.9+ interpreter. Exit codes: 0=checks pass,
1=coding prerequisites blocked, 2=coding possible with preparation warnings.
This checks local metadata, NOT database contents, backups, daemon health,
API credentials, model availability, billing, or full deployment readiness.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    message: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""


def run_command(args: Sequence[str], cwd: Path) -> CommandResult:
    """Run only callers' fixed local probes; suppress raw stderr and time out."""
    try:
        result = subprocess.run(
            list(args), cwd=str(cwd), capture_output=True, text=True,
            timeout=5, check=False,
        )
        return CommandResult(result.returncode, result.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(124)


def exit_code(findings: Sequence[Finding]) -> int:
    if any(item.level == "BLOCK" for item in findings):
        return 1
    return 2 if any(item.level == "WARN" for item in findings) else 0


def check_private_metadata(path: Path, label: str) -> list[Finding]:
    """Inspect one path's mode only: no read, glob, walk, or symlink traversal."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return [Finding("WARN", label, "不存在；离线编码不需要，但集成前需准备。")]
    except OSError:
        return [Finding("WARN", label, "无法读取权限元数据。")]
    if stat.S_ISLNK(metadata.st_mode):
        return [Finding("WARN", label, "是符号链接；目标和权限未检查，未读取内容。")]
    mode = stat.S_IMODE(metadata.st_mode)
    level = "WARN" if mode & 0o077 else "OK"
    suffix = "；组/其他用户有权限，请人工核对。" if level == "WARN" else "。"
    return [Finding(level, label, f"存在，权限 {mode:04o}{suffix}未读取内容。")]


def check_shebang(path: Path, expected_python: Path) -> list[Finding]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            first_line = stream.readline(2048).strip()
    except OSError:
        return [Finding("WARN", path.name, "无法检查启动脚本首行；请使用 python -m。")]
    actual = Path(first_line[2:]) if first_line.startswith("#!") else None
    try:
        points_to_expected_environment = bool(
            actual
            and actual.parent == expected_python.parent
            and actual.resolve() == expected_python.resolve()
        )
    except OSError:
        points_to_expected_environment = False
    if not points_to_expected_environment:
        return [Finding("WARN", path.name, "启动脚本未指向当前虚拟环境路径；不要直接运行它，改用 .venv/bin/python -m。")] 
    return [Finding("OK", path.name, "启动脚本指向当前虚拟环境。")]


PYTHON_PROBE = """import importlib.metadata as m, json, sys
names = ['pytest', 'uvicorn', 'fastapi', 'sqlalchemy', 'pydantic', 'jsonschema', 'python-dotenv', 'openai', 'redis', 'debugpy', 'pillow']
versions = {}
for name in names:
    try:
        versions[name] = m.version(name)
    except m.PackageNotFoundError:
        versions[name] = None
print(json.dumps({'python': list(sys.version_info[:3]), 'packages': versions}))
"""


def collect(
    root: Path,
    runner: Optional[Callable[[Sequence[str], Path], CommandResult]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
) -> list[Finding]:
    runner = runner or run_command
    which = which or shutil.which
    findings: list[Finding] = []
    backend_python = root / "backend/.venv/bin/python"
    if not backend_python.is_file() or not os.access(backend_python, os.X_OK):
        findings.append(Finding("BLOCK", "Python", "backend/.venv/bin/python 不存在或不可执行；不会自动创建/安装。"))
    else:
        result = runner([str(backend_python), "-I", "-B", "-c", PYTHON_PROBE], root)
        try:
            probe = json.loads(result.stdout) if result.returncode == 0 else {}
            version = probe["python"]
            packages = probe["packages"]
            if not isinstance(version, list) or len(version) != 3 or not all(isinstance(value, int) for value in version) or not isinstance(packages, dict):
                raise ValueError("invalid probe")
            findings.append(Finding("OK" if version[:2] == [3, 13] else "BLOCK", "Python", ".".join(map(str, version)) + "；项目要求 3.13.x。"))
            for name in ("pytest", "uvicorn", "fastapi", "sqlalchemy", "pydantic", "jsonschema", "python-dotenv", "openai", "redis", "pillow"):
                value = packages.get(name)
                findings.append(Finding("OK" if value else "BLOCK", name, str(value or "虚拟环境中未安装；不自动安装。")))
            if not packages.get("debugpy"):
                findings.append(Finding("WARN", "debugpy", "未安装；不影响离线任务，未提供断点调试 launch 配置。"))
            else:
                findings.append(Finding("OK", "debugpy", str(packages["debugpy"])))
        except (KeyError, TypeError, ValueError):
            findings.append(Finding("BLOCK", "Python", "解释器探针失败/超时；未导入项目代码，未显示原始错误。"))
    for name in ("pytest", "uvicorn"):
        findings.extend(check_shebang(root / "backend/.venv/bin" / name, backend_python))

    node = which("node")
    if not node:
        findings.append(Finding("BLOCK", "Node", "node 不在当前 PATH；VS Code 与终端的 PATH 可能不同。"))
    else:
        result = runner([node, "--version"], root)
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
        if result.returncode or not match:
            findings.append(Finding("BLOCK", "Node", "版本探针失败/超时。"))
        else:
            version = tuple(map(int, match.groups()))
            findings.append(Finding("OK" if version >= (20, 9, 0) else "BLOCK", "Node", result.stdout.strip() + "；Next 16 至少需要 20.9。"))
    findings.append(Finding("OK" if which("npm") else "WARN", "npm", "已找到。" if which("npm") else "不在 PATH；现有离线 Node 检查仍可独立运行。"))
    for name in ("next", "react", "react-dom", "typescript"):
        try:
            package = json.loads((root / "frontend/node_modules" / name / "package.json").read_text(encoding="utf-8"))
            version = package["version"]
            if not isinstance(version, str):
                raise ValueError("invalid version")
            findings.append(Finding("OK", name, "已安装 " + version))
        except (OSError, ValueError, KeyError, TypeError):
            findings.append(Finding("BLOCK", name, "缺少/无法读取已安装依赖的版本元数据；不会安装。"))

    uv = which("uv")
    if uv:
        result = runner([uv, "--version"], root)
        match = re.match(r"uv \d+\.\d+\.\d+", result.stdout)
        findings.append(Finding("OK" if result.returncode == 0 and match else "WARN", "uv", match.group(0) if result.returncode == 0 and match else "版本探针失败/超时；已有虚拟环境仍可直接调用。"))
    else:
        findings.append(Finding("WARN", "uv", "不在 PATH；现有虚拟环境可继续使用，不自动安装。"))

    git = which("git")
    if not git or runner([git, "rev-parse", "--is-inside-work-tree"], root).stdout.strip() != "true":
        findings.append(Finding("BLOCK", "Git", "无法确认当前目录为 Git 工作区。"))
    else:
        protected = (".env", "backend/.env", "frontend/.env.local", "private-data/preflight-placeholder")
        for relative in protected:
            result = runner([git, "check-ignore", "-q", "--no-index", "--", relative], root)
            findings.append(Finding("OK" if result.returncode == 0 else "WARN", "Git 忽略 " + relative, "规则已覆盖。" if result.returncode == 0 else "未确认忽略；请人工检查，不会自动修改规则。"))
        tracked = runner([git, "ls-files", "-z", "--", ".env", "backend/.env", "frontend/.env.local", "private-data"], root)
        if tracked.returncode:
            findings.append(Finding("WARN", "Git 私密文件", "无法确认跟踪状态。"))
        else:
            findings.append(Finding("BLOCK" if tracked.stdout else "OK", "Git 私密文件", "发现受保护路径被跟踪；请人工处理，未读取内容。" if tracked.stdout else "指定私密路径未被跟踪；未扫描文件内容。"))
    findings.extend(check_private_metadata(root / ".env", ".env 元数据"))
    findings.extend(check_private_metadata(root.parent / "private-data", "仓库外 private-data 元数据"))

    if which("docker"):
        findings.append(Finding("OK", "Docker CLI", "已找到；daemon、Compose、卷和数据库未验证。"))
    else:
        local_pg = Path.home() / "Library/Application Support/AIJubenshaFusionTest/Postgres.app/Contents/Versions/17/bin/pg_ctl"
        local_redis = Path.home() / "Library/Application Support/AIJubenshaFusionRedis/bin/redis-server"
        if local_pg.is_file() and local_redis.is_file():
            findings.append(Finding("OK", "本机服务方案", "未安装 Docker；已准备项目专用 PostgreSQL 与 Redis，足够本机开发。生产部署仍需单独验收。"))
        else:
            findings.append(Finding("WARN", "Docker CLI", "不在 PATH，且未确认专用本机数据库/缓存；集成前需准备一种运行方案。"))
    if which("code"):
        findings.append(Finding("OK", "VS Code CLI", "已找到；未启动编辑器或检查扩展。"))
    else:
        findings.append(Finding("WARN", "VS Code CLI", "code 不在 PATH；可通过 VS Code 菜单打开工作区，不影响现有终端任务。"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出结构化检查结果（无环境变量值）")
    args = parser.parse_args()
    findings = collect(Path(__file__).resolve().parents[1])
    code = exit_code(findings)
    if args.json:
        print(json.dumps({"exit_code": code, "checks": [asdict(item) for item in findings]}, ensure_ascii=False, indent=2))
    else:
        print("离线开发预检：不读取 .env 内容、不加载应用、不连 DB/网络、不自动安装。")
        for item in findings:
            print(f"[{item.level}] {item.check}: {item.message}")
        print(f"退出码 {code}（0=检查通过；1=编码阻塞；2=可编码但有准备/集成警告）。")
        print("本结果不证明密钥有效、数据库/备份正常或部署就绪。")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
