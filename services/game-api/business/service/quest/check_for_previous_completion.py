# -*- coding: utf-8 -*-
"""
Service接口: /service/quest/api/v1/check_for_previous_completion

客户端在打开任务面板/接任务前, 用一批 quest_names 查询每个任务"今天是否已完成"
和"以前是否完成过"。

抓包标准响应(That_Sky_Requests):
  {"results": [{"completed_today": bool, "previously_completed": bool,
                "quest_name": "<name>"}, ...]}
  —— 必须为请求里的每个 quest_name 各返回一项, 且键名是 "results"。

之前的实现返回 {"check_for_previous_completion": []} (键名错+空), 而静态库又返回
一批写死的别的任务名 —— 两者都使客户端拿不到自己所问任务的状态, 于是无限重试
(监控里同一接口被刷 145 次), 表现为"点任务无反应、点了两次"。

本实现按客户端传入的 quest_names 逐个回应。完成状态结合数据库:
  - SeasonQuestReward.rewarded=True 视为"今天已完成/已领奖" -> completed_today=True
  - 其余默认 False (未完成), 让客户端正常进入任务流程。
"""

from flask import request, jsonify
from infrastructure.database.models import User
from datetime import datetime


def check_for_previous_completion():
    data = request.get_json(force=True, silent=True) or {}
    quest_names = data.get("quest_names") or []
    user_id = data.get("user") or data.get("user_id")
    session = data.get("session")

    completed_today = set()
    previously_completed = set()

    # 有合法会话时, 结合数据库标出"今天已完成"的任务, 避免重复计入/重复领奖。
    if user_id and session:
        try:
            user = User.query.filter_by(id=user_id, session=session).first()
            if user:
                from infrastructure.database.models import SeasonQuest, SeasonQuestReward
                today = datetime.now().strftime("%Y-%m-%d")
                # 今天已领奖的任务 -> 今天已完成
                for r in SeasonQuestReward.query.filter_by(
                        user_id=user_id, date=today, rewarded=True).all():
                    completed_today.add(r.daily_quest_def_id)
                # 历史(任何日期)出现过已领奖 -> previously_completed
                for r in SeasonQuestReward.query.filter_by(
                        user_id=user_id, rewarded=True).all():
                    previously_completed.add(r.daily_quest_def_id)
        except Exception:
            # 数据库异常时退化为"全部未完成", 保证接口始终成功且键名正确,
            # 客户端不会因 500/格式错而卡住。
            pass

    results = []
    for nm in quest_names:
        results.append({
            "completed_today": nm in completed_today,
            "previously_completed": nm in previously_completed,
            "quest_name": nm,
        })

    return jsonify({"results": results}), 200
