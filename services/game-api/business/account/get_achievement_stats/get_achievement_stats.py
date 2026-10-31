from flask import request, jsonify, Response
from infrastructure.database.models import db, User, AchievementStat
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_achievement_stats():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        achievement_stats = AchievementStat.query.filter_by(user_id=user_id).all()
        
        if not achievement_stats:
            current_time = int(datetime.utcnow().timestamp())
            
            default_stats = [
                AchievementStat(
                    user_id=user_id,
                    stat_type="world_quest_prairie_laugh_count",
                    value=40000000000,
                    updated_at=current_time
                ),
                AchievementStat(
                    user_id=user_id,
                    stat_type="spirit_cold_count",
                    value=40000000000,
                    updated_at=current_time
                )
            ]
            
            for stat in default_stats:
                db.session.add(stat)
            db.session.commit()
            
            achievement_stats = AchievementStat.query.filter_by(user_id=user_id).all()
        
        achievement_stats_list = []
        for stat in achievement_stats:
            achievement_stats_list.append({
                "type": stat.stat_type,
                "value": stat.value,
                "update": stat.updated_at
            })
        
        response_body = {
            "achievement_stats": achievement_stats_list
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )