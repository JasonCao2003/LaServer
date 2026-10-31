# -*- coding: utf-8 -*-
"""
Service接口: /service/infraction/api/v1/get_infractions_status_decorated
"""

from flask import request, jsonify, Response, send_from_directory
from infrastructure.database.models import db, User, Infraction
from datetime import datetime
import json
import uuid
import time
import os

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_infractions_status_decorated():
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
        
        active_infractions = Infraction.query.filter_by(
            user_id=user_id, 
            status='active'
        ).filter(
            Infraction.end_time.is_(None) | (Infraction.end_time > datetime.utcnow())
        ).all()
        
        access_status = None
        if active_infractions:
            # severity 是字符串(low/medium/high), 不能直接 max(), 按等级映射取最高。
            _rank = {"low": 1, "medium": 2, "high": 3}
            top = max(active_infractions,
                      key=lambda i: _rank.get((i.severity or "low"), 1))
            access_status = {
                "restricted": True,
                "reason": "Account has active infractions",
                "infractions_count": len(active_infractions),
                "severity": top.severity or "low"
            }
        
        response_body = {
            "status": "OK",
            "access_status": access_status
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
