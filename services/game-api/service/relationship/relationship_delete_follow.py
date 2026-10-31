# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/delete_follow  (高版本 > 0.26.5 新增)
取消关注纸船作者。按抓包"删除关注者":
请求: {"user","session","creator":"<作者id>"}
响应: {"number_deleted":1}
"""
from flask import request, Response
from database.models import db, User
from helpers.follow_helper import delete_follow
import json


def relationship_delete_follow():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        creator = data.get("creator") or ""
        if not user_id or not session:
            return Response(json.dumps({"result": "error"}), 400,
                            mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"result": "error"}), 401,
                            mimetype="application/json")

        n = delete_follow(user.id, creator)
        body = {"number_deleted": n}
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"result": "error", "error": str(e)}), 500,
                        mimetype="application/json")
