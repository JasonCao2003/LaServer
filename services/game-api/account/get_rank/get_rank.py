# -*- coding: utf-8 -*-
"""
接口: /account/get_rank
函数: get_rank()
获取用户排名信息
"""
from flask import request, Response
from database.models import db, User, UserRank
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_rank():
    try:
        # 获取JSON数据（不检查Content-Type，强制解析）
        request_data = request.get_json(force=True)
        print(f"Received data: {request_data}")
        
        if request_data is None:
            print("Request data is None")
            return Response(
                response=json.dumps({"error": "Request body is not valid JSON"}),
                status=400,
                mimetype='application/json'
            )
        
        if 'user' not in request_data or 'session' not in request_data:
            print(f"Missing fields. Available fields: {list(request_data.keys())}")
            return Response(
                response=json.dumps({"error": "Missing 'user' or 'session' field"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        print(f"User ID: {user_id}, Session: {session}")
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        # 获取用户排名（性能优化：直接查询，避免不必要的检查）
        user_rank = UserRank.query.filter_by(user_id=user_id).first()
        
        if not user_rank:
            user_rank = UserRank(
                user_id=user_id,
                social_rank=0.0,
                is_shepherd=False,
                is_beginner=False
            )
            db.session.add(user_rank)
            db.session.commit()
        
        response_body = {
            "set_rank": {
                "social": float(user_rank.social_rank),
                "is_shepherd": user_rank.is_shepherd,
                "is_beginner": user_rank.is_beginner
            }
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"Error in get_rank: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            response=json.dumps({"error": "Internal server error"}),
            status=500,
            mimetype='application/json'
        )
