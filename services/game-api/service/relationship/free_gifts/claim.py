# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/free_gifts/claim
函数: claim_free_gift()

领取好友送来的礼物(心火/爱心)。客户端要 send/claim/get_pending 三条路径, 但原服务
**只注册了 send 与 get_pending**, claim 是 404(request_log 实测有 7 个账号请求过
/.../free_gifts/claim 全部 404) -> 收礼方永远领不到, 表现为"好友之间无法赠送爱心和心火"。

客户端(sub_406608 / sub_406654):
  请求 {user, session, from_id, gift_id}
  响应 {result, update_currency, delete_claimed_free_gift,
        get_buffs, set_app_badge_number}
  (update_currency 是**绝对值**快照 —— 见 service/social-feed/super_like 样本)
"""

from flask import request, Response
from database.models import db, User, UserCurrency
import json
import logging

import helpers.gift_helper as gift_helper
from helpers.currency_helper import build_currency

logger = logging.getLogger(__name__)


def _reply(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def _buffs_and_badge(user_id):
    return [], gift_helper.buffs_sign([]), gift_helper.pending_count(user_id)


def claim_free_gift():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get('user') or data.get('user_id')
        session = data.get('session')
        from_id = data.get('from_id')
        gift_id = data.get('gift_id')

        if not user_id or not session:
            return _reply({"result": "invalid"}, 200)

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _reply({"result": "invalid"}, 200)

        if gift_id is None and from_id is None:
            return _reply({"result": "invalid"}, 200)

        if from_id is not None:
            from_id = str(from_id).split(':')[0]

        ok, gift, message = gift_helper.claim_gift(user_id, gift_id, from_id=from_id)
        if not ok and message == "gift_not_found":
            logger.warning("claim_free_gift: 找不到礼物 user=%s gift_id=%s from_id=%s",
                           user_id, gift_id, from_id)
            return _reply({"result": "invalid"}, 200)
        if not ok and message == "already_received":
            # 重复领取: 也算成功, 只是不再加钱(客户端要一个 ok 才把本地那条删掉)。
            logger.info("claim_free_gift: 重复领取 user=%s gift_id=%s", user_id, gift_id)

        db.session.commit()

        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        buffs, sign, badge = _buffs_and_badge(user_id)
        claimed_id = gift_helper._gift_id_int(gift.gift_id) if gift is not None else gift_id
        logger.info("claim_free_gift: user=%s claimed gift_id=%s (%s)", user_id,
                    claimed_id, gift.gift_type if gift is not None else "?")
        return _reply({
            "result": "ok",
            "update_currency": build_currency(cur),
            "delete_claimed_free_gift": claimed_id,
            "get_buffs": buffs,
            "get_buffs_sign": sign,
            "set_app_badge_number": badge,
        })
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("claim_free_gift: 未预期错误: %s", exc, exc_info=True)
        return _reply({"result": "invalid"}, 200)

