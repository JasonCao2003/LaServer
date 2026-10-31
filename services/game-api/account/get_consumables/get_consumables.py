# -*- coding: utf-8 -*-
"""
接口: /account/consumable/get_consumables
函数: get_consumables()

查询玩家背包里的魔法(消耗品)。与 get_consumables1 同义(不同客户端版本走不同路径),
共用 magic_helper.consumables_response。旧实现同样写死空列表。
"""
from flask import Response
import json
import helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def get_consumables():
    data = magic_helper.request_payload()
    user_id = magic_helper.body_user_id(data)
    session = data.get("session")
    if not user_id:
        return _resp({"error": "Invalid request body"}, 400)
    if session and not magic_helper.session_ok(user_id, session):
        return _resp({"error": "Invalid session or user not found"}, 401)
    return _resp(magic_helper.consumables_response(user_id))
