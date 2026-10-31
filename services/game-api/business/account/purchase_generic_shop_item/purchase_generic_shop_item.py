# -*- coding: utf-8 -*-
"""
接口: /account/purchase_generic_shop_item
函数: purchase_generic_shop_item()

通用商店(魔法商店 / SpellShop_*)购买。

请求体: 客户端会把商品 def 的 id 发过来(库 static_responses/account__get_generic_shops.json
        里每条 def 的 id), 同时一般也带 item_name。这里对两种都做兼容:
        id: id / item_id / shop_item_id / generic_shop_item_id / unlock_id
        name: item_name / name / consumable_name

响应(官方抓包实测):
{
  "currency": {...},
  "generic_shop_item_tracking_info": [{"count": 1, "id": 4165070576, "ttl": 63958}],
  "generic_shop_purchase": "success",
  "get_consumables": [{"consumable_id":int,"cooldown_until":int,"quantity":int}, ...]
}

为什么新写这个接口: 私服原来**没有**这个路由(<0.26.0 客户端请求它 -> Flask HTML 404
-> 客户端弹"非法请求。\n请重试。"), >=0.26.0 客户端则被静态库短路到一份**冻结的抓包
响应**上 —— 永远"成功"、永远不扣钱、也永远不给魔法。现在改成真扣真发。
"""
from paths import RESOURCES_DIR
from flask import Response
from infrastructure.database.models import db, User, UserCurrency
import os
import json
import logging
import time
import business.helpers.magic_helper as magic_helper

logger = logging.getLogger(__name__)


_LIB_FILE = os.path.join(RESOURCES_DIR, "static_responses", "account__get_generic_shops.json")
_LIB_CACHE = {}


def _shop_defs():
    """通用商店定义(661 条, 官方抓包)。按 mtime 缓存。"""
    try:
        mtime = os.path.getmtime(_LIB_FILE)
    except OSError:
        return []
    cached = _LIB_CACHE.get("data")
    if cached is not None and cached[0] == mtime:
        return cached[1]
    defs = []
    try:
        with open(_LIB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("generic_shop_defs"), list):
            defs = data["generic_shop_defs"]
    except Exception:
        defs = []
    _LIB_CACHE["data"] = (mtime, defs)
    return defs


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _find_def(payload):
    """按请求里的 id / 名字在商店定义表里找那条 def。找不到返回 None。"""
    defs = _shop_defs()
    wanted_ids = {_to_int(payload.get(k)) for k in
                  ("id", "item_id", "shop_item_id", "generic_shop_item_id", "unlock_id")}
    wanted_ids.discard(0)
    wanted_names = {payload.get(k) for k in ("name", "shop_item_name", "def_name")}
    wanted_names.discard(None)
    wanted_item = {payload.get(k) for k in ("item_name", "consumable_name")}
    wanted_item.discard(None)

    if wanted_ids:
        for d in defs:
            if _to_int(d.get("id")) in wanted_ids:
                return d
    if wanted_names:
        for d in defs:
            if d.get("name") in wanted_names:
                return d
    if wanted_item:
        for d in defs:
            if d.get("item_name") in wanted_item:
                return d
    return None


def _currency_dict(user_id):
    try:
        from business.helpers.currency_helper import build_currency
        return build_currency(UserCurrency.query.filter_by(user_id=user_id).first())
    except Exception:
        return {}


def _ttl():
    """本次购买计入的周期剩余秒数(商店多数是每日重置)。"""
    now = int(time.time())
    return max(86400 - (now % 86400), 1)


def purchase_generic_shop_item():
    try:
        payload = magic_helper.request_payload()
        user_id = payload.get("user") or payload.get("user_id")
        session = payload.get("session")
        if not user_id or not session:
            return _resp({"generic_shop_purchase": "failed",
                          "error": "Invalid request body",
                          "generic_shop_item_tracking_info": [],
                          "get_consumables": [], "currency": {}})
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _resp({"generic_shop_purchase": "failed",
                          "error": "Invalid session or user not found",
                          "generic_shop_item_tracking_info": [],
                          "get_consumables": [], "currency": {}})

        d = _find_def(payload) or {}

        # 卖的是什么: consumable 类 -> 发魔法; 其它类(inventory_unlock / currency /
        # lootbox)本次只回成功(tracking), 不误发魔法。
        item_type = str(d.get("item_type") or "")
        item_name = d.get("item_name") or payload.get("item_name")
        item_count = _to_int(d.get("item_count")) or 1
        cost = _to_int(d.get("cost"))
        currency_type = d.get("currency_type") or "candles"

        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        if cur is None:
            cur = UserCurrency(user_id=user_id, candles=0, heart=0, heart_wax=0,
                               prestige=0, prestige_vip=0, prestige_wax=0,
                               season_candle=0, season_wax=0, season_wax_14=0,
                               vip=0, wax=0)
            db.session.add(cur)
            db.session.flush()

        if cost > 0:
            have = getattr(cur, currency_type, 0) or 0
            if have < cost:
                db.session.rollback()
                return _resp({
                    "generic_shop_purchase": "failed",
                    "code": "INSUFFICIENT_CURRENCY",
                    "currency_type": currency_type, "required": cost, "available": have,
                    "generic_shop_item_tracking_info": [],
                    "get_consumables": magic_helper.consumables_inventory(user_id),
                    "currency": _currency_dict(user_id)})
            setattr(cur, currency_type, have - cost)

        if item_type == "consumable":
            cid = magic_helper.resolve_consumable_id(item_name, payload.get("item_name"),
                                                     payload.get("consumable_id"))
            if cid:
                magic_helper.give_consumable(user_id, cid, item_count)
            else:
                logger.warning(f"purchase_generic_shop_item: 认不出的 consumable {item_name}")

        db.session.commit()

        tracking_id = _to_int(d.get("id")) or _to_int(payload.get("id"))
        tracking = [{"count": 1, "id": tracking_id, "ttl": _ttl()}] if tracking_id else []
        return _resp({
            "generic_shop_purchase": "success",
            "generic_shop_item_tracking_info": tracking,
            "get_consumables": magic_helper.consumables_inventory(user_id),
            "currency": _currency_dict(user_id),
        })

    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.error(f"purchase_generic_shop_item error: {e}")
        return _resp({"generic_shop_purchase": "failed", "error": str(e),
                      "generic_shop_item_tracking_info": [],
                      "get_consumables": [], "currency": {}})