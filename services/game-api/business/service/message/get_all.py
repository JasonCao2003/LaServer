# -*- coding: utf-8 -*-
from flask import request, Response
import json
from business.helpers.mailbox_helper import build_list

def get_all_messages_v2():
    try:
        data = request.get_json(force=True) or {}
        uid = data.get("user") or data.get("user_id") or ""
        max_count = data.get("max_count", 32)
        body = build_list(uid, max_count, request.headers.get("User-Agent", ""))
        return Response(json.dumps(body, ensure_ascii=False), 200, mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"status": "OK", "messages": []}), 200, mimetype="application/json")
