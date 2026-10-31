# -*- coding: utf-8 -*-
"""
接口: /account/serendipity/get
函数: get_serendipity()
获取缘分匹配列表
"""
from flask import request, Response
from database.models import db, User, SerendipityMatch
import json
import time

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_serendipity():
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
        max_count = request_data.get('max_count', 4)  # 默认最多4个
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        current_time = int(time.time())
        
        # 过滤条件：状态为active，且未过期
        matches = SerendipityMatch.query.filter_by(
            user_id=user_id, 
            status='active'
        ).filter(
            (SerendipityMatch.expires_at.is_(None)) | 
            (SerendipityMatch.expires_at > current_time)
        ).order_by(
            SerendipityMatch.match_score.desc()
        ).limit(max_count).all()
        
        serendipity_matches = []
        for match in matches:
            matched_user = User.query.filter_by(id=match.matched_user_id).first()
            if not matched_user:
                continue
                
            match_data = {
                "match_id": match.id,
                "matched_user_id": match.matched_user_id,
                "matched_user_nickname": getattr(matched_user, 'nickname', ''),
                "match_type": match.match_type,
                "match_score": match.match_score,
                "matched_at": match.matched_at,
                "status": match.status
            }
            
            if match.expires_at:
                match_data["expires_at"] = match.expires_at
                
            serendipity_matches.append(match_data)
        
        response_body = {
            "serendipity_matches": serendipity_matches
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
