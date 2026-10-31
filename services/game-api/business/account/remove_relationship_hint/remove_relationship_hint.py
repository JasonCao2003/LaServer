# -*- coding: utf-8 -*-
r"""
接口: /account/remove_relationship_hint
函数: remove_relationship_hint()

消掉某个"关系提示"(hint)。抓包(monitor_exports/cache/account__remove_relationship_hint.jsonc,
2026-10-04 08:46:35):
    {"user": "...", "session": "...", "friend": "ca1b9fc3-...", "hint": 1}

在加好友流程里的位置: give_candle 的 update_friends[].hints 里带着 1(hint 1 = "该给
TA 起个名字"那个能力节点) -> 客户端弹命名框 -> 先调本接口把 hint 1 消掉 -> 再调
/account/set_friend_name 把名字写进去。

这个接口以前**根本没注册**(account/__init__.py 里没有它的路由), 未注册的
/account/* 会被 json_error_handlers 兜成 200 + {} —— 客户端拿不到 result /
update_friends, 那个提示在它那边永远没被确认掉, 好友树上节点一直亮着/解不开
(用户实测: "节点没有被解锁")。

响应逐字段对齐官方抓包 static_responses/account__remove_relationship_hint.json:
    {"result": true,
     "update_friends": [{"abilities": [1, 2, 9, 10, 19],
                         "created_at": "2024-07-16T15:03:44.803Z",
                         "friend_id": "21613cee-...",
                         "hints": [2, 9, 10, 19],      <- 消掉的那个 id 不再出现
                         "last_seen_outfit": null,
                         "soft_deleted": null,
                         "they_soft_deleted": null}]}
即: 把 hint 从 friend_status.hints 里删掉, 再回一份和 set_friend_name 同键名的
update_friends(走 friend_helper.compact_friend_entry)。

2026-10-05 修: 官方那份样本里没有 nickname 这个键, 但**不能**照抄着省掉它 ——
客户端的好友条目解析器(sub_7C0890, line 1336344 起)对 nickname 是无条件覆盖:
条目里没有这个键 -> sub_7BC114 取默认空串 -> sub_E80434 把整条记录(含空名字)覆盖到
客户端缓存上。于是每次"确认一个提示"都把玩家刚设好的备注清空, 客户端认为好友又没
名字了, 命名框重新弹出来(用户实测: "需要反复给好友设置备注, 设置完后一会又会弹")。
所以这里固定 nickname=True(和 set_friend_block 里的同款处理一致)。
"""
import json
import logging
from datetime import datetime

from flask import request, Response

from infrastructure.database.models import db, User, FriendStatus

logger = logging.getLogger(__name__)


def _reply(body, status=200):
    """非 2xx 会被客户端当"非法请求", 所以失败也是 200 + result。"""
    return Response(json.dumps(body, ensure_ascii=False), status,
                    mimetype="application/json")


def remove_relationship_hint():
    try:
        data = request.get_json(force=True, silent=True) or {}

        user_id = data.get('user')
        session_token = data.get('session')
        friend_id = data.get('friend')
        hint = data.get('hint')

        if not user_id or not session_token or not friend_id:
            return _reply({'result': False, 'update_friends': []})

        user = User.query.filter_by(id=user_id, session=session_token).first()
        if not user:
            return _reply({'result': False, 'update_friends': []})

        friend_status = FriendStatus.query.filter_by(
            user_id=user_id,
            friend_id=friend_id
        ).first()
        if friend_status is None:
            return _reply({'result': False, 'update_friends': []})

        import business.helpers.friend_helper as friend_helper

        try:
            hint_id = int(hint)
        except (TypeError, ValueError):
            hint_id = None

        hints = friend_helper.parse_int_list(friend_status.hints)
        if hint_id is not None and hint_id in hints:
            hints = [h for h in hints if h != hint_id]
            friend_status.hints = json.dumps(hints)
            friend_status.updated_at = datetime.utcnow()
            db.session.commit()
            logger.debug("remove_relationship_hint: %s 对 %s 消掉 hint %s, 剩 %s",
                         user_id, friend_id, hint_id, hints)

        return _reply({
            'result': True,
            'update_friends': [friend_helper.compact_friend_entry(friend_status,
                                                                 nickname=True)],
        })

    except Exception as error:
        logger.error(f"An error has occurred in remove_relationship_hint: {error}")
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _reply({'result': False, 'update_friends': []})