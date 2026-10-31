# -*- coding: utf-8 -*-
"""
接口: /account/buff/remove_buff1  (别名 /account/buff/remove_buff)
函数: remove_buff1()

移除一个 buff。请求体(0.20.0 反编译客户端 sub_52678C / 官方 accountRemoveBuffRequest):
    {"user":..., "session":..., "buff_id": <buff id 或 buff 名字>, "giver":...}
响应(客户端 sub_52678C 的解析 + 官方抓包 static_responses/account__buff__remove_buff1.json):
    {"get_buffs":[...], "get_buffs_sign":..., "remove_buff":"success"}

旧服务端**没有这个路由** -> Flask 返回 HTML 404 -> 客户端解析不了 -> 弹"非法请求。
请重试。"(字符串 error_login_request), 严重时闪退。
remove_buff 的值**永远回 success**(官方 removeBuff 找不到目标也回 success; 客户端拿到
"invalid" 会打警告日志), 由 magic_helper.remove_buff_response 保证。
"""
from flask import Response
import json
import helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype="application/json")


def remove_buff1():
    data = magic_helper.request_payload()
    user_id = magic_helper.body_user_id(data)
    session = data.get("session")
    if not user_id or not session:
        return _resp({"error": "Invalid request body"}, 400)
    if not magic_helper.session_ok(user_id, session):
        return _resp({"error": "Invalid session or user not found"}, 401)

    # 客户端固定发 buff_id(数字 id); 兼容工具/脚本发 id / buff_name 的情况。
    identifier = data.get("buff_id")
    if identifier in (None, ""):
        identifier = data.get("id")
    if identifier in (None, ""):
        identifier = data.get("buff_name")
    magic_helper.remove_buff(user_id, identifier)
    return _resp(magic_helper.remove_buff_response(user_id))
