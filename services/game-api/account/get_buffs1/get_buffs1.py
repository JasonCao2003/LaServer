# -*- coding: utf-8 -*-
"""
接口: /account/buff/get_buffs1
函数: get_buffs1()

查询当前生效的 buff 列表。响应 = magic_helper.buffs_response(user_id), 形状由协议层
唯一确定(客户端 sub_52372C 按固定 4 个键解析):
    {"get_buffs": [{"active_until", "buff_id", "cooldown_until", "giver_user_id"}],
     "get_buffs_sign": "..."}
旧实现返回 {buff_id, buff_type, value, duration, expires_at(ISO字符串)} —— 客户端
找不到 active_until/cooldown_until、buff_id 还是字符串, 玩家身上一有 buff 就异常。
"""
from flask import Response
import json
import helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype="application/json")


def get_buffs1():
    data = magic_helper.request_payload()
    user_id = magic_helper.body_user_id(data)
    session = data.get("session")
    if not user_id or not session:
        return _resp({"error": "Invalid request body"}, 400)
    if not magic_helper.session_ok(user_id, session):
        return _resp({"error": "Invalid session or user not found"}, 401)
    return _resp(magic_helper.buffs_response(user_id))
