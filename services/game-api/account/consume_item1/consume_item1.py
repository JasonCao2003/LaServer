# -*- coding: utf-8 -*-
"""
接口: /account/consumable/consume_item1
函数: consume_item1()

使用背包里的一件魔法(消耗品)。**协议形状/单位/语义全部在 magic_helper 的"协议层"**
(见 magic_helper.py 里 DEF_MINUTE / consume_consumable / consume_response 的说明),
这个 handler 只做三件事: 取参数 -> 鉴权 -> 把结果交给 magic_helper 组响应。

请求体(0.20.0 反编译客户端 sub_3FC638 / 官方 accountConsumeItemRequest.deserialize):
{user, session, consumable_id, consumable_count, receiver_uuid}

响应体(客户端 sub_3FC974 + 官方抓包 static_responses/account__consumable__consume_item1.json):
{
  "consume_item": "success",          # 客户端 sub_3FA684; 非 success 时**跳过**下面两项
  "get_buffs": [{"active_until","buff_id","cooldown_until","giver_user_id"}],
  "get_buffs_sign": "...",
  "get_consumables": [{"consumable_id","cooldown_until","quantity"}],   # 回显被消耗的那件
  "shared_results": [],               # 条目形状 {uuid, result}; 官方与参考实现都忽略
  "new_height": 1.0                   # 浮点; 只有 resize_potion 会变
}

本次(2026-09-19)修掉的坑:
  * 定义表的 cooldown 是**分钟**, 旧实现当秒用 -> 冷却 15 秒而不是 15 分钟;
  * 库里没有/已用完的魔法旧实现照样挂 buff -> "没拥有过也能用"(管理员只给一个号发的
    魔法, 别的号照样能用); 现在按参考实现直接 failed;
  * 认不出的 consumable_id(定义表里没有)现在直接 failed, 不再凭空挂 buff。
"""
from flask import Response
import json
import helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype="application/json")


def consume_item1():
    data = magic_helper.request_payload()
    user_id = magic_helper.body_user_id(data)
    session = data.get("session")
    if not user_id or not session:
        return _resp({"error": "Invalid request body"}, 400)
    if not magic_helper.session_ok(user_id, session):
        return _resp({"error": "Invalid session or user not found"}, 401)

    # consumable_id 可能是数字 id, 也可能是名字 / 别的客户端用别的键名。
    consumable_id = magic_helper.resolve_consumable_id(
        data.get("consumable_id"), data.get("consumable_name"),
        data.get("item_name"), data.get("name"))
    try:
        count = int(data.get("consumable_count") or 1)
    except (TypeError, ValueError):
        count = 1

    try:
        status, _reason = magic_helper.consume_consumable(
            user_id, consumable_id, count=count, giver=user_id)
    except Exception:
        # 兜底: 任何异常都回一个**形状正确**的 failed 响应(客户端收到非 2xx 会弹
        # "非法请求。请重试。"), 玩家看不到报错。
        try:
            from database.models import db
            db.session.rollback()
        except Exception:
            pass
        status = magic_helper.CONSUME_FAILED

    return _resp(magic_helper.consume_response(user_id, status=status,
                                               consumable_id=consumable_id))
