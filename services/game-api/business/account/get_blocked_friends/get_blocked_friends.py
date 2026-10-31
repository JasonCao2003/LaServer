# -*- coding: utf-8 -*-
r"""
接口: /account/get_blocked_friends
函数: get_blocked_friends()

黑名单(被自己拉黑的好友)列表。客户端**每个账号启动时都会调一次**(request_log 里
143 个账号各一次), 并在拉黑/解拉黑按钮的可用性上依赖它。

反编译 libBootloader0200.so(0.22, sub_40AA88 —— 就是 "/account/get_blocked_friends"
的响应解析器, 见 line 653737/653818):
    a1[92] = sub_7BC708(a4+8, "page_next", -1);
    a1[93] = sub_7BC708(a4+8, "page_prev", -1);
    v7 = sub_7BDC94(a4+8, "friends");            // 键名是 "friends"
    ... 遍历这个**数组**(槽位 key==NULL 才当元素, 与 set_friends 同一套数组语义),
        每个元素交给 sub_7C0890 解析(= 普通好友条目解析器);
    一条都没有时打日志 "Synced (0) blocked friends from server"。

也就是说响应必须是:
    {"set_blocked_friends": {"friends": [ <好友条目>, ... ],
                             "page_next": <int, 没有更多用 -1>,
                             "page_prev": <int, 没有上一页用 -1>}}
条目里必须带 "friend_id"(sub_7C0890 第一件事就是 sub_7BC114("friend_id"))。

改造前的问题(用户实测 2026-10-05: "一方把另一方拉黑后 对方显示没有拉黑 且拉黑对方
的人不能解除拉黑"):
  * 旧实现回的是 {"set_blocked_friends": {<friend_uuid>: {...}}} —— 一层以 uuid 为键
    的对象, 而且里面**没有 "friends" 这个键**。客户端 sub_7BDC94(client, "friends")
    取到空 -> "Synced (0) blocked friends from server" -> 黑名单菜单永远空 -> 拉黑
    方没有任何地方能点"解除拉黑"。
  * 条目字段也是服务端自造的(user_id/nickname/platform/blocked_at), 不是客户端
    好友条目那套(friend_id/nickname/abilities/hints/blocked/...), 即使能读到也解析
    不出 friend_id。
  * 更要命的是 friend_helper.ensure_relationship 老代码会把 'blocked' 自动改回
    'active'(见那个函数的注释), 于是这里查 status=='blocked' 本来就是空的。
  * 返回的条目里 blocked=true **必须**带上: 客户端拉黑按钮的文案(sub_8B1AE0
    line 1551865)就是 `v4[691] ^ 1` 决定的(1 -> 显示"解除拉黑")。
"""
from flask import request, Response
from infrastructure.database.models import db, User, Friendship, FriendStatus, UserOutfit
import business.helpers.friend_helper as friend_helper
import json
import logging

logger = logging.getLogger(__name__)


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def _reply(body, status=200):
    return Response(
        response=json.dumps(body, sort_keys=False, ensure_ascii=False),
        status=status,
        mimetype='application/json',
    )


def get_blocked_friends():
    try:
        request_data = request.get_json(force=True, silent=True) or {}

        if 'user' not in request_data or 'session' not in request_data:
            return _reply({"error": "Invalid request body"}, 400)

        user_id = request_data.get('user')
        session = request_data.get('session')

        try:
            page_max = int(request_data.get('page_max') or 0)
        except (TypeError, ValueError):
            page_max = 0
        try:
            page_offset = int(request_data.get('page_offset') or 0)
        except (TypeError, ValueError):
            page_offset = 0
        if page_max <= 0:
            page_max = 150
        if page_offset < 0:
            page_offset = 0

        user = get_cached_user(user_id, session)
        if not user:
            return _reply({"error": "Invalid session or user not found"}, 401)

        total = Friendship.query.filter_by(user_id=user_id, status='blocked').count()
        blocked_rows = (Friendship.query
                        .filter_by(user_id=user_id, status='blocked')
                        .order_by(Friendship.updated_at.desc())
                        .offset(page_offset)
                        .limit(page_max)
                        .all())

        try:
            online_ids = {p.get('uuid') for p in friend_helper.node_players() if p.get('uuid')}
        except Exception:
            online_ids = set()

        friends = []
        for friendship in blocked_rows:
            friend_id = friendship.friend_id
            friend_status = FriendStatus.query.filter_by(
                user_id=user_id, friend_id=friend_id).first()
            outfit = UserOutfit.query.filter_by(user_id=friend_id).first()
            entry = friend_helper.build_friend_entry(
                user_id, friend_id, friendship, friend_status, outfit, online_ids)
            if entry is None:
                continue
            # 自己拉的黑: blocked=True(按钮显示"解除拉黑"), 不是"对方拉黑我"。
            entry["blocked"] = True
            entry["local_blocked"] = False
            friends.append(entry)

        consumed = page_offset + len(friends)
        page_next = consumed if consumed < total else -1
        page_prev = (page_offset - page_max) if page_offset > 0 else -1

        return _reply({
            "set_blocked_friends": {
                "friends": friends,
                "page_next": page_next,
                "page_prev": page_prev,
            }
        })

    except Exception as e:
        logger.error("get_blocked_friends: %s", e, exc_info=True)
        # 异常也必须 200 + 结构完整(缺 "friends" 客户端会当 0 个), 非 2xx 会被当非法请求。
        return _reply({"set_blocked_friends": {"friends": [], "page_next": -1, "page_prev": -1}})
