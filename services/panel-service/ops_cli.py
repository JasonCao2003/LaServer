# -*- coding: utf-8 -*-
"""无桌面环境下的 Sky 私服 CLI 运维脚本。

支持在 Linux / Windows 无 GUI 环境下管理 api-service 与 room-service：
- 启停与状态查看（PID 管理 + 端口探测）
- 实时日志查看（tail / follow）
- 数据库账号列表与只读查询
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PANEL_DIR.parent.parent
API_DIR = PROJECT_ROOT / "services" / "api-service"
ROOM_DIR = PROJECT_ROOT / "services" / "room-service"
LOG_DIR = PANEL_DIR / "logs"
PID_DIR = PANEL_DIR / "run"
DB_PATH = API_DIR / "var" / "data" / "instance" / "users.db"

SERVICES = {
    "api": {
        "name": "api-service",
        "cwd": PROJECT_ROOT,
        "cmd": [sys.executable, "-u", str(API_DIR / "sky_boot.py")],
        "port": 8443,
        "log": LOG_DIR / "api-service.log",
        "pid": PID_DIR / "api-service.pid",
    },
    "room": {
        "name": "room-service",
        "cwd": ROOM_DIR,
        "cmd": ["node", "src/main.js"],
        "port": 8080,
        "log": LOG_DIR / "room-service.log",
        "pid": PID_DIR / "room-service.pid",
    },
}


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        res = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in res.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_service(key: str) -> None:
    ensure_dirs()
    svc = SERVICES[key]
    pid = read_pid(svc["pid"])
    if pid and is_process_alive(pid):
        print(f"[{svc['name']}] 已在运行中 (PID={pid})")
        return

    log_fp = open(svc["log"], "a", encoding="utf-8")
    kwargs: dict = {
        "cwd": str(svc["cwd"]),
        "stdout": log_fp,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(svc["cmd"], **kwargs)
    svc["pid"].write_text(str(proc.pid), encoding="utf-8")
    print(f"[{svc['name']}] 已启动 (PID={proc.pid})，日志: {svc['log']}")


def stop_service(key: str) -> None:
    svc = SERVICES[key]
    pid = read_pid(svc["pid"])
    if not pid or not is_process_alive(pid):
        print(f"[{svc['name']}] 未运行")
        if svc["pid"].exists():
            svc["pid"].unlink()
        return

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(20):
            if not is_process_alive(pid):
                break
            time.sleep(0.25)
        if is_process_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

    if svc["pid"].exists():
        svc["pid"].unlink()
    print(f"[{svc['name']}] 已停止 (PID={pid})")


def status_service(key: str) -> None:
    svc = SERVICES[key]
    pid = read_pid(svc["pid"])
    alive = bool(pid and is_process_alive(pid))
    port_up = is_port_open(svc["port"])
    state = "RUNNING" if alive else ("PORT_OPEN(外部进程)" if port_up else "STOPPED")
    print(
        f"{svc['name']:<14} | 状态: {state:<18} | PID: {str(pid or '-'):<8} | 端口 {svc['port']}: {'UP' if port_up else 'DOWN'}"
    )


def tail_logs(key: str, lines: int, follow: bool) -> None:
    log_file = SERVICES[key]["log"]
    if not log_file.exists():
        print(f"日志文件不存在: {log_file}")
        return

    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        content = f.readlines()
        for line in content[-lines:]:
            print(line, end="")
        if not follow:
            return
        print(f"\n--- 正在实时跟踪 {log_file} (Ctrl+C 退出) ---")
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n已退出日志跟踪")


def list_tables() -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print("数据表:", ", ".join(r[0] for r in rows))


def list_users(limit: int) -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        target = "users" if "users" in tables else ("user" if "user" in tables else None)
        if not target:
            print(f"未找到 users/user 表，现有表: {', '.join(sorted(tables))}")
            return
        rows = conn.execute(f"SELECT * FROM {target} LIMIT ?", (limit,)).fetchall()
        if not rows:
            print("暂无用户记录")
            return
        cols = rows[0].keys()
        print(" | ".join(cols))
        print("-" * 60)
        for row in rows:
            print(" | ".join(str(row[c]) for c in cols))


def resolve_targets(target: str) -> list[str]:
    return ["api", "room"] if target == "all" else [target]


def user_info(user_id: int) -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        u = conn.execute("SELECT id, device, device_identifier, is_banned, remark, created_at FROM user WHERE id = ?", (user_id,)).fetchone()
        if not u:
            print(f"未找到用户 ID={user_id}")
            return
        print(dict(u))
        c = conn.execute("SELECT * FROM user_currency WHERE user_id = ?", (user_id,)).fetchone()
        print("货币:", dict(c) if c else "无记录")
        cnt = conn.execute("SELECT COUNT(*) FROM unlock_item WHERE user_id = ?", (user_id,)).fetchone()[0]
        print(f"已解锁物品数 (unlock_item): {cnt}")


def add_currency(user_id: int, field: str, amount: int) -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        u = conn.execute("SELECT id FROM user WHERE id = ?", (user_id,)).fetchone()
        if not u:
            print(f"未找到用户 ID={user_id}")
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(f"SELECT {field} FROM user_currency WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                f"INSERT INTO user_currency (user_id, {field}, updated_at) VALUES (?, ?, ?)",
                (user_id, amount, now),
            )
            new_val = amount
        else:
            old_val = row[0] or 0
            new_val = old_val + amount
            conn.execute(
                f"UPDATE user_currency SET {field} = ?, updated_at = ? WHERE user_id = ?",
                (new_val, now, user_id),
            )
        conn.commit()
        print(f"用户 {user_id} 的 {field} 已更新为 {new_val} (变化: {amount:+d})")


def unlock_all_items(user_id: int) -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        u = conn.execute("SELECT id FROM user WHERE id = ?", (user_id,)).fetchone()
        if not u:
            print(f"未找到用户 ID={user_id}")
            return
        # 从全库已有物品定义中汇总物品名与类型
        items: set[tuple[str, str]] = set()
        for name, utype in conn.execute("SELECT DISTINCT name, COALESCE(unlock_type, '') FROM unlock_item").fetchall():
            if name:
                items.add((name, utype))
        existing = {
            r[0] for r in conn.execute("SELECT name FROM unlock_item WHERE user_id = ?", (user_id,)).fetchall()
        }
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        to_insert = [(user_id, name, utype, 1, now) for name, utype in items if name not in existing]
        if to_insert:
            conn.executemany(
                "INSERT INTO unlock_item (user_id, name, unlock_type, ack, created_at) VALUES (?, ?, ?, ?, ?)",
                to_insert,
            )
        conn.commit()
        print(f"用户 {user_id} 解锁完成：新增 {len(to_insert)} 个物品，当前共 {len(existing) + len(to_insert)} 个。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sky 私服无桌面 CLI 运维工具")
    sub = parser.add_subparsers(dest="command", required=True)

    for act in ("start", "stop", "restart", "status"):
        p = sub.add_parser(act, help=f"{act} 服务")
        p.add_argument("target", nargs="?", default="all", choices=["api", "room", "all"])

    p_logs = sub.add_parser("logs", help="查看服务日志")
    p_logs.add_argument("target", choices=["api", "room"])
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="显示最后 N 行")
    p_logs.add_argument("-f", "--follow", action="store_true", help="实时跟踪日志")

    p_db = sub.add_parser("db", help="数据库只读查看")
    p_db.add_argument("action", choices=["tables", "users"])
    p_db.add_argument("--limit", type=int, default=20, help="显示记录数")

    p_user = sub.add_parser("user", help="账户管理")
    user_sub = p_user.add_subparsers(dest="user_action", required=True)

    p_u_list = user_sub.add_parser("list", help="列出用户")
    p_u_list.add_argument("--limit", type=int, default=20)

    p_u_info = user_sub.add_parser("info", help="查看用户详情与货币")
    p_u_info.add_argument("user_id", type=int)

    p_u_curr = user_sub.add_parser("add-currency", help="为用户添加货币")
    p_u_curr.add_argument("user_id", type=int)
    p_u_curr.add_argument(
        "--type",
        required=True,
        choices=[
            "candles", "heart", "heart_wax", "prestige", "prestige_vip",
            "prestige_wax", "season_candle", "season_wax", "season_wax_14",
            "season_heart", "season_pass_token", "vip", "wax",
        ],
        help="货币字段名",
    )
    p_u_curr.add_argument("--amount", type=int, required=True, help="增加的数量（可为负数）")

    p_u_unlock = user_sub.add_parser("unlock-all", help="解锁指定用户的全物品")
    p_u_unlock.add_argument("user_id", type=int)

    args = parser.parse_args()

    if args.command == "start":
        for k in resolve_targets(args.target):
            start_service(k)
    elif args.command == "stop":
        for k in resolve_targets(args.target):
            stop_service(k)
    elif args.command == "restart":
        for k in resolve_targets(args.target):
            stop_service(k)
            start_service(k)
    elif args.command == "status":
        for k in resolve_targets(args.target):
            status_service(k)
    elif args.command == "logs":
        tail_logs(args.target, args.lines, args.follow)
    elif args.command == "db":
        if args.action == "tables":
            list_tables()
        elif args.action == "users":
            list_users(args.limit)
    elif args.command == "user":
        if args.user_action == "list":
            list_users(args.limit)
        elif args.user_action == "info":
            user_info(args.user_id)
        elif args.user_action == "add-currency":
            add_currency(args.user_id, args.type, args.amount)
        elif args.user_action == "unlock-all":
            unlock_all_items(args.user_id)


if __name__ == "__main__":
    main()
