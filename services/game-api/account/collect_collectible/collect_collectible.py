# -*- coding: utf-8 -*-
# /account/collect_collectible - collect emote/collectible (action)
# 版本分流由全局 static_responses 钩子统一处理:
#   >=0.30.0 走接口库静态响应, 低版本走下面的原动态逻辑 (旧版不受影响)。
from flask import request, Response
from database.models import db, User, Collectible
import json, os

# 只加载必要列的查询(不碰 created_at/updated_at) —— 详见下面的说明。
# 这里**自带一份**而不是从 get_collectibles 里 import: 热更新是按 sys.modules 顺序
# 逐个 reload 的, 跨模块 import 有可能拿到还没重载的旧模块(旧模块里没有这个函数),
# 那样这整个模块会 reload 失败并静默保留旧代码。
_LOAD_COLS = ("id", "used", "candle_space", "carrying", "level", "permanent_level")


def collectible_query(user_id):
    """查该账号的收藏品 —— **不加载 created_at/updated_at**。

    2026-10-01: 面板"推送全动作"用裸 SQL 写 collectible, created_at/updated_at 存的是
    int 秒; 模型里这两列是 DateTime -> ORM 取整行时 SQLAlchemy 的 str_to_datetime 抛
    `TypeError: fromisoformat: argument must be str`, 接口整个 500(客户端重试到放弃,
    表现为"接口到某个就连接失败")。响应里用不到这两个时间列, load_only 排除即可。
    (根治见 models.FlexibleDateTime; 但热更新不重载 models, 所以接口层再兜一道。)
    """
    from sqlalchemy.orm import load_only
    return (Collectible.query
            .options(load_only(*[getattr(Collectible, n) for n in _LOAD_COLS]))
            .filter_by(user_id=user_id))

def _default_collectibles():
    """default collectibles from config (flame/home/normal/sit/skykid etc)"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = os.path.join(root, "config", "collectibles.json")
    try:
        return json.load(open(cfg, encoding="utf-8")).get("collectibles", [])
    except Exception:
        return []

def collect_collectible():
    try:
        data = request.get_json(force=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        if not user_id or not session:
            return Response(json.dumps({"error": "invalid request"}), 400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}), 401, mimetype="application/json")

        name = data.get("name")
        carrying = bool(data.get("carrying", False))

        # ensure user has default collectibles (first time)
        # 用 collectible_query(load_only): 不加载 created_at/updated_at, 详见其注释
        # (面板裸 SQL 写进去的 int 时间戳会让 ORM 取整行时抛 fromisoformat)。
        existing = {c.id: c for c in collectible_query(user_id).all()}
        if not existing:
            for dc in _default_collectibles():
                c = Collectible(
                    user_id=user_id, id=dc.get("id"),
                    used=dc.get("used", False),
                    candle_space=dc.get("candle_space", False),
                    carrying=dc.get("carrying", False),
                    level=dc.get("level", 0),
                    permanent_level=dc.get("permanent_level", 0))
                db.session.add(c)
                existing[c.id] = c
            db.session.commit()

        # collect the requested action: level up, set carrying
        if name:
            c = existing.get(name)
            if not c:
                c = Collectible(user_id=user_id, id=name, used=False,
                                candle_space=False, carrying=carrying,
                                level=1, permanent_level=1)
                db.session.add(c)
                existing[name] = c
            else:
                c.level = (c.level or 0) + 1
                if c.permanent_level < 1:
                    c.permanent_level = 1
                c.carrying = carrying
            db.session.commit()

        # return all collectibles
        result = []
        for c in collectible_query(user_id).all():
            result.append({
                "candle_space": bool(c.candle_space),
                "carrying": bool(c.carrying),
                "id": c.id,
                "level": c.level or 0,
                "permanent_level": c.permanent_level or 0,
                "used": bool(c.used),
            })
        return Response(json.dumps({"collectibles": result}, ensure_ascii=False), 200, mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500, mimetype="application/json")
