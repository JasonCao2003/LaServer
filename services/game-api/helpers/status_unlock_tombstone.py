# -*- coding: utf-8 -*-
"""
状态解锁删除墓碑 (status_unlock_deleted 表) 辅助模块

新建账号全解锁(auto_full_unlock)在 get_unlocks 响应里直接注入 config/kaiquantu.json,
这些解锁不写 status_unlock 表。客户端 delete_unlock 删掉某条时, 只删表里已有的行不够
—— 注入的数据下次请求还会回来。这里用一张墓碑表记录"该用户已删除的名字":
  - get_unlocks 组装响应时跳过墓碑名 -> 删除真正生效
  - add_unlock 重新解锁时清除墓碑   -> 可再次出现
  - response_override 的 get_unlocks 合并也跳过墓碑名
"""
import os
import sqlite3


def _db_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "instance", "users.db")


def _conn():
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS status_unlock_deleted (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id VARCHAR(36) NOT NULL,
            name VARCHAR(100) NOT NULL,
            deleted_at INTEGER NOT NULL,
            UNIQUE(user_id, name)
        )
    """)
    return conn


def deleted_names(user_id):
    """返回该用户已删除的状态解锁名集合。失败返回空集。"""
    if not user_id:
        return set()
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT name FROM status_unlock_deleted WHERE user_id=?",
            (user_id,)).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def tombstone(user_id, name):
    """记录一次删除。已存在则不动。失败静默(不阻塞删除主流程)。"""
    if not user_id or not name:
        return
    import time as _t
    try:
        conn = _conn()
        conn.execute(
            "INSERT OR IGNORE INTO status_unlock_deleted (user_id, name, deleted_at)"
            " VALUES (?,?,?)",
            (user_id, name, int(_t.time())))
        conn.commit()
        conn.close()
    except Exception:
        pass


def clear_tombstone(user_id, name):
    """重新解锁时清除墓碑, 让该名字重新出现在响应里。"""
    if not user_id or not name:
        return
    try:
        conn = _conn()
        conn.execute(
            "DELETE FROM status_unlock_deleted WHERE user_id=? AND name=?",
            (user_id, name))
        conn.commit()
        conn.close()
    except Exception:
        pass
