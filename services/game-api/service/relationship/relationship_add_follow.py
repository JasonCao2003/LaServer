# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/add_follow  (高版本 > 0.26.5 新增)
关注纸船作者。按抓包"修改纸船关注者":
请求: {"user","session","creator":"<作者id>:<hash>","nickname":"..."}
响应: {"result":"ok","follower":{"id":"000000000000000000000000","follower":"<user>",
       "creator":"<作者id>","last_modified":"ISO","nickname":"..."}}
"""
from flask import request, Response
from database.models import db, User
from helpers.follow_helper import add_follow
import json


def relationship_add_follow():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        creator = data.get("creator") or ""
        nickname = data.get("nickname") or ""
        if not user_id or not session:
            return Response(json.dumps({"result": "error"}), 400,
                            mimetype="application/json")
        if not creator:
            return Response(json.dumps({"result": "error"}), 400,
                            mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"result": "error"}), 401,
                            mimetype="application/json")

        cbase, nick, lm = add_follow(user.id, creator, nickname)
        body = {
            "result": "ok",
            "follower": {
                "id": "000000000000000000000000",
                "follower": user.id,
                "creator": cbase,
                "last_modified": lm,
                "nickname": nick,
            },
        }
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"result": "error", "error": str(e)}), 500,
                        mimetype="application/json")
