# -*- coding: utf-8 -*-
"""
Starwatch认证接口
"""

from flask import request, jsonify, Response
from infrastructure.database.models import User
from datetime import datetime, timedelta
import uuid
import json
import jwt


def generate_random_string(length=22):
    """生成随机字符串"""
    return str(uuid.uuid4()).replace('-', '')[:length]


def auth():
    """Starwatch认证接口"""
    searwatch = str(uuid.uuid4())
    suiji = generate_random_string()
    
    def create_jwt_token(client_id, user_id, username, device_id):
        """创建JWT token"""
        payload = {
            "client_id": client_id,
            "username": username,
            "acl": {
                "pub": [
                    f"sky/beta/device/{device_id}/user/{user_id}/event/+",
                    f"sky/live/device/{device_id}/user/{user_id}/remote/prompt"
                ],
                "sub": [
                    f"sky/live/device/{device_id}/user/{user_id}/remote/submit"
                ]
            },
            "iat": datetime.utcnow(),
            "iss": "sw-auth",
            "aud": "sw-broker",
            "exp": datetime.utcnow() + timedelta(seconds=86400),
            "jit": generate_random_string(22)
        }
        header = {
            'alg': 'HS256'
        }
        return jwt.encode(payload, "SkyTidePrivateServer", algorithm="HS256", headers=header)

    def create_response_body(device_id, user_id, broker_url, base64_password, jwt_token):
        """创建响应体"""
        now = datetime.now()
        client_id = "sky-beta-game-" + suiji
        a = client_id[14:36]
        username = f"user::{a}"
        response_body = {
            "mqtt": {
                "clientId": client_id,
                "username": username,
                "password": jwt_token,
                "brokerUrl": broker_url
            },
            "settings": {
                "enableEventPublishing": True,
                "blockedEvents": [
                    "account_resource_waiting",
                    "candle_accepted_cost",
                    "checkpoint",
                    "dayhm_manta_count_03",
                    "debug_trace",
                    "find_prev_game_request",
                    "match_joined",
                    "net_game_connect_attempt",
                    "net_game_move_timeout",
                    "sync_user_data",
                    "*_loaded",
                    "loaded_*",
                    "player_nearby",
                    "player_nearby_left"
                ]
            },
            "acls": {
                "publish": [
                    f"sky/beta/device/{device_id}/user/{user_id}/event/+",
                    f"sky/beta/device/{device_id}/user/{user_id}/remote/prompt",
                    f"sky/beta/device/{device_id}/user/{user_id}/challenge/response"
                ],
                "subscribe": [
                    f"sky/beta/device/{device_id}/user/{user_id}/remote/submit",
                    f"sky/beta/device/{device_id}/user/{user_id}/challenge/submit"
                ]
            },
            "ownerId": f"sky::beta::game::{a}",
            "isJwt": True,
            "expiresSeconds": 86400,
            "starwatchSessionId": searwatch,
            "serverTime": {
                "requestStartMillis": int(now.timestamp() * 1000),
                "requestEndMillis": int((now + timedelta(milliseconds=15)).timestamp() * 1000)
            }
        }
        return response_body

    def handle_request(request_body):
        """处理请求"""
        request_data = json.loads(request_body)
        b = request_data.get("sessionToken", "")
        user = User.query.filter_by(session=b).first()
        if not user:
            return jsonify({"result": "not_found"}), 400
        user_id = user.id
        device_id = request_data.get("deviceId", "")
        base64_password = "SkyTidePrivateServer"
        client_id = "sky-beta-game-" + suiji
        a = client_id[14:36]
        username = f"user::{a}"
        jwt_token = create_jwt_token(client_id, user_id, username, device_id)
        broker_url = "stream.starwatch.ai"
        response_body = create_response_body(device_id, user_id, broker_url, base64_password, jwt_token)
        response_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(json.dumps(response_body))),
            "Connection": "keep-alive",
            "Server": "nginx/1.14.2",
            "X-Powered-By": "Express",
            "Access-Control-Allow-Origin": "*",
            "ETag": "W/\"c1f-X4aC/hwchF/e+GoN5sUDVn/DmHo\"",
            "Via": "1.1 google",
            "Alt-Svc": "h3=\":443\"; ma=2592000,h3-29=\":443\"; ma=2592000"
        }
        return response_headers, response_body

    request_body = request.get_data()
    response_headers, response_body = handle_request(request_body)

    return Response(
        response=json.dumps(response_body, sort_keys=False),
        status=200,
        mimetype='application/json'
    )
