# -*- coding: utf-8 -*-
"""
接口: /account/claim_quest_reward
函数: claim_quest_reward()

领取世界任务(world quest)奖励。
请求体: {"name":"sunset_race","bonus_percent":0.38,"reward_event_name":"",
         "user":"...","session":"...","user_id":"..."}
响应:
  {"bonus_amount":N,"bonus_wax_amount":N,"currency":{...},
   "result":"ok","update_forge_rates":[...],"update_world_quest":{...}}
"""
from flask import request, Response
from infrastructure.database.models import db, User, UserCurrency
import json
import time


def _get_user(user_id, session_token):
    return User.query.filter_by(id=user_id, session=session_token).first()


def claim_quest_reward():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        quest_name = data.get("name") or ""
        if not user_id or not session:
            return Response(json.dumps({"error": "Invalid request body"}),
                            status=400, mimetype="application/json")

        user = _get_user(user_id, session)
        if not user:
            return Response(json.dumps({"error": "Invalid session or user not found"}),
                            status=401, mimetype="application/json")

        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        if not cur:
            cur = UserCurrency(user_id=user_id, candles=0, heart=0, heart_wax=0,
                               prestige=0, prestige_vip=0, prestige_wax=0,
                               season_candle=0, season_wax=0, season_wax_14=0,
                               vip=0, wax=0)
            db.session.add(cur)
            db.session.flush()

        try:
            pct = float(data.get("bonus_percent", 0) or 0)
        except (TypeError, ValueError):
            pct = 0.0
        bonus_amount = int(15 + round(pct * 20))   # 蜡烛奖励
        bonus_wax_amount = bonus_amount            # 蜡奖励(与样例一致, 等量)

        cur.candles = (cur.candles or 0) + bonus_amount
        cur.wax = (cur.wax or 0) + bonus_wax_amount
        db.session.commit()

        body = {
            "bonus_amount": bonus_amount,
            "bonus_wax_amount": bonus_wax_amount,
            "currency": {"candles": cur.candles or 0, "wax": cur.wax or 0},
            "result": "ok",
            "update_forge_rates": [],
            "update_world_quest": {
                "cooldown_over_time": int(time.time()) + 86400,
                "ever_completed": True,
                "world_quest_def_id": quest_name,
            },
        }
        return Response(json.dumps(body, ensure_ascii=False, sort_keys=False),
                        status=200, mimetype="application/json")

    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"error": str(e)}),
                        status=500, mimetype="application/json")