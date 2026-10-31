# -*- coding: utf-8 -*-
"""
接口: /account/claim_message_gift  (老客户端发的是复数 /account/claim_message_gifts)
函数: claim_message_gift()

领取好友赠礼(心火 / 爱心)。原来**没有注册过**这两个路径 —— request_log 实测
/account/claim_message_gifts 直接 404, 收礼方永远领不到礼物。

客户端(sub_43xxxx 组包, 见 690461/690472 两条分支):
  请求 {user, session, from_id, gift_id}
  响应处理(704419 起的共享 handler)读:
    result / currency / sent_message / get_buffs / set_recvd_messages /
    set_app_badge_number
  官方样本 static_responses/account__claim_message_gift.json:
    {"currency":{...}, "get_buffs":[], "get_buffs_sign":"…", "result":"ok",
     "set_app_badge_number":14,
     "set_recvd_messages":[{"currency_count":5,"currency_type":"heart_wax",
       "from_id":"…","gift_id":…,"msg_id":…,"raw_message":null,"type":"gift_heart_wax"}]}
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


def _currency_snapshot(user_id):
    try:
        return build_currency(UserCurrency.query.filter_by(user_id=user_id).first())
    except Exception:
        return {}


def _claimed_ids(data):
    """请求里可能带 gift_id(单个) / gift_ids(列表) / messages(列表)。"""
    ids = []
    if data.get('gift_id') is not None:
        ids.append(data.get('gift_id'))
    for key in ('gift_ids', 'messages', 'gifts'):
        value = data.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get('gift_id') is not None:
                    ids.append(item.get('gift_id'))
                elif item is not None:
                    ids.append(item)
    return ids


def claim_message_gift():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get('user') or data.get('user_id')
        session = data.get('session')
        from_id = data.get('from_id')

        if not user_id or not session:
            return _reply({"result": "invalid"}, 200)

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _reply({"result": "invalid"}, 200)

        if from_id is not None:
            from_id = str(from_id).split(':')[0]

        ids = _claimed_ids(data)
        claimed = []
        if ids:
            for gid in ids:
                ok, gift, message = gift_helper.claim_gift(user_id, gid, from_id=from_id)
                if ok and gift is not None:
                    claimed.append(gift)
                elif message == "gift_not_found":
                    logger.warning("claim_message_gift: 找不到礼物 user=%s gift_id=%s",
                                   user_id, gid)
        elif from_id:
            # 只给了 from_id: 把这个好友送来的都领了(客户端某些版本这么发)。
            _sent, recvd = gift_helper.pending_gifts(user_id)
            for gift in recvd:
                if gift.sender_id == from_id:
                    ok, got, _msg = gift_helper.claim_gift(user_id, gift.gift_id,
                                                           from_id=from_id)
                    if ok and got is not None:
                        claimed.append(got)
        else:
            return _reply({"result": "invalid"}, 200)

        db.session.commit()

        buffs = []
        badge = gift_helper.pending_count(user_id)
        logger.info("claim_message_gift: user=%s 领取 %d 份礼物", user_id, len(claimed))
        return _reply({
            "result": "ok",
            "currency": _currency_snapshot(user_id),
            "set_recvd_messages": [gift_helper.recvd_message_entry(g) for g in claimed],
            "set_app_badge_number": badge,
            "get_buffs": buffs,
            "get_buffs_sign": gift_helper.buffs_sign(buffs),
        })
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("claim_message_gift: 未预期错误: %s", exc, exc_info=True)
        return _reply({"result": "invalid"}, 200)

