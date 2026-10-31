from flask import request, Response
from database.models import db, User, TrustGroupDef, UserTrustGroup
import json
from datetime import datetime

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def get_trust_group_defs():
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
        
        user_trust_groups = UserTrustGroup.query.filter_by(
            user_id=user_id, 
            is_active=True
        ).all()
        
        trust_group_defs = []
        for user_trust_group in user_trust_groups:
            group_def = TrustGroupDef.query.filter_by(
                group_id=user_trust_group.group_id,
                is_active=True
            ).first()
            
            if group_def:
                member_count = UserTrustGroup.query.filter_by(
                    group_id=group_def.group_id,
                    is_active=True
                ).count()
                
                trust_group_defs.append({
                    "group_id": group_def.group_id,
                    "group_name": group_def.group_name,
                    "description": group_def.description,
                    "min_trust_level": group_def.min_trust_level,
                    "max_members": group_def.max_members,
                    "current_members": member_count,
                    "user_trust_level": user_trust_group.trust_level,
                    "created_at": group_def.created_at.isoformat() if group_def.created_at else None,
                    "joined_at": user_trust_group.joined_at.isoformat() if user_trust_group.joined_at else None
                })
        
        response_body = {
            "get_trust_group_defs": trust_group_defs
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