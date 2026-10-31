# -*- coding: utf-8 -*-
"""
Service接口: /service/quest/api/v1/get_player_quests
按状态分组返回该用户的真实任务(从 PlayerQuest / QuestStat 读取)。
"""

from flask import request, jsonify
from infrastructure.database.models import db, User, PlayerQuest, QuestStat


def _quest_to_dict(pq):
    stats = QuestStat.query.filter_by(quest_id=pq.quest_id).all()
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
        "quest_name": pq.quest_name,
        "quest_id": pq.quest_id,
        "start_time": pq.start_time,
        "expiry_time": pq.expiry_time,
        "stats": {
            "init_stats": init_stats,
            "completion_requirements": completion_reqs,
            "target_values": target_values,
        },
        "status": pq.status or "",
    }


def get_player_quests():
    try:
        data = request.get_json(force=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        buckets = {"inactive": [], "active": [], "failed": [],
                   "succeeded": [], "collected": [], "canceled": [], "suspended": []}
        if user_id and session:
            user = User.query.filter_by(id=user_id, session=session).first()
            if user:
                for pq in PlayerQuest.query.filter_by(user_id=user_id).all():
                    key = (pq.quest_status or "inactive").lower()
                    if key in buckets:
                        buckets[key].append(_quest_to_dict(pq))
        return jsonify(buckets), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
