#!/usr/bin/env python3
"""Manage the dedicated, loopback-only Redis used by local development."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time


LOCAL_ROOT = Path.home() / "Library/Application Support/AIJubenshaFusionRedis"
BIN_DIR = LOCAL_ROOT / "bin"
DATA_DIR = LOCAL_ROOT / "data"
CONFIG_FILE = LOCAL_ROOT / "redis.conf"
HOST = "127.0.0.1"
PORT = "56379"


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def require_installation() -> tuple[Path, Path]:
    server = BIN_DIR / "redis-server"
    client = BIN_DIR / "redis-cli"
    if any(not path.exists() for path in (server, client, CONFIG_FILE, DATA_DIR)):
        print("本项目的专用 Redis 尚未完整安装；请查看 docs/development/LOCAL_DEVELOPMENT.md。")
        raise SystemExit(2)
    return server, client


def command(client: Path, *parts: str) -> subprocess.CompletedProcess[str]:
    return run([str(client), "-h", HOST, "-p", PORT, "--raw", *parts])


def is_ready(client: Path) -> bool:
    result = command(client, "PING")
    return result.returncode == 0 and result.stdout.strip() == "PONG"


def owns_running_service(client: Path) -> bool:
    result = command(client, "CONFIG", "GET", "dir")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return result.returncode == 0 and lines[-1:] == [str(DATA_DIR)]


def status() -> int:
    _, client = require_installation()
    if not is_ready(client):
        print(f"本地 Redis 未运行：{HOST}:{PORT}")
        return 1
    if not owns_running_service(client):
        print(f"端口 {PORT} 上存在非本项目 Redis；为保护数据，拒绝接管。")
        return 2
    version = command(client, "INFO", "server").stdout
    version_line = next((line for line in version.splitlines() if line.startswith("redis_version:")), "")
    print(f"本地 Redis 已就绪：{HOST}:{PORT} {version_line}".strip())
    return 0


def start() -> int:
    server, client = require_installation()
    if is_ready(client):
        if owns_running_service(client):
            print(f"本地 Redis 已在 {HOST}:{PORT} 运行，无需重复启动。")
            return 0
        print(f"端口 {PORT} 已被其他 Redis 占用；为保护数据，拒绝启动。")
        return 2
    result = run([str(server), str(CONFIG_FILE)])
    if result.returncode != 0:
        print("本地 Redis 启动失败；请查看专用日志 log/redis.log。")
        return 1
    for _ in range(40):
        if is_ready(client) and owns_running_service(client):
            print(f"本地 Redis 已启动：{HOST}:{PORT}")
            return 0
        time.sleep(0.1)
    print("本地 Redis 启动超时；请查看专用日志 log/redis.log。")
    return 1


def stop() -> int:
    _, client = require_installation()
    if not is_ready(client):
        print("本地 Redis 已停止，无需重复操作。")
        return 0
    if not owns_running_service(client):
        print(f"端口 {PORT} 上不是本项目 Redis；为保护数据，拒绝停止。")
        return 2
    result = command(client, "SHUTDOWN", "SAVE")
    if result.returncode not in (0, 1):
        print("本地 Redis 未能正常停止；未删除任何文件。")
        return 1
    for _ in range(40):
        if not is_ready(client):
            print("本地 Redis 已安全停止；数据文件仍保留。")
            return 0
        time.sleep(0.1)
    print("本地 Redis 停止超时；未删除任何文件。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    action = parser.parse_args().action
    return {"start": start, "status": status, "stop": stop}[action]()


if __name__ == "__main__":
    sys.exit(main())
