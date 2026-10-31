# -*- coding: utf-8 -*-
"""
接口: /account/lootboxes/redeem_box
函数: redeem_box()

开启(兑换)一个 lootbox 宝箱 -> 得到一件魔法(消耗品)。
请求体: {"box_id": "lootbox_common_19", "user": "...", "session": "..."}
响应(官方抓包实测形状):
  {"currency": {...}, "get_consumables": [...], "get_lootboxes": [], "result": "ok"}

修复点(旧实现的坑):
  1. 开出来的 consumable_id 用 abs(hash(box_id)) 现造 —— Python 字符串 hash 每个
     进程都不一样, 而且这个 id 在客户端定义表里**根本不存在**, 客户端查不到定义
     就崩(用户反馈的"兑换魔法闪退")。改用 magic_helper.pick_box_consumable:
     从官方定义表里按宝箱名字确定性挑一件真实存在的魔法(common 箱 -> energy 等)。
  2. 没有真正消耗宝箱(不扣 quantity / 不置 opened) -> 无限开。现在会扣。
  3. get_consumables 只回自己拼的一件; 现在回整包背包(与官方一致)。
  4. currency 自造了 "hearts"/"ascended_candles"/"season_candles" 这组客户端不认识
     的复数键, 又缺 heart/season_wax/season_heart/prestige —— 客户端按快照更新,
     缺失字段被动画到 0, 表现为"爱心/季腊/季节爱心/升华蜡烛减少"。现在统一走
     currency_helper.build_currency(与 get_currency / 购买接口同一套字段)。
  5. 缺少参数/会话无效原来回 400/401(客户端弹"非法请求"); 现在 200 + result=error。
"""
from paths import RESOURCES_DIR
from flask import Response
from infrastructure.database.models import db, User, UserCurrency, Lootbox
import os
import json
import business.helpers.magic_helper as magic_helper


def _get_user(user_id, session_token):
    return User.query.filter_by(id=user_id, session=session_token).first()


# 兑换树静态库(用于查 box 的成本/货币类型), 按 mtime 缓存
_LIB_FILE = os.path.join(RESOURCES_DIR, "static_responses", "account__get_spirit_shops.json")
_LIB_CACHE = {}


def _box_node(box_id):
    """从兑换树查 box_id 对应节点(取 cst 成本、ctyp 货币类型)。失败返回 None。"""
    try:
        mtime = os.path.getmtime(_LIB_FILE)
    except OSError:
        return None
    cached = _LIB_CACHE.get("data")
    if not cached or cached[0] != mtime:
        try:
            with open(_LIB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            index = {}
            for n in data.get("spirit_shops", []):
                nm = n.get("nm")
                if nm:
                    index[nm] = n
            _LIB_CACHE["data"] = (mtime, index)
            cached = _LIB_CACHE["data"]
        except Exception:
            return None
    return cached[1].get(box_id)


# 货币类型(ctyp) -> UserCurrency 字段
_CTYP_FIELD = {
    "candles": "candles",
    "heart": "heart",
    "prestige": "prestige",
    "season_candle": "season_candle",
    "season_heart": "heart",
    "wax": "wax",
}


def _get_currency_dict(cur):
    """货币字典: 统一用 currency_helper.build_currency(与 get_currency 完全一致)。

    旧实现自造了 "hearts"/"ascended_candles"/"season_candles" 这组 **客户端不认识**
    的复数键, 又缺 heart / season_wax / season_heart / prestige —— 客户端把每次带
    currency 的响应都当"货币最新快照", 缺失字段会被动画到 0, 于是开箱/用魔法后
    表现为"爱心、季腊、季节爱心、升华蜡烛开始减少", 下一次 get_currency 再涨回来。
    """
    try:
        from business.helpers.currency_helper import build_currency
        return build_currency(cur)
    except Exception:
        return {}


def _json(body, status=200):
    return Response(json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def redeem_box():
    try:
        data = magic_helper.request_payload()
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        box_id = data.get("box_id") or data.get("lootbox_id")
        if not user_id or not session or not box_id:
            # 客户端收到非 2xx 会弹"非法请求。\n请重试。"(error_login_request)
            return _json({"result": "error", "error": "Invalid request body",
                          "currency": {}, "get_consumables": [], "get_lootboxes": []})

        user = _get_user(user_id, session)
        if not user:
            return _json({"result": "error", "error": "Invalid session or user not found",
                          "currency": {}, "get_consumables": [], "get_lootboxes": []})

        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        if not cur:
            cur = UserCurrency(user_id=user_id, candles=0, heart=0, heart_wax=0,
                               prestige=0, prestige_vip=0, prestige_wax=0,
                               season_candle=0, season_wax=0, season_wax_14=0,
                               vip=0, wax=0)
            db.session.add(cur)
            db.session.flush()

        # 查 box 成本/货币类型(兑换树); 查不到则默认 1 蜡烛
        node = _box_node(box_id)
        cost = int(node.get("cst", 1)) if node else 1
        ctyp = (node.get("ctyp") if node else "candles") or "candles"
        field = _CTYP_FIELD.get(ctyp, "candles")
        have = getattr(cur, field, 0) or 0
        if have < cost:
            # 货币不足: 200 + 明确结果(旧实现是 200, 保持不变; 绝不 4xx)
            return _json({
                "result": "insufficient_funds",
                "currency": _get_currency_dict(cur),
                "get_consumables": magic_helper.consumables_inventory(user_id),
                "get_lootboxes": _open_boxes(user_id)})

        setattr(cur, field, have - cost)

        # 开出的一件魔法: 定义表里真实存在(common 箱 -> energy 等)
        consumable_id = magic_helper.pick_box_consumable(box_id)
        magic_helper.give_consumable(user_id, consumable_id, 1)

        # 消耗宝箱(扣数量 / 扣完置 opened)
        box = Lootbox.query.filter_by(user_id=user_id, lootbox_id=str(box_id),
                                      opened=False).first()
        if box is not None:
            box.quantity = max((box.quantity or 0) - 1, 0)
            if box.quantity <= 0:
                box.opened = True

        db.session.commit()

        return _json({
            "currency": _get_currency_dict(cur),
            "get_consumables": magic_helper.consumables_inventory(user_id),
            "get_lootboxes": _open_boxes(user_id),
            "result": "ok",
        })

    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        # 也不给 5xx(客户端收到非 2xx 会弹"非法请求")
        return _json({"error": str(e), "result": "error",
                      "get_consumables": [], "get_lootboxes": []})


def _open_boxes(user_id):
    """还没开的宝箱列表(与 /account/lootboxes/get 同一形状)。"""
    try:
        import time
        now = int(time.time())
        out = []
        for lb in Lootbox.query.filter_by(user_id=user_id, opened=False).all():
            if lb.expires_at and lb.expires_at < now:
                continue
            out.append({
                "lootbox_id": lb.lootbox_id,
                "type": lb.lootbox_type or "lootbox",
                "quantity": lb.quantity or 0,
                "acquired_at": lb.acquired_at or 0,
            })
        return out
    except Exception:
        return []