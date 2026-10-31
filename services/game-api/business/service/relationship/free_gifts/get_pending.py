# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/free_gifts/get_pending
函数: get_pending_free_gifts()

客户端轮询"待处理的赠礼"。官方字段(static_responses/...__get_pending.json):
  {"set_sent_free_gifts":[...],
   "set_recvd_free_gifts":[{"from_id","gift_id","recvd","to_currency_count",
                            "to_currency_type","type"}]}

旧实现把条目写成了 gift_id/receiver_id/sender_id/gift_type/sent_at/received 这套
服务端自造的名字, 客户端 sub_418934 解析 "from_id"/"to_id"/"type"/"to_currency_*"
全都取不到 -> 待收列表永远是空/无效, 于是"好友之间无法赠送爱心和心火"。
"""

from flask import request, Response
from infrastructure.database.models import User
import json
import logging

import business.helpers.gift_helper as gift_helper

logger = logging.getLogger(__name__)


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def get_pending_free_gifts():
    try:
        request_data = request.get_json(force=True, silent=True) or {}

        if 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )

        user_id = request_data.get('user')
        session = request_data.get('session')

        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )

        sent_gifts, recvd_gifts = gift_helper.pending_gifts(user_id)

        response_body = {
            "set_sent_free_gifts": [gift_helper.sent_entry(g) for g in sent_gifts],
            "set_recvd_free_gifts": [gift_helper.recvd_entry(g) for g in recvd_gifts],
        }

        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )

    except Exception as e:
        logger.error("get_pending_free_gifts: %s", e, exc_info=True)
        return Response(
            response=json.dumps({"error": str(e)}),
            status=200,
            mimetype='application/json'
        )
