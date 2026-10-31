# -*- coding: utf-8 -*-
"""
染色辅助

高版本(> 0.26.5)染色功能: /account/purchase_dye 与
/service/inventory/api/v1/unlocks/remove_dye 共用。

UnlockItem 表没有染色的字段, 这里用一张轻量表 item_dye 记录每个
"用户+物品"当前染色方案, 表不存在则自动创建(幂等)。
"""
import threading
from sqlalchemy import text

_lock = threading.Lock()


def _ensure_table():
    """确保 item_dye 表存在(与当前 db.session 同事务)。"""
    from database.models import db
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS item_dye (
                user_id TEXT NOT NULL,
                unlock_name TEXT NOT NULL,
                upgrade_dye TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, unlock_name)
            )
        """))
    except Exception:
        db.session.rollback()


def set_dye(user_id, unlock_name, upgrade_dye):
    """写入/覆盖某用户某物品的染色方案。调用方负责 commit。"""
    from database.models import db
    import time as _t
    _ensure_table()
    now = int(_t.time())
    try:
        db.session.execute(text("""
            INSERT INTO item_dye (user_id, unlock_name, upgrade_dye, created_at, updated_at)
            VALUES (:u, :n, :d, :c, :c)
            ON CONFLICT(user_id, unlock_name) DO UPDATE SET
                upgrade_dye=excluded.upgrade_dye, updated_at=excluded.updated_at
        """), {"u": user_id, "n": unlock_name, "d": upgrade_dye, "c": now})
    except Exception:
        db.session.rollback()


def remove_dye(user_id, unlock_name):
    """删除某用户某物品的染色方案。调用方负责 commit。"""
    from database.models import db
    _ensure_table()
    try:
        db.session.execute(text(
            "DELETE FROM item_dye WHERE user_id=:u AND unlock_name=:n"),
            {"u": user_id, "n": unlock_name})
    except Exception:
        db.session.rollback()


def get_dyes(user_id):
    """返回某用户全部染色方案 dict: {unlock_name: upgrade_dye}。"""
    from database.models import db
    _ensure_table()
    try:
        rows = db.session.execute(text(
            "SELECT unlock_name, upgrade_dye FROM item_dye WHERE user_id=:u"),
            {"u": user_id}).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        db.session.rollback()
        return {}
