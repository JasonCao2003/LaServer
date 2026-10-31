# -*- coding: utf-8 -*-
"""
接口: /account/get_account_world_quests
函数: get_account_world_quests()

返回该账号已设置/完成过的世界任务(world quest)状态。
依据 That_Sky_Requests 抓包, 干净账号返回空列表:
  {"set_world_quests": []}

说明: 世界任务的"已完成/冷却"状态由 /account/claim_quest_reward 在领取时通过
      update_world_quest 下发给客户端; 本接口对新账号返回空集合即可, 与官方一致。
      之前这里返回写死的两条世界任务, 会让所有账号一进游戏就显示已完成, 已改为空。
"""
from flask import request, jsonify
from infrastructure.database.models import User


def get_account_world_quests():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")

        if not user_id or not session:
            return jsonify({"error": "Invalid request body"}), 400

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return jsonify({"error": "Invalid session or user not found"}), 401

        return jsonify({"set_world_quests": []}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
