# -*- coding: utf-8 -*-
"""
接口: /account/consumable/get_consumables1  (与 /account/consumable/get_consumables 等价)
函数: get_consumables1()

查询玩家背包里的魔法(消耗品)。响应 = magic_helper.consumables_response(user_id), 条目
形状由协议层唯一确定(客户端 sub_3FACD8):
    {"get_consumables": [{"consumable_id": int, "cooldown_until": int, "quantity": int}, ...]}
旧实现写死返回 {"get_consumables": []} —— 玩家在先祖树买的/宝箱开的魔法在背包里永远是
空的, 客户端与服务端对不上, 魔法界面异常/闪退。

鉴权保持**宽松**(老代码完全不校验): 带 session 就校验, 不带就只按 user 查 —— 这样
管理脚本/工具不受影响, 而"会话过期"的客户端能拿到 401 而不是别人的背包。
"""
from flask import Response
import json
import helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def get_consumables1():
    data = magic_helper.request_payload()
    user_id = magic_helper.body_user_id(data)
    session = data.get("session")
    if not user_id:
        return _resp({"error": "Invalid request body"}, 400)
    if session and not magic_helper.session_ok(user_id, session):
        return _resp({"error": "Invalid session or user not found"}, 401)
    return _resp(magic_helper.consumables_response(user_id))
