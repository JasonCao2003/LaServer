# -*- coding: utf-8 -*-
"""
接口: /account/claim_season_quest_reward
函数: claim_season_quest_reward()

领取某个季节/每日任务的奖励。客户端完成任务后点击"领取"时调用。
请求体:
  {"in_season":1,"quest_id":"save_a_spirit_in_dusk","session":"...",
   "user":"...","user_id":"..."}
响应 (依据 That_Sky_Requests 抓包):
  {"currency":{...完整货币...},
   "has_season_quests_rewards":[{"daily_quest_def_id":"...","date":"...","rewarded":true},...],
   "season_quest_claim_result":"ok"}

实现:
  - 把 season_quest_reward 表里该用户"当天"对应 quest_id 的行置 rewarded=True
    (没有则补建为已领取), 幂等。
  - 发放奖励: in_season 为真时发季节蜡烛, 否则发普通蜡烛 (可由季节配置调整)。
  - 返回当天全部奖励状态 + 完整货币 + claim_result。
"""
from flask import request, Response
from database.models import db, User, UserCurrency, SeasonQuestReward
from datetime import datetime
import json
from helpers.game_control_helper import get_season_quests_config


def _get_user(user_id, session_token):
    return User.query.filter_by(id=user_id, session=session_token).first()


def _currency_dict(cur):
    # 统一用 currency_helper.build_currency, 与 get_currency/购买/季节结算字段一致,
    # 避免客户端把缺失/置0字段动画到0再涨回(扣费/奖励异常)。
    from helpers.currency_helper import build_currency
    return build_currency(cur)


def claim_season_quest_reward():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        quest_id = data.get("quest_id") or ""
        in_season = bool(data.get("in_season", 0))

        if not user_id or not session:
            return Response(json.dumps({"error": "Invalid request body"}),
                            status=400, mimetype="application/json")
        if not quest_id:
            return Response(json.dumps({"error": "Missing quest_id"}),
                            status=400, mimetype="application/json")

        user = _get_user(user_id, session)
        if not user:
            return Response(json.dumps({"error": "Invalid session or user not found"}),
                            status=401, mimetype="application/json")

        today = datetime.now().strftime("%Y-%m-%d")

        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        if not cur:
            cur = UserCurrency(user_id=user_id)
            db.session.add(cur)
            db.session.flush()

        # 奖励行: 找当天该任务奖励; 没有则补建。重复领取不再二次发放(幂等)。
        reward = SeasonQuestReward.query.filter_by(
            user_id=user_id, date=today, daily_quest_def_id=quest_id).first()
        already_rewarded = bool(reward and reward.rewarded)
        if not reward:
            reward = SeasonQuestReward(
                user_id=user_id, date=today,
                daily_quest_def_id=quest_id, rewarded=True)
            db.session.add(reward)
        else:
            reward.rewarded = True

        if not already_rewarded:
            # 奖励量由中控配置 (config/game_control.json -> season_quests)
            _cfg = get_season_quests_config()
            if in_season:
                cur.season_candle = (cur.season_candle or 0) + _cfg["reward_season_candle"]
            else:
                cur.candles = (cur.candles or 0) + _cfg["reward_candle"]

        db.session.commit()

        rewards = SeasonQuestReward.query.filter_by(
            user_id=user_id, date=today).all()
        has_rewards = [
            {
                "daily_quest_def_id": r.daily_quest_def_id,
                "date": r.date,
                "rewarded": bool(r.rewarded),
            }
            for r in rewards
        ]

        body = {
            "currency": _currency_dict(cur),
            "has_season_quests_rewards": has_rewards,
            "season_quest_claim_result": "ok",
        }
        return Response(json.dumps(body, ensure_ascii=False, sort_keys=False),
                        status=200, mimetype="application/json")

    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"error": str(e)}),
                        status=500, mimetype="application/json")
