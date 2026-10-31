# -*- coding: utf-8 -*-
"""
Service接口: /service/quest/api/v1/update_quest
推进任务状态 (每日任务, 全版本统一):
- status=succeeded : 标记完成 (成就达标后客户端发来)
- collect=true     : 领取奖励, 发放季节蜡烛并标记 collected
响应格式按抓包(领取每日任务奖励): 奖励 season_candle, 返回
  season_reward_id.quest_season_reward 与 purchase_fx.season_candle。
"""
from flask import request, jsonify
from database.models import db, User, PlayerQuest, UserCurrency
from helpers.currency_helper import build_currency
from helpers.game_control_helper import get_daily_quests_config, DEFAULT_SEASON_QUEST_REWARD_SEASON_CANDLE
from datetime import datetime


def _season_reward():
    """每日任务奖励季节蜡烛数 (管理面板可配 game_control.json -> season_quests.reward_season_candle)。"""
    try:
        cfg = get_daily_quests_config()
        return int(cfg.get("reward_season_candle", DEFAULT_SEASON_QUEST_REWARD_SEASON_CANDLE) or 0)
    except Exception:
        return DEFAULT_SEASON_QUEST_REWARD_SEASON_CANDLE


def _ensure_currency(user_id):
    cur = UserCurrency.query.filter_by(user_id=user_id).first()
    if not cur:
        cur = UserCurrency(user_id=user_id, candles=0, heart=0, heart_wax=0,
                           prestige=0, prestige_vip=0, prestige_wax=0,
                           season_candle=0, season_wax=0, season_wax_14=0,
                           season_heart=0, vip=0, wax=0)
        db.session.add(cur)
    return cur


def update_quest():
    try:
        data = request.get_json(force=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        quest_id = data.get("quest_id")
        if not user_id or not session:
            return jsonify({"success": False, "error": "missing fields"}), 400
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return jsonify({"success": False, "error": "invalid session"}), 401

        pq = PlayerQuest.query.filter_by(quest_id=quest_id, user_id=user_id).first()

        if data.get("collect"):
            cur = _ensure_currency(user_id)
            reward = _season_reward()
            cur.season_candle = (cur.season_candle or 0) + reward
            if pq:
                pq.quest_status = "collected"
                pq.status = "collected"
            # 同步标记当天季节任务的领取状态(SeasonQuestReward.rewarded)
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                from database.models import SeasonQuestReward
                if pq and pq.quest_name:
                    sqr = SeasonQuestReward.query.filter_by(
                        user_id=user_id, date=today,
                        daily_quest_def_id=pq.quest_name).first()
                    if sqr:
                        sqr.rewarded = True
            except Exception:
                pass
            db.session.commit()
            return jsonify({
                "success": True,
                "status": "collected",
                "rewards": {"currency": {"season_candle": reward},
                            "season_reward_id": {"quest_season_reward": 1}},
                "fulfillment": {"currency": build_currency(cur)},
                "purchase_fx": {"currency_count": reward,
                                "currency_type": "season_candle", "sound_id": 1},
                "fulfilled_types": ["currency"],
            }), 200

        new_status = data.get("status", "active")
        if pq:
            pq.quest_status = new_status
            pq.status = new_status
            db.session.commit()
        return jsonify({"status": new_status, "success": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
