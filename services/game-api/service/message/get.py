# -*- coding: utf-8 -*-
from flask import request, Response
import json
from helpers.mailbox_helper import build_detail

def get_message():
    try:
        data = request.get_json(force=True) or {}
        uid = data.get("user") or data.get("user_id") or ""
        ids = data.get("messages", [])
        body = build_detail(uid, ids, request.headers.get("User-Agent", ""))
        return Response(json.dumps(body, ensure_ascii=False), 200, mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"status": "OK", "update_messages": []}), 200, mimetype="application/json")
