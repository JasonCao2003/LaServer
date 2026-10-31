# -*- coding: utf-8 -*-
"""
响应覆盖 (Response Override) 机制

允许管理员针对 "某用户 + 某接口路径" 设置自定义响应体。
命中时, 中间件在 after_request 阶段把真实响应体替换为管理员保存的内容,
从而玩家实际收到的是修改后的响应。

存储: instance/users.db 的 response_override 表
  user_id  TEXT   用户ID (空字符串表示对所有用户生效)
  path     TEXT   请求路径, 如 /account/get_currency
  body     TEXT   覆盖的响应体(通常是 JSON 字符串)
  enabled  INTEGER 1=启用
  updated_at TEXT
唯一键: (user_id, path)
"""
from paths import DATA_DIR, SERVICE_ROOT
import os
import json
import sqlite3
import threading

_lock = threading.Lock()


def _db_path():
    root = SERVICE_ROOT
    return os.path.join(DATA_DIR, "instance", "users.db")


def _conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table():
    try:
        with _lock:
            conn = _conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS response_override (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT,
                    UNIQUE(user_id, path)
                )
            """)
            conn.commit()
            conn.close()
    except Exception:
        pass


def get_override(user_id, path):
    """返回该用户该路径的启用中覆盖体(str); 无则 None。
    优先匹配具体用户, 其次匹配通配(user_id='')。"""
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT body FROM response_override WHERE user_id=? AND path=? AND enabled=1",
            (user_id or "", path)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT body FROM response_override WHERE user_id='' AND path=? AND enabled=1",
                (path,)).fetchone()
        conn.close()
        return row["body"] if row else None
    except Exception:
        return None


def merge_status_unlocks_override(user_id, override_body):
    """针对 /service/status/api/v1/get_unlocks 的覆盖体做"活数据合并"。

    问题: 覆盖体是历史某刻(如赛季25)导出的静态 status_unlocks 快照,
    不含之后新赛季客户端通过 add_unlock 写入的开场标记(如 AP28Intro_01)。
    结果: 客户端每次进游戏拿到旧快照, 看不到"本赛季开场已看过", 于是循环重播开场动画。

    修复: 把该用户 status_unlock 表里的实时记录并入覆盖体(按 name 去重, DB 优先),
    这样新赛季的开场/剧情标记能被客户端读到, 开场动画只播一次。

    override_body: 覆盖体字符串(通常是 JSON)。返回合并后的 JSON 字符串;
    任何异常都回退返回原始 override_body(宁可用旧快照, 也不 500)。
    """
    if not user_id or not override_body:
        return override_body
    try:
        data = json.loads(override_body)
    except Exception:
        return override_body
    if not isinstance(data, dict) or "status_unlocks" not in data:
        return override_body
    try:
        merged = {}
        for it in data.get("status_unlocks") or []:
            nm = it.get("name")
            if nm:
                merged[nm] = it
        conn = _conn()
        rows = conn.execute(
            "SELECT name, unlock_type, ack, unlocked_at FROM status_unlock WHERE user_id=?",
            (user_id,)).fetchall()
        conn.close()
        for r in rows:
            nm = r["name"]
            if not nm:
                continue
            merged[nm] = {
                "name": nm,
                "type": r["unlock_type"] or "level",
                "ack": bool(r["ack"]),
                "unlocked_at": r["unlocked_at"] or 0,
            }
        # 跳过客户端已删除(墓碑)的状态解锁, 让删除在覆盖响应里也真正生效
        try:
            from business.helpers.status_unlock_tombstone import deleted_names
            _del = deleted_names(user_id)
            if _del:
                merged = {k: v for k, v in merged.items() if k not in _del}
        except Exception:
            pass
        items = list(merged.values())
        data["status_unlocks"] = items
        data["status_unlocks_total_count"] = len(items)
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return override_body


def set_override(user_id, path, body, enabled=True):
    import datetime
    ensure_table()
    with _lock:
        conn = _conn()
        conn.execute("""
            INSERT INTO response_override (user_id, path, body, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, path) DO UPDATE SET
                body=excluded.body, enabled=excluded.enabled, updated_at=excluded.updated_at
        """, (user_id or "", path, body, 1 if enabled else 0,
              datetime.datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()


def delete_override(user_id, path):
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM response_override WHERE user_id=? AND path=?",
                     (user_id or "", path))
        conn.commit()
        conn.close()


def list_overrides(user_id=None):
    try:
        conn = _conn()
        if user_id is None:
            rows = conn.execute("SELECT * FROM response_override ORDER BY path").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM response_override WHERE user_id IN (?, '') ORDER BY path",
                (user_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ------------------ 请求日志 (供面板按用户查看接口/响应) ------------------
def ensure_log_table():
    try:
        with _lock:
            conn = _conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    method TEXT,
                    status_code INTEGER,
                    body_resp TEXT,
                    updated_at TEXT,
                    UNIQUE(user_id, path)
                )
            """)
            # 账号活跃表: 记录每个账号最后一次请求的时间(unix秒),
            # 供管理面板按"5分钟内有无请求"判定在线/离线状态。
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_activity (
                    user_id TEXT PRIMARY KEY,
                    last_seen INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
    except Exception:
        pass


def touch_user(user_id):
    """更新账号最后活跃时间为当前(每次请求调用)。空 user_id 忽略。"""
    if not user_id:
        return
    import time as _t
    try:
        now = int(_t.time())
        with _lock:
            conn = _conn()
            conn.execute("""
                INSERT INTO user_activity (user_id, last_seen) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen
            """, (user_id, now))
            conn.commit()
            conn.close()
    except Exception:
        pass


def record_request(user_id, path, method, status_code, body_resp):
    """记录(用户,路径)最近一次的响应, 同键覆盖更新。"""
    import datetime
    try:
        if isinstance(body_resp, bytes):
            body_resp = body_resp.decode("utf-8", errors="replace")
        with _lock:
            conn = _conn()
            conn.execute("""
                INSERT INTO request_log (user_id, path, method, status_code, body_resp, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, path) DO UPDATE SET
                    method=excluded.method, status_code=excluded.status_code,
                    body_resp=excluded.body_resp, updated_at=excluded.updated_at
            """, (user_id or "", path, method, status_code, body_resp[:65536],
                  datetime.datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            conn.close()
    except Exception:
        pass


def list_user_requests(user_id):
    """返回某用户请求过的所有接口及最近响应。"""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM request_log WHERE user_id=? ORDER BY path", (user_id or "",)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def extract_user_id_from_request(req_body_bytes):
    """从请求体(bytes)中解析 user 字段。失败返回空串。"""
    if not req_body_bytes:
        return ""
    try:
        data = json.loads(req_body_bytes.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            return data.get("user") or data.get("user_id") or ""
    except Exception:
        pass
    return ""
