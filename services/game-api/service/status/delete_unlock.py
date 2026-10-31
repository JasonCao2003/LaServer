# -*- coding: utf-8 -*-
"""
Service接口: /service/status/api/v1/delete_unlock

客户端在"完成/取消某项状态标记"时(如 XXDepositingQueueddawn 等押金排队标记
处理完后)调用本接口删除对应的状态解锁记录。与 add_unlock 成对。

请求体: {"user": "<uid>", "session": "<session>", "name": "<unlock_name>"}
响应:   {"delete_status_unlocks": ["<name>"], "result": "ok"}
"""
from flask import request, Response
from database.models import db, User, StatusUnlock
import json


def delete_unlock():
    try:
        request_data = request.get_json(force=True, silent=True) or {}
        user_id = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        unlock_name = request_data.get('name')

        if not user_id or not session_token or not unlock_name:
            return Response(
                response=json.dumps({
                    "result": "error",
                    "error": "Missing required parameters: user, session, name",
                }),
                status=400, mimetype='application/json')

        # 会话校验(与 add_unlock 一致)
        user = User.query.filter_by(id=user_id, session=session_token).first()
        if not user:
            return Response(
                response=json.dumps({"result": "invalid_session"}),
                status=401, mimetype='application/json')

        # 删除该状态解锁(不存在也视为成功, 幂等)
        StatusUnlock.query.filter_by(user_id=user_id, name=unlock_name).delete()
        db.session.commit()

        # 记录墓碑: 让"新建账号全解锁"注入的该名字下次也不再出现
        try:
            from helpers.status_unlock_tombstone import tombstone
            tombstone(user_id, unlock_name)
        except Exception:
            pass

        return Response(
            response=json.dumps({
                "delete_status_unlocks": [unlock_name],
                "result": "ok",
            }, ensure_ascii=False),
            status=200, mimetype='application/json')

    except Exception as e:
        db.session.rollback()
        return Response(
            response=json.dumps({"result": "internal_error"}),
            status=500, mimetype='application/json')
