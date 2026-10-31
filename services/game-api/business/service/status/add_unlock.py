# -*- coding: utf-8 -*-
"""
Service接口: /service/status/api/v1/add_unlock
"""

from flask import request, jsonify, Response, send_from_directory
from infrastructure.database.models import db, User, StatusUnlock
import json
import uuid
import time
import os

def check_request_params(request_data, required_params):
    if not request_data:
        return {
            "status": 400,
            "result": {"error": "Request body is empty or not valid JSON"}
        }
    missing_params = [param for param in required_params if param not in request_data]
    if missing_params:
        return {
            "status": 400,
            "result": {"error": f"Missing required parameters: {', '.join(missing_params)}"}
        }
    return None

def validate_session(user_id, session_token):
    from infrastructure.database.models import User  # 注意：要在函数内部导入，避免循环依赖
    user = User.query.filter_by(id=user_id, session=session_token).first()
    if not user:
        return {
            "status": 401,
            "result": {"error": "Invalid session or user not found"}
        }
    return None

def add_unlock():
    try:
        request_data = request.get_json(force=True)
        
        required_params = ['user', 'session', 'name', 'ack']
        params_check = check_request_params(request_data, required_params)
        
        if params_check:
            return Response(
                response=json.dumps({"result": params_check["result"]}),
                status=params_check["status"],
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        session_validation = validate_session(user_id, session_token)
        
        if session_validation:
            return Response(
                response=json.dumps({"result": session_validation["result"]}),
                status=session_validation["status"],
                mimetype='application/json'
            )
        
        user_unlocks = StatusUnlock.query.filter_by(user_id=user_id).all()
        
        unlock_name = request_data.get('name')
        ack_value = request_data.get('ack')
        
        response_data = {
            "result": "ok",
            "update_status_unlocks": []
        }
        
        already_saved = False
        for unlock in user_unlocks:
            if unlock.name == unlock_name:
                already_saved = True
                response_data["status"] = "already"
                response_data["update_status_unlocks"].append({
                    "name": unlock.name,
                    "type": unlock.unlock_type,
                    "ack": unlock.ack,
                    "unlocked_at": unlock.unlocked_at
                })
                break
        
        if not already_saved:
            current_timestamp = int(time.time())
            
            new_unlock = StatusUnlock(
                user_id=user_id,
                name=unlock_name,
                unlock_type="level",  # 固定为"level"
                ack=bool(ack_value),
                unlocked_at=current_timestamp
            )
            
            db.session.add(new_unlock)
            db.session.commit()

            # 重新解锁: 清除墓碑, 让该名字重新出现在响应里
            try:
                from business.helpers.status_unlock_tombstone import clear_tombstone
                clear_tombstone(user_id, unlock_name)
            except Exception:
                pass

            response_data["update_status_unlocks"].append({
                "name": unlock_name,
                "type": "level",
                "ack": bool(ack_value),
                "unlocked_at": current_timestamp
            })
        
        return Response(
            response=json.dumps(response_data, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"An error has occurred in add_unlock - {e}")
        return Response(
            response=json.dumps({"result": "internal_error"}),
            status=500,
            mimetype='application/json'
        )
