# -*- coding: utf-8 -*-
"""
接口: /account/get_generic_shop_item_tracking_info  (高版本 > 0.26.5 新增)
返回指定通用商店(如 SpellShop_*)的物品购买追踪信息。
请求: {"user","session","shop_name"}
响应(按抓包"新年商店/商店已购买物品"):
  {"generic_shop_item_tracking_info":[{"id":<hash>,"count":1,"ttl":<秒>}, ...]}
数据来源: config/generic_shops.json 的 generic_shop_defs 列表。
"""
from flask import request, Response
from database.models import db, User
import json
import os
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
_TTL = 1645257  # 约19天, 与抓包一致


def _load_shops():
    """从 config/generic_shops.json 读取 generic_shop_defs 列表。"""
    try:
        with open(os.path.join(_ROOT, "config", "generic_shops.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        return d.get("generic_shop_defs", []) if isinstance(d, dict) else d
    except Exception:
        return []


def get_generic_shop_item_tracking_info():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        shop_name = data.get("shop_name") or ""
        if not user_id or not session:
            return Response(json.dumps({"error": "Invalid request body"}),
                            400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}),
                            401, mimetype="application/json")

        info = []
        now = int(time.time())
        for shop in _load_shops():
            name = shop.get("name") or shop.get("shop_name") or ""
            if shop_name and name != shop_name:
                continue
            items = shop.get("items", [])
            if not isinstance(items, list):
                continue
            for it in items:
                iid = it.get("id") or it.get("unlockid") or it.get("item_id")
                if not iid:
                    continue
                info.append({
                    "id": int(iid),
                    "count": int(it.get("count", 1)),
                    "ttl": int(it.get("ttl", _TTL)),
                })
        body = {"generic_shop_item_tracking_info": info}
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500,
                        mimetype="application/json")
