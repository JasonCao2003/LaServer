# -*- coding: utf-8 -*-
"""
Service接口: /service/status/api/v1/get_unlocks
按账号返回开门/开图状态。新号只有基础(config/all_unlock_status.json),
管理层推送的"开全图"(kaiquantu.json)写入 StatusUnlock 表后并入返回。
"""
from paths import CONFIG_DIR, SERVICE_ROOT

from flask import request, jsonify, Response
from infrastructure.database.models import db, User, StatusUnlock
import json
import logging
import os

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = SERVICE_ROOT


def _inject_shared_space_props(items):
    """把共享空间道具(CharSkyKid_Prop_*)并进 status_unlocks。

    只注入到响应里, 不写库(见 shared_space_props.py 的说明)。失败只记日志 ——
    注意别用 `except: pass` 吞掉: 之前静默失败过一次, 接口照常回 200 但道具没了,
    查了半天(服务器日志是 GBK, 中文日志行会乱码, 这里一律写英文)。
    """
    try:
        from business.helpers.shared_space_props import inject
        return inject(items)
    except Exception as e:
        logger.warning("shared_space_props inject failed: %s", e, exc_info=True)
        return 0


def _basic_status():
    try:
        with open(os.path.join(CONFIG_DIR, "all_unlock_status.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        return d.get("status_unlocks", d) if isinstance(d, dict) else d
    except Exception:
        return []


def get_unlocks_status():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        if not user_id or not session:
            basics = _basic_status()
            # 共享空间道具(没有会话也给, 客户端拿道具列表建"物品放置栏")
            _inject_shared_space_props(basics)
            return jsonify({"status_unlocks": basics,
                            "status_unlocks_total_count": len(basics)})
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}),
                            401, mimetype="application/json")

        # 新建账号全解锁: 开启后, 开启时间点之后新建的账号拉取数据时直接返回完整解锁
        auto_full = False
        try:
            from business.helpers.full_unlock_helper import should_auto_full_unlock, full_status_unlocks
            auto_full = should_auto_full_unlock(user.created_at)
        except Exception:
            auto_full = False

        merged = {}
        if auto_full:
            # 直接发送准备好的开全门/全图 JSON(config/kaiquantu.json)
            for s in full_status_unlocks():
                nm = s.get("name")
                if nm:
                    merged[nm] = s
        for s in _basic_status():
            nm = s.get("name")
            if nm:
                merged.setdefault(nm, s)
        for row in StatusUnlock.query.filter_by(user_id=user_id).all():
            merged[row.name] = {
                "name": row.name,
                "type": row.unlock_type,
                "ack": bool(row.ack),
                "unlocked_at": row.unlocked_at,
            }
        # 过滤客户端已删除(墓碑)的状态解锁: 让删除真正生效
        try:
            from business.helpers.status_unlock_tombstone import deleted_names
            _del = deleted_names(user_id)
            if _del:
                merged = {k: v for k, v in merged.items() if k not in _del}
        except Exception:
            pass
        items = list(merged.values())
        # 共享空间道具(CharSkyKid_Prop_*): 官方这个接口里就带两百多条 type="level" 的
        # 道具, 本服原来一条都没有 -> 共享空间"物品放置栏"是空的(用户反馈"打不开")。
        # 只注入到响应里, 不写库(见 shared_space_props.py 的说明)。
        _inject_shared_space_props(items)
        return jsonify({"status_unlocks": items,
                        "status_unlocks_total_count": len(items)})
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500,
                        mimetype="application/json")
