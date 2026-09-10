#!/usr/bin/env python3
"""Manage the dedicated, loopback-only PostgreSQL used by this project.

This helper never creates, deletes, migrates, or inspects database contents. It
only starts, stops, or checks the exact Postgres.app cluster prepared for local
development on 127.0.0.1:55432.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


LOCAL_ROOT = Path.home() / "Library/Application Support/AIJubenshaFusionTest"
DATA_DIR = LOCAL_ROOT / "pgdata"
BIN_DIR = LOCAL_ROOT / "Postgres.app/Contents/Versions/17/bin"
LOG_FILE = LOCAL_ROOT / "postgres-55432.log"
HOST = "127.0.0.1"
PORT = "55432"


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def require_installation() -> tuple[Path, Path]:
    pg_ctl = BIN_DIR / "pg_ctl"
    pg_isready = BIN_DIR / "pg_isready"
    missing = [path for path in (pg_ctl, pg_isready, DATA_DIR / "PG_VERSION") if not path.exists()]
    if missing:
        print("本项目的专用 PostgreSQL 尚未完整安装；请先查看 docs/development/LOCAL_DEVELOPMENT.md。")
        raise SystemExit(2)
    return pg_ctl, pg_isready


def is_ready(pg_isready: Path) -> bool:
    result = run([str(pg_isready), "-h", HOST, "-p", PORT])
    return result.returncode == 0


def status() -> int:
    _, pg_isready = require_installation()
    if is_ready(pg_isready):
        print(f"本地 PostgreSQL 已就绪：{HOST}:{PORT}")
        return 0
    print(f"本地 PostgreSQL 未运行：{HOST}:{PORT}")
    return 1


def start() -> int:
    pg_ctl, pg_isready = require_installation()
    if is_ready(pg_isready):
        print(f"本地 PostgreSQL 已在 {HOST}:{PORT} 运行，无需重复启动。")
        return 0
    LOG_FILE.touch(mode=0o600, exist_ok=True)
    result = run([
        str(pg_ctl),
        "-D", str(DATA_DIR),
        "-l", str(LOG_FILE),
        "-o", f"-h {HOST} -p {PORT}",
        "-w", "-t", "20", "start",
    ])
    if result.returncode == 0 and is_ready(pg_isready):
        print(f"本地 PostgreSQL 已启动：{HOST}:{PORT}")
        return 0
    print("本地 PostgreSQL 启动失败；请查看专用日志 postgres-55432.log。")
    return 1


def stop() -> int:
    pg_ctl, pg_isready = require_installation()
    if not is_ready(pg_isready):
        print("本地 PostgreSQL 已停止，无需重复操作。")
        return 0
    result = run([
        str(pg_ctl),
        "-D", str(DATA_DIR),
        "-m", "fast",
        "-w", "-t", "20", "stop",
    ])
    if result.returncode == 0 and not is_ready(pg_isready):
        print("本地 PostgreSQL 已安全停止；数据库文件仍保留。")
        return 0
    print("本地 PostgreSQL 未能正常停止；未删除任何文件。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    action = parser.parse_args().action
    return {"start": start, "status": status, "stop": stop}[action]()


if __name__ == "__main__":
    sys.exit(main())
