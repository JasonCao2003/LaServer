# -*- coding: utf-8 -*-
"""
接口: /account/support/has_reply
函数: has_reply()
检查支持回复状态
"""
from flask import request, Response
import json

def has_reply():
    required_header_keys = ['Host', 'User-Agent']
    if all(key in request.headers for key in required_header_keys):
        data = request.get_json(force=True)

        required_keys = ["user", "session"]

        if not all(key in data for key in required_keys):
            return Response(
                response=json.dumps({}),
                status=400,
                mimetype='application/json'
            )

        response_body = {
            "result": "ok",
            "has_reply": False
        }

        return Response(
            response=json.dumps(response_body, sort_keys=False),
            status=200,
            mimetype='application/json'
        )
    else:
        missing_keys = [key for key in required_header_keys if key not in request.headers]
        response_body = {
            "result": "ok",
            "has_reply": False
        }
    return Response(
        response=json.dumps(response_body, sort_keys=False),
        status=200,
        mimetype='application/json'
    )
