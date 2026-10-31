# -*- coding: utf-8 -*-
"""
接口: /account/get_collectibles
按账号返回收藏品(动作 emote)。新号只有基础动作(config/collectibles.json),
管理层推送的全动作写入 Collectible 表后, 这里会把库里的并入返回。
"""
from paths import CONFIG_DIR, SERVICE_ROOT
from flask import request, Response
from infrastructure.database.models import db, User, Collectible
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = SERVICE_ROOT


def _basic_collectibles():
    try:
        with open(os.path.join(CONFIG_DIR, "collectibles.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        return d.get("collectibles", d) if isinstance(d, dict) else d
    except Exception:
        return []


def collectible_query(user_id):
    """查该账号的收藏品 —— **只加载必要列, 不加载 created_at/updated_at**。

    2026-10-01 玩家反馈"接口到某个就连接失败": 管理层"推送全解锁/全动作"是用裸 SQL
    写的 collectible 表, created_at/updated_at 直接塞了 int(time.time()); 而模型里
    这两列声明的是 DateTime, ORM 取**整行**时 SQLAlchemy 会调 str_to_datetime ->
        TypeError: fromisoformat: argument must be str
    -> /account/get_collectibles 一路 500(客户端退避重试 8 次后放弃, 登录中断)。
    响应里本来就不需要这两个时间列, 所以这里用 load_only 把它们排除在 SELECT 之外
    (deferred), 从根上避开这行脏数据。

    根治在 models.Collectible(已改用 FlexibleDateTime, 同时兼容 int 秒和文本),
    但"热更新接口"**不会重载 models**(models.db 是进程级单例, 重载会换掉 SQLAlchemy
    实例, 风险太大) —— 接口层再兜一道, 点一次热更新即可生效, 不必重启服务器。
    """
    from sqlalchemy.orm import load_only
    return (Collectible.query
            .options(load_only(*[getattr(Collectible, n)
                                 for n in ("id", "used", "candle_space",
                                           "carrying", "level", "permanent_level")]))
            .filter_by(user_id=user_id))


def get_collectibles():
    try:
        data = request.get_json(force=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        if not user_id or not session:
            return Response(json.dumps({"collectibles": _basic_collectibles()},
                                       ensure_ascii=False),
                            200, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}),
                            401, mimetype="application/json")

        # 新建账号全解锁: 开启后, 开启时间点之后新建的账号拉取数据时直接返回完整解锁
        auto_full = False
        try:
            from business.helpers.full_unlock_helper import should_auto_full_unlock, full_collectibles
            auto_full = should_auto_full_unlock(user.created_at)
        except Exception:
            auto_full = False

        merged = {}
        if auto_full:
            # 直接发送准备好的全动作 JSON(config/quandongzuo.json)
            for c in full_collectibles():
                cid = c.get("id")
                if cid:
                    merged[cid] = c
        for c in _basic_collectibles():
            cid = c.get("id")
            if cid:
                merged.setdefault(cid, c)
        for row in collectible_query(user_id).all():
            merged[row.id] = {
                "id": row.id,
                "used": bool(row.used),
                "candle_space": bool(row.candle_space),
                "carrying": bool(row.carrying),
                "level": row.level,
                "permanent_level": row.permanent_level,
            }
        return Response(json.dumps({"collectibles": list(merged.values())},
                                   ensure_ascii=False),
                        200, mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500,
                        mimetype="application/json")
