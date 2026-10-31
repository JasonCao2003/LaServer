# -*- coding: utf-8 -*-
"""
Service接口: /service/inventory/api/v1/unlocks/remove_dye  (高版本 > 0.26.5 新增)
删除物品上的染色方案。按抓包"删除配色":
请求: {"user","session","unlock_name":"...","upgrade_dye":"(magenta_white,none)"}
响应: {"status":"OK","result":"ok"}
"""
from flask import request, Response
from infrastructure.database.models import db, User
from business.helpers.dye_helper import remove_dye
import json


def inventory_remove_dye():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        unlock_name = data.get("unlock_name") or ""
        if not user_id or not session:
            return Response(json.dumps({"status": "ERROR", "result": "error"}),
                            400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"status": "ERROR", "result": "error"}),
                            401, mimetype="application/json")

        if unlock_name:
            remove_dye(user.id, unlock_name)
            db.session.commit()
        body = {"status": "OK", "result": "ok"}
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"status": "ERROR", "result": "error",
                                    "error": str(e)}), 500,
                        mimetype="application/json")
