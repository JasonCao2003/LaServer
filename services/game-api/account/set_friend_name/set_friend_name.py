# -*- coding: utf-8 -*-
r"""
接口: /account/set_friend_name
函数: set_friend_name()

客户端给好友起名/改名。抓包(monitor_exports/cache/account__set_friend_name.jsonc,
2026-10-04 08:46:36): 用蜡烛加好友之后, give_candle 的 update_friends[].hints 里带着
1(hint 1 = "该给 TA 起个名字"那个节点), 客户端自己弹命名框, 然后把名字发过来:
    {"user","session","friend","name"}
名字是**客户端定的** —— 实测那次客户端发的是它自己生成的名字 "Fatule"。服务端不要
替它写死(用户明确要求: 不要固定好友名, 由客户端确定)。

响应必须逐字段对齐官方抓包 static_responses/account__set_friend_name.json:
    {"result": true,
     "update_friends": [{"abilities": [1, 2, 9, 10, 19],      <- int 数组
                         "created_at": "2024-07-16T15:03:44.803Z",
                         "friend_id": "21613cee-...",
                         "hints": [2, 9, 10, 19],             <- int 数组
                         "last_seen_outfit": null,
                         "nickname": "UWAAA",
                         "soft_deleted": null,
                         "they_soft_deleted": null}]}

改造前的问题(用户实测: "房员输入完后点确定就崩溃, 且节点没有被解锁, 只是多了一个名字"):
  abilities/hints 把库里的 TEXT 原样回过去(字符串 "[1, 2, 6, 9, 10, 19, 27]"),
  outfit 也是字符串 "{}", when_created 是 unix 整数, 还多带了
  attitude/player_badge_type/local_soft_deleted —— 客户端按 int 数组解析 update_friends
  时就崩了, 那份响应里本来该用来解锁好友树节点的 abilities 也就没生效。
现在统一走 friend_helper.compact_friend_entry()(与 /account/remove_relationship_hint
同一套键名), 并把库里的 TEXT 解析成 int 数组。

2026-10-05 补: 起名这一步还要**消费掉 hint 1**。官方样本对比 ——
give_candle 的 hints=[1,2,9,10,19], set_friend_name 的 hints=[2,9,10,19]: 名字定下来
之后那个"该给 TA 起个名字"的提示就该消失。老代码只改 nickname、不动 hints, 于是
客户端那边"待命名"的标记一直留着, 进好友树又弹一次命名框(用户实测: "需要反复给好友
设置备注, 设置完后一会又会弹")。
"""
import json
import logging
from datetime import datetime

from flask import request, Response

from database.models import db, User, FriendStatus

logger = logging.getLogger(__name__)


def _reply(body, status=200):
    """非 2xx 会被客户端当"非法请求"并回退动画(见 json_error_handlers), 所以业务
    失败也是 200 + result。"""
    return Response(json.dumps(body, ensure_ascii=False), status,
                    mimetype="application/json")


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def set_friend_name():
    try:
        data = request.get_json(force=True, silent=True) or {}

        missing = [k for k in ('user', 'session', 'friend', 'name') if data.get(k) is None]
        if missing:
            return _reply({'result': 'missing_parameters: %s' % ", ".join(missing)})

        user_id = data.get('user')
        session_token = data.get('session')
        friend_id = data.get('friend')
        new_name = data.get('name')

        user = get_cached_user(user_id, session_token)
        if not user:
            return _reply({'result': 'unauthorized'})

        friend_status = FriendStatus.query.filter_by(
            user_id=user_id,
            friend_id=friend_id
        ).first()

        if friend_status is None:
            # 正常流程里 give_candle 已经建过关系; 万一没建上(并发、旧数据、对方是刚
            # 补的占位账号), 这里补一次 —— 否则玩家输完名字点确定会白发一次, 好友树上
            # 那个节点还是没东西可解锁。
            try:
                import helpers.friend_helper as friend_helper
                friend_helper.ensure_relationship(user_id, friend_id, allow_placeholder=True)
            except Exception as ensure_error:  # noqa: BLE001
                logger.warning("set_friend_name: 补建关系失败(%s <-> %s): %s",
                               user_id, friend_id, ensure_error)
                db.session.rollback()
            friend_status = FriendStatus.query.filter_by(
                user_id=user_id,
                friend_id=friend_id
            ).first()

        if friend_status is None:
            return _reply({'result': 'friend_not_found'})

        import helpers.friend_helper as friend_helper

        friend_status.nickname = str(new_name)[:100]
        # 能力位是空的(占位账号/老记录)就补默认那份, 不然客户端拿到 abilities: [] 还是
        # 解不开节点。
        if not friend_helper.parse_int_list(friend_status.abilities):
            friend_status.abilities = json.dumps(list(friend_helper.DEFAULT_ABILITIES))
        # 名字一旦定下来, 就把"该给 TA 起个名字"那个节点(hint 1)从待确认列表里去掉。
        # 官方对比: give_candle 的 hints 带 1, set_friend_name 的 hints 不带 1 —— 起名
        # 这一步本来就该消费掉 hint 1。不去掉的话客户端本地的"待命名"标记一直在, 进
        # 好友树又会弹命名框(用户实测: "需要反复给好友设置备注, 设置完后一会又会弹")。
        friend_helper.clear_hint(friend_status, friend_helper.NAMING_HINT_ID)
        friend_status.local_soft_deleted = False
        friend_status.updated_at = datetime.utcnow()

        db.session.commit()

        return _reply({
            'result': True,
            'update_friends': [
                friend_helper.compact_friend_entry(friend_status, nickname=True)
            ],
        })

    except Exception as error:
        logger.error(f"An error has occurred in set_friend_name: {error}")
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _reply({'result': 'internal_error'})