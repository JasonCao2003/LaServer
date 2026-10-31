# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/free_gifts/send
函数: send_free_gift()

好友之间赠送礼物(心火 / 爱心)。以前这里只有 CORS 预检处理器, POST 落到它身上
只会回一个**空 200 body** —— 客户端解析 "sent_free_gift" 失败, 于是"赠送心火"
点了没反应(用户实测: "好友之间无法赠送爱心和心火")。

客户端(sub_406018 / sub_406078):
  请求 {user, session, target, gift_type}   (gift_type 是名字, 如 "gift_heart_wax")
  响应 {result, sent_free_gift:{currency_count,currency_type,gift_id,recvd,to_id,type}}

本函数同时处理 OPTIONS(预检) 与 POST(真业务)。
"""

from flask import request, Response
from infrastructure.database.models import db, User
import json
import logging
import time

import business.helpers.gift_helper as gift_helper

logger = logging.getLogger(__name__)


def _reply(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def _cors_preflight():
    response = Response()
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers',
                         'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
    response.headers.add('Access-Control-Max-Age', '3600')
    return response


def send_free_gift_options():
    """OPTIONS 预检 + POST 真业务(端点名沿用 url_map 里已有的 send_free_gift_options)。"""
    if request.method == 'OPTIONS':
        return _cors_preflight()
    return send_free_gift()


def send_free_gift():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get('user') or data.get('user_id')
        session = data.get('session')
        target = data.get('target') or data.get('recv_user') or data.get('to')
        gift_type = data.get('gift_type')

        if not user_id or not session:
            return _reply({"result": "invalid"}, 200)

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _reply({"result": "invalid"}, 200)

        if not target:
            return _reply({"result": "invalid"}, 200)

        target_id = str(target).split(':')[0]
        if target_id == user_id:
            return _reply({"result": "invalid"}, 200)

        # 对方可能还没登录过本服 —— 补一个占位账号, 礼物记录才挂得住。
        if User.query.filter_by(id=target_id).first() is None:
            try:
                import business.helpers.friend_helper as friend_helper
                friend_helper.ensure_placeholder_user(target_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("send_free_gift: 建占位账号 %s 失败: %s", target_id, exc)
                db.session.rollback()
            if User.query.filter_by(id=target_id).first() is None:
                return _reply({"result": "invalid"}, 200)

        name = gift_helper.normalize_gift_type(gift_type)
        if not name:
            logger.warning("send_free_gift: 未知 gift_type=%r (user=%s)", gift_type, user_id)
            return _reply({"result": "invalid"}, 200)

        ok, pay_cur, pay_amount = gift_helper.charge_sender(user_id, name)
        if not ok:
            logger.warning("send_free_gift: %s %s 不足(%s), 拒发", user_id, pay_cur, pay_amount)
            return _reply({"result": "not_enough_currency"}, 200)

        gift = gift_helper.create_gift(user_id, target_id, name,
                                       pay_currency=pay_cur, pay_amount=pay_amount)
        db.session.commit()

        logger.info("send_free_gift: %s -> %s %s (gift_id=%s)", user_id, target_id, name,
                    gift.gift_id)
        return _reply({
            "result": "ok",
            "sent_free_gift": gift_helper.sent_entry(gift),
        })
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("send_free_gift: 未预期错误: %s", exc, exc_info=True)
        return _reply({"result": "invalid"}, 200)
