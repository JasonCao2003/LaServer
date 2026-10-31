# -*- coding: utf-8 -*-
r"""
接口: /account/set_friend_block
函数: set_friend_block()

客户端(0.22.0 test.gold, libBootloader0200.so: 请求构造 sub_441C44 / 发送 sub_441BEC /
响应解析 sub_441C90)屏蔽或取消屏蔽一个好友。
抓包 monitor_exports/cache/account__set_friend_block.jsonc (2026-10-04 12:03:17):
    {"user","session","friend","blocked"}      # blocked 是布尔

反编译证据(不猜):
  * 请求字段: sub_441C44 -> "friend"(字符串) + "blocked"(布尔)。
  * 响应解析: sub_441C90 遍历响应对象顶层键, 只认 "update_friends"(长度 14 = 0xE),
    命中后把该数组依次喂给两个 friend barn(*(a2+472)+952 与 +960)。也就是说本接口
    的响应形状必须和 /account/set_friend_name、/account/remove_relationship_hint 同一套:
        {"result": true, "update_friends": [ {friend 条目} ]}
    (set_friend_favorite 的 sub_441970 是同一份代码, 只有请求键不同。)
  * friend 条目里的 "blocked"/"local_blocked" 对应客户端 AccountFriend 的 +691/+692
    (好友解析器 sub_7C0890: sub_7BCD20(a2,"blocked",0) / sub_7BCD20(a2,"local_blocked",0))。
    关系树布局(约 line 1491300)只在 `!rec || (!rec[691] && !rec[692])` 时才建树,
    所以把 blocked=true 回给客户端, 它自己就会把好友树收起来。

改造前的问题: 服务端根本没注册这条路由(account/__init__.py 里没有), 请求落到
json_error_handlers 的"JSON 兜底 200 {}", 客户端拿到 {} 后既不更新 friend barn 也不
落库 —— 用户实测"屏蔽静默失败 / 屏蔽后没反应"。

持久化用的是**已有的列**: Friendship.status ('active'/'blocked'), 和
/account/get_blocked_friends 查询用的完全是同一列(status=='blocked')。本接口**不动**
账号数据以外的东西, 也不新增数据库列。
"""
import json
import logging

from flask import request, Response

from database.models import db, User, FriendStatus, Friendship

logger = logging.getLogger(__name__)


def _reply(body, status=200):
    """非 2xx 会被客户端当"非法请求"并回退动画(见 json_error_handlers), 所以业务
    失败也是 200 + result。"""
    return Response(json.dumps(body, ensure_ascii=False), status,
                    mimetype="application/json")


