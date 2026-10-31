# -*- coding: utf-8 -*-
"""
纸船关注辅助 (高版本 > 0.26.5 新增接口共用)

/service/relationship/api/v1/{add_follow,set_follow_name,delete_follow}
记录"用户关注某纸船作者(creator)"及昵称。用轻量表 boat_follow 存储,
表不存在则自动创建(幂等)。
"""
import threading
from sqlalchemy import text

_lock = threading.Lock()


def _ensure_table(engine):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS boat_follow (
                    user_id TEXT NOT NULL,
                    creator TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    last_modified TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, creator)
                )
            """))
    except Exception:
        pass


def _creator_base(creator):
    """去掉 ":<hash>" 后缀, 只留用户ID。"""
    return (creator or "").split(":")[0]


def _now_str():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def add_follow(user_id, creator, nickname=""):
    """新增/更新关注记录, 返回 (creator_base, nickname, last_modified)。"""
    from infrastructure.database.models import db
    engine = db.engine
    _ensure_table(engine)
    cbase = _creator_base(creator)
    nick = nickname or ""
    lm = _now_str()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO boat_follow (user_id, creator, nickname, last_modified)
                VALUES (:u, :c, :n, :l)
                ON CONFLICT(user_id, creator) DO UPDATE SET
                    nickname=excluded.nickname, last_modified=excluded.last_modified
            """), {"u": user_id, "c": cbase, "n": nick, "l": lm})
    except Exception:
        pass
    return cbase, nick, lm


def set_follow_name(user_id, creator, nickname):
    """更新关注昵称, 返回 (creator_base, nickname, last_modified); 无记录则补建。"""
    return add_follow(user_id, creator, nickname)


def delete_follow(user_id, creator):
    """删除关注记录, 返回删除条数。"""
    from infrastructure.database.models import db
    engine = db.engine
    _ensure_table(engine)
    cbase = _creator_base(creator)
    try:
        with engine.begin() as conn:
            r = conn.execute(text(
                "DELETE FROM boat_follow WHERE user_id=:u AND creator=:c"),
                {"u": user_id, "c": cbase})
            return r.rowcount or 0
    except Exception:
        return 0
