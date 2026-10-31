# -*- coding: utf-8 -*-
"""
接口: /account/lootboxes/get
函数: get_lootboxes()

获取玩家未开启的宝箱列表。玩家在先祖兑换树买的"魔法"(typ=lootbox 的节点)会变成
这里的宝箱, 再通过 /account/lootboxes/redeem_box 开箱得到具体魔法。

修复点: 旧实现把 lootbox_type 塞进 "type" 键的同时还带 opened/expires_at 等字段,
形状与 redeem_box 里返回的宝箱不一致; 现在统一成
    {"lootbox_id": str, "type": str, "quantity": int, "acquired_at": int}
并且任何异常都返回 200 + JSON(客户端收到非 2xx 就弹"非法请求")。
"""
from flask import Response
from infrastructure.database.models import User, Lootbox
import json
import time
import business.helpers.magic_helper as magic_helper


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def get_lootboxes():
    data = magic_helper.request_payload()
    user_id = data.get('user') or data.get('user_id')
    session = data.get('session')

    if user_id and session:
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _resp({"error": "Invalid session or user not found"}, 401)

    out = []
    now = int(time.time())
    try:
        for lb in Lootbox.query.filter_by(user_id=user_id, opened=False).all():
            if lb.expires_at and lb.expires_at < now:
                continue
            out.append({
                "lootbox_id": lb.lootbox_id,
                "type": lb.lootbox_type or "lootbox",
                "quantity": lb.quantity or 0,
                "acquired_at": lb.acquired_at or 0,
            })
    except Exception:
        out = []
    return _resp({"get_lootboxes": out})