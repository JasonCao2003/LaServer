# -*- coding: utf-8 -*-
r"""
接口: /account/give_candle
函数: give_candle()
送蜡烛(结交好友) / 送心火。

抓包(monitor_exports/cache/account__give_candle.jsonc, 2026-10-03 23:09:28 / 23:09:54):
  请求: {"user","session","recv_user","ability_id":1,"ability_cost":1,"pos":[4,0,-11],"level_id":...}

ability_id=1 就是"加好友"那个节点(config/relationship_abilities.json: tier=1, cost=1,
currency_type=candles, give_also=[2,9,10,19]) —— 把一根蜡烛给对方, 同时把
牵手/收藏/屏蔽/举报 一起解锁。官方响应样本 static_responses/account__give_candle.json
里的 update_friends[0] = {abilities:[1,2,9,10,19], given:1, hints:[1,2,9,10,19],
level:1, when_created:"2024-07-16T15:03:44.803Z"}; 同一份响应里还有 set_friend_count
和 update_online_friends。

参考实现(用户提供) F:\QQ\QQ下载\GiveCandle.js:
  ability_id===1 且还没有好友关系 -> createFriendshipBidirectional(...);
  能力已经在 abilities 里        -> already_unlocked, 不扣费;
  响应 = res / result / currency / update_friends / set_sent_messages /
         set_friend_count / update_online_friends / trust_update。

改造前的问题(用户实测: "加好友加不了, 送了蜡烛又退回来"):
  1) ability_id=1 被当成免费心火, 一根蜡烛都不扣 -> 响应里的 currency 和客户端自己
     乐观扣费后的预期对不上, 客户端按服务端快照把那根蜡烛"退"回来;
  2) 从不写 friendship/friend_status, 也从不回 update_friends -> 客户端拿不到好友记录,
     加好友这笔交易没有下文, 动画回退;
  3) 缺 set_friend_count / update_online_friends / trust_update(官方响应里都有)。

现在按官方样本 + 参考实现补齐: 关系不存在就先建(客户端点名、但本服从没见过的人补一条
占位账号), 该收费时收 ability_cost 根蜡烛并把 given 记 +1, 响应把官方字段回全。
"""

from flask import request, Response
from database.models import db, User, UserCurrency, Friendship, FriendStatus
from helpers.currency_helper import build_currency
import datetime
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# account/give_candle/give_candle.py -> 往上三层 = Sky/ 根目录
_DEFS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "relationship_abilities.json",
)

# /account/give_candle 可能被客户端并发调用(实测两次相隔 26s), 扣费/建关系都要串行
_lock = threading.Lock()

# 注意: 这里**故意没有**"加好友后固定对方名字"的逻辑。名字由客户端决定 —— 加好友
# 之后客户端自己会弹命名框, 再把玩家输入(或它自己 generatePenpalNickname() 生成的)
# 名字用 /account/set_friend_name 发过来(抓包 08:46:36 那次发的就是客户端自己的
# "Fatule")。服务端曾把它写死成"新朋友", 结果弹窗被预填成"新朋友", 等于替客户端把
# 名字定了(用户明确要求: 不要固定好友名, 由客户端确定)。

_defs_cache = {"mtime": None, "table": {}}


