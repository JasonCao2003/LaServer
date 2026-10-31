# -*- coding: utf-8 -*-
"""
接口: /account/purchase_event_buff
函数: purchase_event_buff()

购买/领取"活动 buff"(活动期间用活动货币换的限时效果)。

**响应形状的证据**(官方抓包库里没有这个接口, 只能取参考实现):
  * 十二的光遇服务器 v3.39 `account0322StubRequests.js`:
      AccountPurchaseEventBuffRequest = makeWriteRequest("/account/purchase_event_buff",
                                      () => ({ "result": "ok", "update_buffs": [] }))
  * 0.33.7 客户端 `libBootloader0337.so.c` 里只有路径串(sub_D4B364), 请求是结构化参数
    (不是 JSON 键值), 响应键名读不出来 —— 所以**不擅自发明**响应键:
      - result / update_buffs 按参考实现原样回;
      - update_buffs 一律**空表**(参考实现就是空表; 它的元素形状无法证实, 塞东西进去
        有解析风险);
      - 额外带上 get_buffs / get_buffs_sign(形状有客户端 sub_52372C 与官方抓包双重
        背书), 这样客户端即使只认 get_buffs 也能立刻同步到刚拿到的那件活动 buff。
    "purchase_event_buff": "success" 这个键是旧实现自己发明的, 已去掉。

原问题: 客户端会请求这个路径, 但老私服**没有该路由** -> Flask HTML 404 -> 客户端解析
不了 -> 弹"非法请求。\n请重试。"(字符串 error_login_request), 严重时闪退。

因为服务端已经把节日/活动全关(config/game_control.json events.enabled=false), 这里只
负责"别让客户端因为 404 报错": 能解析出 buff 就挂上, 解析不出也回 200 成功(不扣货币)。
"""
from flask import Response
from database.models import db, User
import json
import logging
import helpers.magic_helper as magic_helper

logger = logging.getLogger(__name__)


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype="application/json")


def _error_body(user_id, reason):
    """形状正确的**失败**响应(result != ok), 客户端不会当解析错误。"""
    body = {"result": "error", "update_buffs": [], "error": reason}
    if user_id:
        body.update(magic_helper.buffs_response(user_id))
    else:
        body["get_buffs"], body["get_buffs_sign"] = [], ""
    return body


def _resolve_buff_id(data):
    """把请求体里各种键名解析成 buff 定义表里真实存在的 buff_id(0 = 没解析出来)。

    客户端/参考实现里这个接口传的是"活动 buff"的 id, 但不同客户端键名不一, 所以按
    buff_id -> event_buff_id -> buff_name -> item_name -> name 依次试, 最后再拿
    magic 定义表(buff_id 字段)兜一层。
    """
    identifier = None
    for key in ("buff_id", "event_buff_id", "buff_name", "item_name", "name"):
        if data.get(key) not in (None, ""):
            identifier = data.get(key)
            break
    if identifier is None:
        return 0
    bid = magic_helper.to_int(identifier)
    if bid and magic_helper.buff_def(bid) is not None:
        return bid
    if isinstance(identifier, str):
        d = magic_helper.buff_def_by_name(identifier)
        if d:
            return magic_helper.to_int(d.get("id"))
    cdef = magic_helper.consumable_def(magic_helper.resolve_consumable_id(identifier)) or {}
    bid = magic_helper.to_int(cdef.get("buff_id"))
    return bid if magic_helper.buff_def(bid) is not None else 0


def purchase_event_buff():
    try:
        data = magic_helper.request_payload()
        user_id = magic_helper.body_user_id(data)
        session = data.get("session")
        if not user_id or not session:
            return _resp(_error_body(user_id, "Invalid request body"))
        if not magic_helper.session_ok(user_id, session):
            return _resp(_error_body(user_id, "Invalid session or user not found"))

        buff_id = _resolve_buff_id(data)
        if buff_id:
            magic_helper.add_buff(user_id, buff_id, buff_type="event", giver=user_id)

        body = {"result": "ok", "update_buffs": []}
        body.update(magic_helper.buffs_response(user_id))
        return _resp(body)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.error("purchase_event_buff error: %s", e)
        try:
            return _resp(_error_body(magic_helper.body_user_id(
                magic_helper.request_payload()), str(e)))
        except Exception:
            return _resp({"result": "error", "update_buffs": [],
                          "get_buffs": [], "get_buffs_sign": "", "error": str(e)})
