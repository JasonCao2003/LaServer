# -*- coding: utf-8 -*-
"""
接口: /account/activate_season_quest
函数: activate_season_quest()

激活一个季节/每日任务。客户端点击"接受任务"时调用。
请求体:
  {"quest_id":"world_quest_rain_tsktsk","session":"...","user":"...","user_id":"..."}
响应 (依据 That_Sky_Requests 抓包):
  {"season_quest_activated":[{"activated":true,"quest_id":"...","start_value":0.0}]}

实现: 在 season_quest 表里找该用户"当天"对应 daily_quest_def_id 的任务行,
      置 activated=True、start_value=0.0; 若当天没有该任务行(玩家可能先拿到
      任务列表再激活), 则补建一行, 保证幂等且与 get_season_quests 收敛一致。
"""
from flask import request, Response
from database.models import db, User, SeasonQuest
from datetime import datetime
import json


def _get_user(user_id, session_token):
    return User.query.filter_by(id=user_id, session=session_token).first()


def activate_season_quest():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        quest_id = data.get("quest_id") or ""

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

        # 找当天该任务行; 没有则补建(与 get_season_quests 的 stat_type 命名约定一致)
        quest = SeasonQuest.query.filter_by(
            user_id=user_id, date=today, daily_quest_def_id=quest_id).first()
        if not quest:
            quest = SeasonQuest(
                user_id=user_id,
                date=today,
                daily_quest_def_id=quest_id,
                stat_type=f"{quest_id}_count",
                start_value=0.0,
                activated=True,
            )
            db.session.add(quest)
        else:
            quest.activated = True
            quest.start_value = 0.0
        db.session.commit()

        body = {
            "season_quest_activated": [
                {
                    "activated": True,
                    "quest_id": quest_id,
                    "start_value": 0.0,
                }
            ]
        }
        return Response(json.dumps(body, ensure_ascii=False, sort_keys=False),
                        status=200, mimetype="application/json")

    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"error": str(e)}),
                        status=500, mimetype="application/json")
