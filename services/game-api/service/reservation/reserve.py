# -*- coding: utf-8 -*-
"""
Service接口: /service/reservation/api/v1/reserve
"""

from flask import request, jsonify, Response, send_from_directory
from database.models import db, User
import json
import uuid
import time
import os

def reserve():
    try:
        current_timestamp = int(time.time())
        
        response_data = {
            "status": True,
            "msg": "OK",
            "timestamp": current_timestamp,
            "hmac": "0000000000000000000000000000000000000000000000000000000000000000",
            "bench_id": 5,
            "seat_id": 1,
            "room_ip": "127.0.0.1",
            "room_port": 8443,
            "platform_filter": 0,
            "event_id": 8,
            "base_event_id": 8,
            "subevent_id": 0,
        }
        
        return Response(
            response=json.dumps(response_data, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        print(f"Error in reserve endpoint: {e}")
        return Response(
            response=json.dumps({"error": "internal_error"}),
            status=500,
            mimetype="application/json"
        )

# ========== 封号系统 ==========
# 封号管理页面 - HTML页面
