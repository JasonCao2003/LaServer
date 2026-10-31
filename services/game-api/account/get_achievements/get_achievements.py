from flask import request, jsonify, Response
from database.models import db, User, Achievement
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_achievements():
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
        platform = request_data.get('platform', '')
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        achievements_query = Achievement.query.filter_by(user_id=user_id)
        user_achievements = achievements_query.order_by(Achievement.unlocked_at.desc()).all()
        
        achievements_list = []
        for achievement in user_achievements:
            achievements_list.append({
                "id": achievement.achievement_id,
                "name": achievement.achievement_name,
                "description": achievement.achievement_description,
                "unlocked_at": achievement.unlocked_at,
                "progress": achievement.progress,
                "completed": achievement.completed
            })
        
        response_body = {
            "achievements": achievements_list
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