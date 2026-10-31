# -*- coding: utf-8 -*-
"""
Service接口: /service/quest/api/v1/get_player_quests_by_name

客户端传入一批 quest_names, 查询玩家是否已有这些(具名)任务及其进度。
官方/抓包响应格式:
  {"results": { "<quest_name>": {"quest_id","quest_name","start_time","stats","status"}, ... }}
  —— 每个被问到的 quest_name 都要有一项; 没有该任务时返回"空壳"
     {"quest_id":null,"quest_name":"","start_time":0,"stats":{},"status":""}。

之前问题: 该路径无动态处理器, 且不在 static_responses 排除表里, 高版本客户端拿到的是
接口库里写死的一批别的任务名(ap28_fetch_*)的空壳, 而非自己所问任务的真实状态 ->
向导/伙伴互动(companion_*/trust_moment_*)类任务拿不到状态, 互动按钮点了无反应。

本实现按客户端传入的 quest_names 逐个回应, 命中数据库里玩家真实任务则带出进度。
"""

from flask import request, jsonify
from database.models import User, PlayerQuest, QuestStat


def _empty():
    return {"quest_id": None, "quest_name": "", "start_time": 0,
            "stats": {}, "status": ""}


def _quest_stats(quest_id):
    stats = QuestStat.query.filter_by(quest_id=quest_id).all()
    init_stats, completion_reqs, target_values = [], [], []
    for s in stats:
        entry = {"stat_type": s.stat_type, "value": s.value}
        if s.stat_category == "init_stats":
            if s.updated:
                entry["updated"] = s.updated
            if s.updated_epoch:
                entry["updated_epoch"] = s.updated_epoch
            init_stats.append(entry)
        elif s.stat_category == "completion_requirements":
            completion_reqs.append(entry)
        elif s.stat_category == "target_values":
            target_values.append(entry)
    return {
        "init_stats": init_stats,
        "completion_requirements": completion_reqs,
        "target_values": target_values,
    }


def get_player_quests_by_name():
    try:
        data = request.get_json(force=True, silent=True) or {}
        quest_names = data.get("quest_names") or []
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")

        results = {nm: _empty() for nm in quest_names}

        if user_id and session:
            user = User.query.filter_by(id=user_id, session=session).first()
            if user and quest_names:
                rows = PlayerQuest.query.filter(
                    PlayerQuest.user_id == user_id,
                    PlayerQuest.quest_name.in_(quest_names)).all()
                latest = {}
                for pq in rows:
                    cur = latest.get(pq.quest_name)
                    if cur is None or (pq.start_time or 0) >= (cur.start_time or 0):
                        latest[pq.quest_name] = pq
                for nm, pq in latest.items():
                    results[nm] = {
                        "quest_id": pq.quest_id,
                        "quest_name": pq.quest_name,
                        "start_time": pq.start_time or 0,
                        "stats": _quest_stats(pq.quest_id),
                        "status": pq.status or pq.quest_status or "",
                    }

        return jsonify({"results": results}), 200
    except Exception as e:
        # 退化为全空壳, 保证键名/结构正确, 客户端不会卡住
        try:
            quest_names = (request.get_json(force=True, silent=True) or {}).get("quest_names") or []
        except Exception:
            quest_names = []
        return jsonify({"results": {nm: _empty() for nm in quest_names},
                        "error": str(e)}), 200
