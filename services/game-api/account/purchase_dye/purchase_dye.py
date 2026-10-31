# -*- coding: utf-8 -*-
"""
接口: /account/purchase_dye  (高版本 > 0.26.5 新增)
给物品染色(消耗对应颜色染料)。按抓包"染色物品":
请求: {"user","session","unlock_name":"...","primary_slot":["white","white"],"secondary_slot":[]}
响应: {"purchase_result":["success","undefined",[更新后的物品列表]],
       "currency":{...颜色染料快照...}}
"""
from flask import request, Response
from database.models import db, User, UserCurrency, UnlockItem
from helpers.currency_helper import build_currency
from helpers.dye_helper import set_dye, get_dyes
import json
import time


def _color_cur(cur):
    """把当前可用颜色染料并入货币快照(缺失字段补 0)。"""
    d = build_currency(cur)
    for c in ("color_white", "color_black", "color_red", "color_blue",
              "color_cyan", "color_yellow", "color_green", "color_magenta"):
        d.setdefault(c, 0)
    # 颜色染料存入 dye_helper 同库的 color_dye_currency 表(轻量, 可读)
    try:
        from database.models import db as _db
        from sqlalchemy import text as _text
        with _db.engine.connect() as _conn:
            rows = _conn.execute(_text(
                "SELECT color_name, value FROM color_dye_currency WHERE user_id=:u"),
                {"u": cur.user_id if cur else ""}).fetchall()
        for cn, v in rows:
            d[cn] = int(v or 0)
    except Exception:
        pass
    return d


def _unlock_item_dict(it):
    return {
        "upgrades": {"types": [], "dyes": [], "count": 0},
        "type": it.unlock_type,
        "name": it.name,
        "created_at": it.created_at,
        "ack": bool(it.ack),
    }


def purchase_dye():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        unlock_name = data.get("unlock_name") or ""
        primary_slot = data.get("primary_slot") or []
        secondary_slot = data.get("secondary_slot") or []

        if not user_id or not session:
            return Response(json.dumps({"error": "Invalid request body"}),
                            400, mimetype="application/json")
        if not unlock_name:
            return Response(json.dumps({"error": "Missing unlock_name"}),
                            400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}),
                            401, mimetype="application/json")

        # 合成染色方案字符串: "(white_white,none)" 形式(抓包格式)
        # primary_slot=["white","white"] -> "white_white"; 空槽 -> "none"
        def _slot_str(slot):
            parts = []
            for s in slot or []:
                if isinstance(s, str) and s:
                    parts.append(s if "_" in s else s)
            return "_".join(parts) if parts else "none"
        upgrade_dye = "(%s,%s)" % (_slot_str(primary_slot), _slot_str(secondary_slot))

        it = UnlockItem.query.filter_by(user_id=user_id, name=unlock_name).first()
        if not it:
            it = UnlockItem(user_id=user_id, name=unlock_name,
                            unlock_type="spiritshop", ack=True,
                            created_at=int(time.time()))
            db.session.add(it)
            db.session.flush()

        set_dye(user_id, unlock_name, upgrade_dye)

        try:
            from database.models import db as _db
            from sqlalchemy import text as _text
            with _db.engine.begin() as conn:
                conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS color_dye_currency (
                        user_id TEXT NOT NULL,
                        color_name TEXT NOT NULL,
                        value INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, color_name)
                    )
                """))
                for dye in list(primary_slot) + list(secondary_slot):
                    if not isinstance(dye, str) or not dye:
                        continue
                    color = dye.split("_")[0]
                    if color not in ("white", "black", "red", "blue", "cyan",
                                     "yellow", "green", "magenta"):
                        continue
                    conn.execute(_text("""
                        INSERT INTO color_dye_currency (user_id, color_name, value)
                        VALUES (:u, :c, 0)
                        ON CONFLICT(user_id, color_name) DO NOTHING
                    """), {"u": user_id, "c": "color_" + color})
        except Exception:
            pass

        db.session.commit()

        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        body = {
            "purchase_result": [
                "success",
                "undefined",
                [_unlock_item_dict(it)],
            ],
            "currency": _color_cur(cur),
        }
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"error": str(e)}), 500,
                        mimetype="application/json")
