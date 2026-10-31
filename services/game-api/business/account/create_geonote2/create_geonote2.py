# -*- coding: utf-8 -*-
"""任务留言创建: /account/geonote/create_geonote2 与 create_bug_report_geonote。

旧实现没有这两个接口 -> 客户端写完留言什么都不存, 神龛/任务点看不到任何留言。
存到 instance/geonotes.json, 返回体同时给新版 {"result","geonote"} 与旧版
{"geonotes","likes","emojis","social"} 两份形状。
"""

import json
import re
import time

from flask import Response, request

import business.helpers.geonote_store as geonote_store
from infrastructure.database.models import User

_POS = re.compile(r"[\-\d.]+")


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _parse_pos(data):
    for key in ("pos", "position", "location"):
        raw = data.get(key)
        if isinstance(raw, str):
            nums = [float(x) for x in _POS.findall(raw)[:3]]
            if len(nums) == 3:
                return nums
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            return list(raw[:3])
    try:
        return [float(data.get("pos_x") or 0), float(data.get("pos_y") or 0),
                float(data.get("pos_z") or 0)]
    except (TypeError, ValueError):
        return [0, 0, 0]


def _create(bug=False):
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        if not user_id or not session:
            return _resp({"result": "invalid_params"}, 400)
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _resp({"result": "invalid_session"}, 401)

        message = (data.get("message") or data.get("msg")
                   or data.get("content") or data.get("text") or "")
        level_id = data.get("level_id") or 0
        note = geonote_store.create(
            author=user_id,
            author_name=data.get("author_name") or data.get("name")
            or data.get("creator_nickname") or "",
            message=message,
            pool_type=data.get("pool_type") or "",
            pool_name=data.get("pool_name") or "",
            level_id=level_id,
            level_hash=data.get("level_hash") or 0,
            pos=_parse_pos(data),
            parent_guid=data.get("parent_guid") or 0,
            parent_rel_pos=data.get("parent_rel_pos"),
            cam_dir=data.get("cam_dir"),
            bug=bug,
            extra={"server_time": int(time.time())},
        )
        if note is None:
            return _resp({"result": "error", "geonote": {}})
        # 新旧两种响应形状合并: 旧版读 geonotes/likes/social, 新版读 geonote。
        body = geonote_store.payload(user_id, level_id=level_id, bug=bug)
        body.update({"result": "ok", "geonote": note})
        return _resp(body)
    except Exception as exc:  # noqa: BLE001
        return _resp({"result": "error", "geonote": {}, "error": str(exc)})


def create_geonote2():
    return _create(bug=False)


def create_bug_report_geonote():
    return _create(bug=True)
