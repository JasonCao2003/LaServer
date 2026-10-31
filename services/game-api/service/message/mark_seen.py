# -*- coding: utf-8 -*-
from flask import request, Response
import json, time

def mark_seen():
    try:
        data = request.get_json(force=True) or {}
        ids = data.get("messages", [])
        now = int(time.time())
        msgs = []
        for item in ids:
            mid = item.get("id") if isinstance(item, dict) else item
            msgs.append({"payload": {"headers": {"id": mid, "seen_at": now}}})
        return Response(json.dumps({"status": "OK", "messages": msgs}, ensure_ascii=False), 200, mimetype="application/json")
    except Exception:
        return Response(json.dumps({"status": "OK", "messages": []}), 200, mimetype="application/json")