def _ability_table():
    """config/relationship_abilities.json -> {id: def}。按 mtime 缓存, 读不到返回 {}。

    这份表就是客户端静态数据里那份(能力 id/名称/cost/currency_type/give_also),
    用它的 cost 兜底, 省得客户端某天不发 ability_cost 时我们一根蜡烛都不扣。
    """
    try:
        mtime = os.path.getmtime(_DEFS_FILE)
    except OSError:
        return {}

    if _defs_cache["table"] and _defs_cache["mtime"] == mtime:
        return _defs_cache["table"]

    try:
        with open(_DEFS_FILE, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
        defs = payload.get("relationship_defs") if isinstance(payload, dict) else payload
        table = {}
        for d in defs or []:
            try:
                table[int(d.get("id"))] = d
            except (TypeError, ValueError, AttributeError):
                continue
        _defs_cache["mtime"] = mtime
        _defs_cache["table"] = table
        return table
    except Exception as exc:  # noqa: BLE001 - 读不到不能影响接口
        logger.warning("give_candle: 载入 %s 失败: %s", _DEFS_FILE, exc)
        return {}


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_list(text):
    """friend_status.abilities / hints 是 TEXT 存的 JSON 数组。"""
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _iso_utc(ts):
    """friend_status.when_created 是 unix 秒; 官方 update_friends 里是
    "2024-07-16T15:03:44.803Z" 这种 ISO8601 字符串。"""
    try:
        dt = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        dt = datetime.datetime.fromtimestamp(0, datetime.timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def _expand_ability_ids(ability_id, table):
    """参考实现的 expandAbilityIds: 节点的 id 加上 give_also, 去重升序。

    id=1 -> [1,2,9,10,19], 与官方样本的 update_friends[0].abilities 完全一致。
    """
    ids = set()
    if ability_id > 0:
        ids.add(ability_id)
    definition = table.get(ability_id) or {}
    for extra in (definition.get("give_also") or []):
        value = _to_int(extra, 0)
        if value > 0:
            ids.add(value)
    return sorted(ids)


def _ensure_currency(user_id):
    cur = UserCurrency.query.filter_by(user_id=user_id).first()
    if not cur:
        cur = UserCurrency(user_id=user_id, candles=0, heart=0, heart_wax=0,
                           prestige=0, prestige_vip=0, prestige_wax=0,
                           season_candle=0, season_wax=0, season_wax_14=0,
                           season_heart=0, vip=0, wax=0)
        db.session.add(cur)
        db.session.flush()
    return cur


def _friend_status(user_id, friend_id):
    return FriendStatus.query.filter_by(user_id=user_id, friend_id=friend_id).first()


def _friend_count(user_id):
    """好友数(和好友树里真正下发的那批条目一致)。

    不能直接数 Friendship(status='active'): "点亮"登记出来的关系也是 active
    (见 friend_helper.ensure_relationship / sync_lit_relationships), 数进去就变成
    "默默点亮过的陌生人也算好友数"。统一走 friend_helper.count_added_friends。
    """
    try:
        import helpers.friend_helper as friend_helper
        count = friend_helper.count_added_friends(user_id)
        if count is not None:
            return int(count)
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(Friendship.query.filter_by(user_id=user_id, status="active").count())
    except Exception:  # noqa: BLE001
        return 0


def _friend_update_entry(friend_id, status):
    """官方 update_friends 里那一条(字段与 static_responses/account__give_candle.json 对齐)。

    "nickname" 这一项官方样本里是空串(送蜡烛那一步还没起名), 但不能真的把键省掉:
    客户端 sub_7BC114 / sub_7C0890 对**缺失**的 nickname 键是取默认值("" 空串),
    然后 sub_E80434 会把整条记录覆盖上去 —— 于是我们库里已经存好的名字会被清成空,
    客户端就认为这个好友还没命名, 又把命名框弹一次(用户实测: "需要反复给好友设置
    备注, 设置完后一会又会弹")。所以这里始终带上 nickname, 值取库里的。
    """
    when_created = int(status.when_created) if status and status.when_created else int(time.time())
    return {
        "abilities": _parse_list(status.abilities) if status else [],
        "friend_id": friend_id,
        "given": int(status.given or 0) if status and status.given is not None else 0,
        "hints": _parse_list(status.hints) if status else [],
        "last_seen_outfit": None,
        "level": int(status.level) if status and status.level is not None else 1,
        "nickname": (status.nickname or "") if status else "",
        "recvd": int(status.recvd or 0) if status and status.recvd is not None else 0,
        "soft_deleted": None,
        "they_soft_deleted": None,
        "unlinked_star_sku": "",
        "when_created": _iso_utc(when_created),
    }


def _reply(body, status=200):
    return Response(json.dumps(body, ensure_ascii=False), status,
                    mimetype="application/json")


def _business_failure(result, user_id, **extra):
    """业务失败也必须 200 + 带 result —— 非 2xx 会被客户端当"非法请求"并回退动画。

    参考实现里失败也是 200: {result: <字符串>, trust, currency}。
    """
    cur = UserCurrency.query.filter_by(user_id=user_id).first()
    body = {
        "res": "ok",
        "result": result,
        "currency": build_currency(cur),
        "update_friends": [],
        "set_sent_messages": [],
        "set_friend_count": _friend_count(user_id),
        "update_online_friends": [],
        "trust_update": {"result": result, "applied": False},
    }
    body.update(extra)
    return _reply(body)


def _queue_gift_message(sender_id, target_id, is_wax, cost):
    """把这次送出的东西推到接收者的待收队列(get_pending_messages 轮询时取走)。"""
    msg_id = int(time.time() * 1000) % 2147483647
    gift = {
        "msg_id": msg_id,
        "type": "gift_heart_wax" if is_wax else "gift",
        "to_id": target_id,
        "currency_type": "" if is_wax else "candles",
        "currency_count": 0 if is_wax else cost,
        "recvd": False,
        "gift_id": msg_id,
    }
    try:
        from account.get_pending_messages.get_pending_messages import pending_messages
        with _lock:
            pending_messages.setdefault(target_id, []).append({
                "type": gift["type"],
                "sender_id": sender_id,
                "msg_id": str(msg_id),
                "msg": json.dumps({"gift_id": msg_id,
                                   "currency_type": gift["currency_type"],
                                   "currency_count": gift["currency_count"]}),
                "ch": "gift",
                "timestamp": int(time.time() * 1000),
                "result": "success",
            })
    except Exception as exc:  # noqa: BLE001 - 只是捎带的消息, 失败不影响送礼
        logger.debug("give_candle: 投递礼物消息失败: %s", exc)
    return gift


def give_candle():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        recv_user = data.get("recv_user") or data.get("receiver") or data.get("to")
        ability_id = _to_int(data.get("ability_id"), 0)
        ability_cost = _to_int(data.get("ability_cost"), 0)

        if not user_id or not session:
            return _reply({"error": "Invalid request body"}, 400)

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _reply({"error": "invalid session"}, 401)

        if not recv_user:
            return _business_failure("missing_target_or_ability", user_id)

        target_id = str(recv_user).split(":")[0]
        target = User.query.filter_by(id=target_id).first()
        if target is None:
            # 客户端会拿"同关卡里点亮过、但从没登录过本服"的 uuid 来加好友。以前这里
            # 直接 404 + {"error": ...} -> 客户端当"非法请求"并回退动画。friend_helper
            # 里同款处理(见 placeholder_users_enabled 的说明)。
            try:
                import helpers.friend_helper as friend_helper
                friend_helper.ensure_placeholder_user(target_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("give_candle: 建占位账号 %s 失败: %s", target_id, exc)
                db.session.rollback()
            target = User.query.filter_by(id=target_id).first()
        if target is None:
            return _business_failure("receiver_not_found", user_id)

        table = _ability_table()
        definition = table.get(ability_id) or {}
        def_cost = _to_int(definition.get("cost"), 0)
        if ability_cost > 0 and def_cost > 0 and ability_cost != def_cost:
            # 不按参考实现那样直接报 invalid_ability_cost: 客户端版本一多, 这份表未必
            # 和它的静态数据完全同步, 拒单会让"加好友"彻底用不了。以客户端报的为准。
            logger.warning("give_candle: ability_cost=%s 与 %s 里 ability %s 的 cost=%s 不一致, 以客户端为准",
                           ability_cost, _DEFS_FILE, ability_id, def_cost)
        cost = ability_cost if ability_cost > 0 else def_cost
        if cost < 0:
            cost = 0
        is_wax = cost <= 0

        # 收费货币: 每个节点自带 currency_type(config/relationship_abilities.json)。
        # id 34/38/8/60 是 prestige(红蜡), id 33 是 season_pass_token。原来这里一律扣
        # candles —— 于是"好友树用红蜡解锁后解锁了节点却返还了红蜡": 客户端乐观扣的是
        # 红蜡, 服务端回调的 currency 快照里红蜡没动(被客户端当"退回"), 蜡烛反而被白扣。
        cur_type = str(definition.get("currency_type") or "candles")
        if cur_type not in UserCurrency.__table__.columns:
            logger.warning("give_candle: ability %s 的 currency_type=%r 不是本地货币列, 按 candles 处理",
                           ability_id, cur_type)
            cur_type = "candles"

        # permanent=false 的节点是"可重复赠送"的动作(如 #7 "赠送爱心": 3 蜡烛 -> 对方 1
        # 爱心)。重复赠送必须每次都收费, 不能因为 abilities 里已经有 7 就当已解锁。
        permanent = definition.get("permanent", True)
        is_permanent = True if permanent is None else bool(permanent)
        reward_type = str(definition.get("reward_type") or "").strip()
        reward_amount = _to_int(definition.get("reward_amount"), 0)

        ability_ids = _expand_ability_ids(ability_id, table)

        # 1) 关系: 客户端要加的人如果本服还没有关系, 先补双向 friendship + friend_status
        #    (客户端点名的 uuid 允许补占位账号)。两点:
        #      nickname=""  —— 名字由客户端定, 服务端不预填(见文件头注释);
        #      abilities=[] —— **故意不预填**能力。能力统一在第 3 步由
        #        friend_helper.grant_abilities 写, 这样"新解锁的节点"才能作为 hints
        #        原样报给客户端(官方样本 static_responses/account__give_candle.json 里
        #        刚加好友那一条就是 abilities=[1,2,9,10,19] + hints=[1,2,9,10,19])。
        #        如果这里先填满 abilities, 第 3 步就"没有新增"了, hints 会变空,
        #        客户端看不到新节点/不弹命名框。
        try:
            import helpers.friend_helper as friend_helper
            friend_helper.ensure_relationship(user_id, target_id, commit=False,
                                              allow_placeholder=True,
                                              abilities=[] if ability_ids else None,
                                              nickname="")
        except Exception as exc:  # noqa: BLE001
            logger.warning("give_candle: 建立好友关系失败(%s <-> %s): %s", user_id, target_id, exc)
            db.session.rollback()

        status = _friend_status(user_id, target_id)
        if status is None:
            # ensure_relationship 失败(并发撞唯一索引等)也不能让客户端空手而归。
            return _business_failure("not_friends", user_id)

        already = bool(ability_id and ability_id in _parse_list(status.abilities))
        charged = False
        result = "ok"

        cur = _ensure_currency(user_id)
        # 2) 扣费: 只有"永久节点 + 已经用该货币送过(given>0)且能力已在"才算
        #    already_unlocked 不收费(与参考实现 checkAbility 一致)。之前是 friend_helper
        #    自动点亮白送的能力(abilities 里带 1 但 given=0)不算数 —— 否则客户端以为扣了
        #    蜡烛、服务端回原值, 就又变成"蜡烛退回来"。非永久(可重复赠送)节点永远收费。
        if is_permanent and already and int(status.given or 0) > 0:
            result = "already_unlocked"
        elif not is_wax:
            balance = int(getattr(cur, cur_type, 0) or 0)
            if balance < cost:
                logger.warning("give_candle: %s %s 不足(%s < %s), 按 0 收", user_id, cur_type, balance, cost)
            setattr(cur, cur_type, max(0, balance - cost))
            charged = True

        # 3) 把这次给的能力并进**双方**档案, 并记下给的蜡烛数(官方 update_friends.given)
        #
        #    好友树的节点是双向的, 只写自己这一行的话对方那一行永远不动 ——
        #    实测(2026-10-04) "房主没有解锁好友树": 送蜡烛那一侧的 abilities 里有 4
        #    (high_five), 对方那一行还是老一套, 收礼的人树上没有这个节点。
        #    friend_helper.grant_abilities 只把"这次新增"的 id 追加进 hints(不再整份
        #    覆盖), 免得客户端已经确认过的 hint 又被下发一遍、命名框反复弹。
        try:
            import helpers.friend_helper as friend_helper
            friend_helper.grant_abilities(status, ability_ids)
            other_status = _friend_status(target_id, user_id)
            if other_status is not None:
                friend_helper.grant_abilities(other_status, ability_ids)
                other_status.local_soft_deleted = False
                # 收礼方那一行的 recvd 必须跟着这张蜡烛一起 +1 (官方语义:
                # given = 我送出去的蜡烛数, recvd = 我收到的蜡烛数)。
                # 老代码只在"赠送类节点"分支里 recvd+1, 于是正常送蜡烛时对方那行
                # 一直是 given=0/recvd=0 —— 收礼方重新登录后客户端读到的是
                # "这段关系没换过蜡烛"(只有 abilities, 没有蜡烛记录), 用户实测
                # "加好友后改完名字重新登录不显示对方是好友只显示名字"。
                # 两边都记上, 好友树两侧的蜡烛数才对得上。
                if charged:
                    other_status.recvd = int(other_status.recvd or 0) + 1
            # 已经起过名的好友, 就别再把"该给 TA 起个名字"这个 hint(id 1)挂在待确认
            # 列表里 —— 老记录(有过名字但 abilities 里没有 1)被 grant_abilities 补上 1
            # 时, hint 1 会一起被塞回去, 客户端下次就又把命名框弹一遍(用户实测:
            # "需要反复给好友设置备注, 设置完后一会又会弹")。abilities 里保留 1 没问题,
            # 那是"节点已解锁"; 要去掉的只是 hints 里那个"等客户端确认"的标记。
            if (status.nickname or "").strip():
                friend_helper.clear_hint(status, friend_helper.NAMING_HINT_ID)
            if other_status is not None and (other_status.nickname or "").strip():
                friend_helper.clear_hint(other_status, friend_helper.NAMING_HINT_ID)
        except Exception as exc:  # noqa: BLE001 - 合并失败不该让送蜡烛整单失败
            logger.warning("give_candle: 合并能力失败(%s <-> %s): %s", user_id, target_id, exc)

        # 3b) 赠送类节点(如 #7 "赠送爱心": 3 蜡烛 -> 对方 1 爱心)把奖励加到**对方**的
        #     货币上。原来只扣送方的钱、对方什么也没拿到 —— 就是用户说的
        #     "好友之间无法赠送爱心"。reward_type/reward_amount 取自节点定义。
        if charged and reward_type and reward_amount > 0:
            if reward_type in UserCurrency.__table__.columns:
                try:
                    tcur = _ensure_currency(target_id)
                    setattr(tcur, reward_type,
                            int(getattr(tcur, reward_type, 0) or 0) + reward_amount)
                    # recvd 不在这里加: 上面 charged 那一步已经按"收到几根蜡烛"统一 +1
                    # (赠送类节点也是用蜡烛换的, 一样算对方收到了一根), 这里再加就重复了。
                    logger.info("give_candle: %s 送给 %s %s +%s", user_id, target_id,
                                reward_type, reward_amount)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("give_candle: 给 %s 加 %s 失败: %s", target_id, reward_type, exc)
            else:
                logger.warning("give_candle: ability %s 的 reward_type=%r 不是本地货币列, 跳过",
                               ability_id, reward_type)

        if charged:
            status.given = int(status.given or 0) + 1
        # 4) 名字不在这里动: 客户端加完好友会自己弹命名框, 再调 /account/set_friend_name
        #    把它定的名字发过来(见文件头注释)。这里只把软删除标记清掉, 让关系可用。
        status.local_soft_deleted = False
        if not status.when_created:
            status.when_created = int(time.time())

        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 - 写库失败也要回 200
            logger.error("give_candle: 写库失败(%s <-> %s): %s", user_id, target_id, exc, exc_info=True)
            db.session.rollback()
            return _business_failure("server_error", user_id)

        # 4) 响应: 字段与官方样本 + 参考实现对齐
        status = _friend_status(user_id, target_id)
        friendship = Friendship.query.filter_by(user_id=user_id, friend_id=target_id).first()
        cur = UserCurrency.query.filter_by(user_id=user_id).first()

        # 只有"送心火"(免费那条)才产生一条待收礼物消息 —— 官方样本里 set_sent_messages
        # 装的全是 type=gift_heart_wax / currency_count=0 这种免费心火; 参考实现里
        # "用蜡烛加好友"这条路径 set_sent_messages 恒为 [] (点蜡烛不给人发消息,
        # 对方只是被点亮)。所以扣蜡烛那条路径不再塞消息, 免得客户端拿它当"不是好友"
        # 的礼物记录而拒绝整份响应。
        gift = None
        if is_wax:
            gift = _queue_gift_message(user_id, target_id, is_wax, cost)

        # 官方 give_candle 的 set_sent_messages 装的是"我送出去的所有 message gift"
        # (static_responses/account__give_candle.json 里一大串 gift_heart_wax 就是)。
        # 这里把 gift_helper 里真实存在的赠礼记录也回给客户端, 客户端的"已送礼物"
        # 列表才不会是空的。
        sent_messages = []
        try:
            import helpers.gift_helper as gift_helper
            _sent_gifts, _ = gift_helper.pending_gifts(user_id)
            sent_messages = [gift_helper.sent_entry(g) for g in _sent_gifts]
        except Exception as exc:  # noqa: BLE001
            logger.debug("give_candle: 读取已送赠礼失败: %s", exc)
        if gift:
            sent_messages.append(gift)

        body = {
            "res": "ok",
            "result": True,
            "currency": build_currency(cur),
            "update_friends": [_friend_update_entry(target_id, status)],
            "set_sent_messages": sent_messages,
            "set_friend_count": _friend_count(user_id),
            "update_online_friends": [{"friend_id": target_id, "level_id": 0}],
            "trust_update": {
                "result": "ok",
                "applied": True,
                "target_player": target_id,
                "xp_delta": 0,
                "xp": 999999,
                "tier": 999,
                "source": definition.get("trust_activity") or "fake_full_trust",
            },
        }
        if friendship is None:
            logger.warning("give_candle: %s <-> %s 关系没建上", user_id, target_id)
        logger.debug("give_candle: %s -> %s ability=%s cost=%s result=%s charged=%s",
                     user_id, target_id, ability_id, cost, result, charged)
        return _reply(body)

    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("give_candle: 未预期错误: %s", exc, exc_info=True)
        try:
            return _business_failure("server_error", data.get("user") or data.get("user_id"))
        except Exception:  # noqa: BLE001
            return _reply({"error": str(exc)}, 500)