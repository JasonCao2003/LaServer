# -*- coding: utf-8 -*-
"""
接口: /account/buff/get_buffs
函数: get_buffs()

与 /account/buff/get_buffs1 等价(老客户端走这个路径), 响应同样由
magic_helper.buffs_response 组。旧实现写死空列表 + 全 0 签名, 玩家身上的 buff 永远
同步不过去。

鉴权保持**宽松**: 只有"带了 session 但 session 不对"才回 401 —— 这条老路径上有
工具/脚本只发 user 不发 session, 收紧会让它们从"能拿到 buff"变成报错。
"""
from flask import Response
import json
import business.helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype="application/json")


def get_buffs():
    data = magic_helper.request_payload()
    user_id = magic_helper.body_user_id(data)
    session = data.get("session")
    if not user_id:
        return _resp({"error": "Invalid request body"}, 400)
    if session and not magic_helper.session_ok(user_id, session):
        return _resp({"error": "Invalid session or user not found"}, 401)
    return _resp(magic_helper.buffs_response(user_id))
