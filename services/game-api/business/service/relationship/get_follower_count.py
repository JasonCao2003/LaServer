# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/get_follower_count
"""

from flask import request, jsonify, Response, send_from_directory
# FollowRelationship 必带: 下面 FollowRelationship.query 统计 "active" 关注数,
# 漏 import 会 NameError -> 接口 500(实测日志里反复出现
# "POST /service/relationship/api/v1/get_follower_count HTTP/1.1" 500)。
from infrastructure.database.models import db, User, FollowRelationship
import json
import uuid
import time
import os

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_follower_count():
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
        
        # active
        follower_count = FollowRelationship.query.filter_by(
            following_id=user_id, 
            status='active'
        ).count()
        
        response_body = {
            "count": follower_count
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