def set_friend_block():
    try:
        data = request.get_json(force=True, silent=True) or {}

        missing = [k for k in ('user', 'session', 'friend', 'blocked')
                   if data.get(k) is None]
        if missing:
            return _reply({'result': 'missing_parameters: %s' % ", ".join(missing)})

        user_id = data.get('user')
        session = data.get('session')
        friend_id = data.get('friend')
        blocked = bool(data.get('blocked'))

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _reply({'result': 'unauthorized'})

        # Friendship.status: 'active' / 'blocked' / 'pending' —— get_blocked_friends
        # 就是按 status=='blocked' 查的, 所以直接复用它, 不新增列。
        #
        # 注意: 写入后**必须**不会被 250ms 一次的 get_friends/get_friend_statues 自动
        # 点亮改回 'active'。那条路径是 friend_helper.ensure_relationship, 已改成只把
        # 'pending' 转 'active', 'blocked' 一律保持(见那里的长注释)。否则这里刚拉黑,
        # 下一次轮询就被抹掉 —— 用户实测"拉黑后对方显示没有拉黑 / 不能解除拉黑"。
        friendship = Friendship.query.filter_by(
            user_id=user_id, friend_id=friend_id).first()
        new_status = 'blocked' if blocked else 'active'
        if friendship is None:
            friendship = Friendship(user_id=user_id, friend_id=friend_id,
                                    status=new_status)
            db.session.add(friendship)
        else:
            friendship.status = new_status
        db.session.commit()
        logger.info("set_friend_block: %s -> %s blocked=%s", user_id, friend_id, blocked)

        # 拉黑/解除拉黑之后**必须**通知双方客户端重拉好友条目, 否则对方的客户端一直
        # 停在进游戏时缓存的那份好友数据上(好友 barn 里 "local_blocked"/+1236 还是 0),
        # 界面上就"一切照旧"—— 用户反馈的"被拉黑的仍然无反应"正是这个: 服务端条目
        # 里早就带上 local_blocked 了, 只是没人让客户端重新去取。
        # 官方服务端在关系变化时推 friends_updated(反编译 libBootloader0337.so.c
        # line 2447442: if (sub_D26668(&v200, "friends_updated")) goto LABEL_258;),
        # 该分支无字段, 收到即作废好友缓存并重拉 get_friends / get_friend_statues。
        try:
            import helpers.friend_helper as friend_helper
            friend_helper.notify_block_changed(user_id, friend_id)
        except Exception as exc:  # noqa: BLE001 - 推送失败不影响屏蔽结果本身
            logger.warning("set_friend_block: 推送 friends_updated 失败: %s", exc)

        # update_friends[] 条目: 走和 set_friend_name 同一套 compact_friend_entry()
        # (abilities/hints 是 int 数组、created_at 带毫秒 Z), 再补客户端要的 blocked。
        friend_status = FriendStatus.query.filter_by(
            user_id=user_id, friend_id=friend_id).first()
        if friend_status is not None:
            import helpers.friend_helper as friend_helper
            # nickname=True: 好友解析器(sub_7C0890, line 1336344 的 sub_7BC114("nickname"))
            # 对 nickname 是**无条件覆盖**: 条目里没有这个键就把客户端缓存的备注清空。
            # 本接口没有官方抓包可对齐(只有请求), 所以按 set_friend_name 那份**带 nickname**
            # 的形状发, 免得屏蔽一次玩家备注又没了。数值列(level/given/...)解析时用原值
            # 当默认, 缺席不影响; 但 nickname 缺席会被清掉 —— 必须带上。
            entry = friend_helper.compact_friend_entry(friend_status, nickname=True)
        else:
            entry = {
                "abilities": [],
                "created_at": None,
                "friend_id": friend_id,
                "hints": [],
                "last_seen_outfit": None,
                "nickname": "",
                "soft_deleted": None,
                "they_soft_deleted": None,
            }
        # blocked: 自己这一侧的拉黑状态(客户端 +691, 决定按钮文案是"拉黑"还是"解除拉黑",
        # 见 sub_8B1AE0 line 1551865)。local_blocked 永远是 False —— 这个接口只改自己
        # 那一行, "对方拉黑了我"是 local_blocked, 由 /account/get_friends 从**对方的行**
        # 推出来(见 friend_helper.block_flags)。
        entry["blocked"] = blocked
        entry["local_blocked"] = False
        # they_blocked: 0.33.7 的条目解析器把 +1236 做成
        #   *(a1+1236)  = sub_165F4F0(a2, "local_blocked", 0) & 1;
        #   *(a1+1236) |= sub_165F4F0(a2, "they_blocked", 0) & 1;
        # (libBootloader0337.so.c line 2914883/2914884), 也就是"我也把对方拉黑了"。
        # 这里补上"对方有没有拉黑我"(对方的行 status=='blocked'), 语义和
        # friend_helper.build_friend_entry 完全一致; 键缺席时客户端读默认 0, 不影响。
        try:
            _reverse = Friendship.query.filter_by(
                user_id=friend_id, friend_id=user_id).first()
            entry["they_blocked"] = bool(_reverse is not None
                                         and _reverse.status == 'blocked')
        except Exception as exc:  # noqa: BLE001 - 附加字段, 查不到就不带
            logger.warning("set_friend_block: 查询反向屏蔽状态失败: %s", exc)

        return _reply({
            'result': True,
            'update_friends': [entry],
        })

    except Exception as error:
        logger.error(f"An error has occurred in set_friend_block: {error}")
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _reply({'result': 'internal_error'})
