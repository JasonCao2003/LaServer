# -*- coding: utf-8 -*-
"""
接口: /account/purchase_spirit_shop_item
函数: purchase_spirit_shop_item()

先祖兑换树里买东西(服饰/动作/魔法)。
请求体示例:
{
  "user": "...", "session": "...",
  "unlock_id": 2588538909,                 # = FNV1a(item_name), 客户端就是这么算的
  "item_name": "lootbox_common_53",        # 节点名
  "spirit_id": 1649464330,
  "cs_end": false
}
响应(官方 accountPurchaseItemResponse.serialize, 抓包实测):
{"result":"ok","currency":{...},"update_unlocks":[...]}
  官方购买响应**只有** result / currency / update_unlocks 三个键, 没有 collectibles。
  (买魔法宝箱 typ=lootbox 的节点时额外带 get_lootboxes, 让客户端拿到刚买到的宝箱。)

重要(踩过的坑):
  * **绝不能**在购买响应里回 "collectibles": []。客户端的收藏品列表(= 动作/站姿,
    即 config/quandongzuo.json 那一套 emote, 由 Collectible 表提供)会被这个空数组
    **覆盖清空** -> 玩家买完魔法后"动作栏、站姿栏全部消失, 只剩魔法栏"。
    官方响应里根本没有这个键, 所以这里只保留官方三键(+ 宝箱分支的 get_lootboxes)。
  * 买魔法(typ=lootbox 的节点)时新宝箱通过 get_lootboxes 发给客户端, 少了它客户端
    拿不到"我得到了什么"、不会去开箱(日志实测: 买完立刻调 redeem_box)。
  * get_lootboxes 一律回**真实**未开宝箱列表(失败分支也是), 不回写死的 []: 客户端
    会用响应里的列表替换本地列表, 空数组会把玩家还没开的宝箱一并清掉。
  * 魔法节点(typ=lootbox)**也要**落一条 unlock。原来这里写"魔法节点不产生 unlock
    记录(名字 lootbox_xxx 不在 Unlocks 定义里)"是**错的**, 官方抓包反驳了它:
    static_responses/account__get_unlocks.json 里就有 lootbox_common_19(created_at
    1714997790)和 lootbox_common_57(1715963144), 两条都是 type=spiritshop、ack=false。
    不落 unlock 的后果(玩家实测): 先祖兑换树上的魔法买完不显示为已解锁 ->
    客户端认为这笔交易没成 -> 本地扣掉的蜡烛又被退回去("兑换了没解锁, 却返还了蜡烛",
    但宝箱已经通过 get_lootboxes 发下来了, 所以"魔法倒是给了")-> 玩家反复点同一个节点
    (request_log 实测 lootbox_common_23 连点 5 次, 蜡烛 295->290)。
  3. 货币不足原来返回 **402**; 客户端收到非 2xx 就弹"非法请求。\n请重试。"
     (字符串 error_login_request), 现在一律 200 + result=error。
  4. 异常原来返回 500(同样会弹非法请求), 现在也回 200 + result=error。
"""
from paths import RESOURCES_DIR
from flask import request, jsonify, Response
from infrastructure.database.models import db, User, ShopItem, UnlockItem, UserCurrency, Lootbox
import os
import json
import logging
import time
from business.helpers.emote_helper import is_emote_unlock, family_levels_for
import business.helpers.magic_helper as magic_helper

logger = logging.getLogger(__name__)


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


# 兑换树静态库(用于查节点类型 typ), 按 mtime 缓存
_LIB_FILE = os.path.join(RESOURCES_DIR, "static_responses", "account__get_spirit_shops.json")
_LIB_CACHE = {}


def _tree_node(item_name):
    """兑换树里按名字取节点(看 typ: lootbox=魔法宝箱, 等)。查不到返回 None。"""
    if not item_name:
        return None
    try:
        mtime = os.path.getmtime(_LIB_FILE)
    except OSError:
        return None
    cached = _LIB_CACHE.get("data")
    if not cached or cached[0] != mtime:
        try:
            with open(_LIB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            index = {}
            for n in data.get("spirit_shops", []):
                nm = n.get("nm")
                if nm and nm not in index:
                    index[nm] = n
            _LIB_CACHE["data"] = (mtime, index)
            cached = _LIB_CACHE["data"]
        except Exception:
            return None
    return cached[1].get(item_name)


def _is_lootbox_node(item_name):
    """这个节点是不是"魔法宝箱"(先祖树里的魔法)。"""
    node = _tree_node(item_name)
    if node and str(node.get("typ", "")) == "lootbox":
        return True
    return str(item_name or "").startswith("lootbox")


def _node_reward(item_name):
    """先祖兑换树里"爱心节点"的奖励。

    static_responses/account__get_spirit_shops.json 里:
      typ == "heart"         -> 买下后给 1 个爱心(heart)。例: heart_point/heart_ohno,
                                cst=3, ctyp=candles
      typ == "season_heart"  -> 买下后给 1 个季节爱心(season_heart)。例: heart_whistle,
                                cst=3, ctyp=season_candle
    返回 (货币列名, 数量); 不是爱心节点返回 (None, 0)。
    """
    node = _tree_node(item_name) or {}
    typ = str(node.get("typ") or "")
    if typ == "heart":
        return "heart", 1
    if typ == "season_heart":
        return "season_heart", 1
    return None, 0


def _open_boxes(user_id):
    """还没开的宝箱列表。"""
    now = int(time.time())
    out = []
    for lb in Lootbox.query.filter_by(user_id=user_id, opened=False).all():
        if lb.expires_at and lb.expires_at < now:
            continue
        out.append({
            "lootbox_id": lb.lootbox_id,
            "type": lb.lootbox_type or "lootbox",
            "quantity": lb.quantity or 0,
            "acquired_at": lb.acquired_at or 0,
        })
    return out


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def purchase_spirit_shop_item():
    """购买灵商店物品接口(见文件头注释)。"""
    try:
        request_data = request.get_json(force=True, silent=True) or {}

        user_id = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        unlock_id = request_data.get('unlock_id')
        item_name = request_data.get('item_name')
        cs_end = request_data.get('cs_end', False)

        # 缺字段不再 400(客户端会弹"非法请求"), 用 200 + result=error 表达。
        if not user_id or not session_token or not item_name:
            return _resp({"result": "error", "error": "Missing required fields",
                          "currency": {}, "update_unlocks": [],
                          "get_lootboxes": _safe_open_boxes(user_id)})

        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"Invalid session for user {user_id}")
            return _resp({"result": "error", "error": "Invalid session or user not found",
                          "currency": {}, "update_unlocks": [],
                          "get_lootboxes": _safe_open_boxes(user_id)})

        user_currency = UserCurrency.query.filter_by(user_id=user_id).first()
        if not user_currency:
            user_currency = UserCurrency(
                user_id=user_id, candles=0, heart=0, heart_wax=0, prestige=0,
                prestige_vip=0, prestige_wax=0, season_candle=0, season_wax=0,
                season_wax_14=0, vip=0, wax=0)
            db.session.add(user_currency)

        # 价格: shop_item 表里有就按它扣; 没有就按**兑换树节点自己的价签**(cst/ctyp)扣。
        # 节点自带的 cst/ctyp 才是客户端显示、并且它下单前本地先扣的值(例如
        # lootbox_common_23 = 1 candles, emote_upgrade_nothanks_01 = 0 candles)。
        # 以前这里查不到就写死 0(免费): 客户端本地扣了 N 根, 服务端却按 0 扣 ->
        # 响应里的 currency 比客户端预期多 -> 玩家看到"兑换后退还了蜡烛"。
        shop_item = ShopItem.query.filter_by(unlockid=unlock_id).first()
        if shop_item is not None:
            currency_type = shop_item.currency_type
            cost = shop_item.unlock_cost
        else:
            node = _tree_node(item_name) or {}
            node_cur = str(node.get("ctyp") or "candles")
            try:
                node_cost = int(node.get("cst") or 0)
            except (TypeError, ValueError):
                node_cost = 0
            # 价格必须与客户端看到的树一致: get_spirit_shops 下发的节点会把
            # cst=0 的免费节点抬到 1(_neutralize_free, 否则客户端当成"已点亮"整棵树
            # 空白), 而这里读的是 static 原文件(cst=0) -> 客户端本地扣 1、服务端扣 0
            # -> 买完蜡烛反而变多("兑换后退还蜡烛")。这里补同样的抬价规则;
            # 向导/任务节点(直接点击即解锁)保持 0。
            if node and node_cost == 0:
                try:
                    from business.account.get_spirit_shops.get_spirit_shops import _is_free_clickable
                    if not _is_free_clickable(node):
                        node_cost = 1
                except Exception:
                    pass
            # 只认 UserCurrency 里真的存在的货币列(candles/season_candle/heart/prestige…)。
            # 兑换树里还有 event_candle_days_of_xxx / season_heart 这类本服没有列的名字,
            # 盲用会让 getattr 取到 0 -> 变成"余额不足"把本来免费的节点卡死, 所以这类
            # 仍然按老行为(免费)走, 并留日志。
            if node_cost and node_cur in UserCurrency.__table__.columns:
                currency_type = node_cur
                cost = node_cost
                logger.info(f"Price from tree node: {item_name} -> {cost} {currency_type}")
            else:
                currency_type = "candles"
                cost = 0
                if node_cost:
                    logger.warning(f"No local column for currency {node_cur!r} "
                                   f"(node {item_name}, cst={node_cost}); charging 0")

        current_balance = getattr(user_currency, currency_type, 0) or 0
        if cost > 0 and current_balance < cost:
            logger.warning(f"Insufficient {currency_type} for user {user_id}: "
                           f"needed {cost}, has {current_balance}")
            return _resp({"result": "error", "code": "INSUFFICIENT_CURRENCY",
                          "error": f"Insufficient {currency_type}",
                          "currency_type": currency_type, "required": cost,
                          "available": current_balance,
                          "currency": _currency_dict(user_id),
                          "update_unlocks": [],
                          "get_lootboxes": _safe_open_boxes(user_id)})
        setattr(user_currency, currency_type, current_balance - cost)

        # ---- 魔法节点(typ=lootbox): 发一个宝箱, 同时落一条 unlock ----
        if _is_lootbox_node(item_name):
            node = _tree_node(item_name) or {}
            ltype = str(node.get("typ") or "lootbox")
            box = Lootbox.query.filter_by(user_id=user_id, lootbox_id=str(item_name),
                                         opened=False).first()
            if box is not None:
                box.quantity = (box.quantity or 0) + 1
            else:
                box = Lootbox(user_id=user_id, lootbox_id=str(item_name),
                              lootbox_type=ltype, quantity=1,
                              acquired_at=int(time.time()), opened=False)
                db.session.add(box)

            # 首次买这个魔法节点才落 unlock / 才告诉客户端; 之后再买(宝箱可以重复买,
            # 每次 +1 个)响应里 update_unlocks 就回 [] —— 与官方抓包一致(官方
            # account__purchase_spirit_shop_item.json 里 lootbox 的 update_unlocks 为空)。
            update_unlocks = []
            magic_unlock = UnlockItem.query.filter_by(user_id=user_id,
                                                      name=item_name).first()
            if magic_unlock is None:
                db.session.add(UnlockItem(user_id=user_id, name=item_name,
                                          unlock_type="spiritshop", ack=False,
                                          created_at=int(time.time())))
                update_unlocks.append({"type": "spiritshop", "name": item_name,
                                       "created_at": 0, "ack": False})
                logger.info(f"Magic node unlock recorded: user={user_id}, name={item_name}")
            elif not magic_unlock.ack:
                # 之前买过但客户端没 ack(ack_unlock 坏掉时期留下的行): 再喊一次
                update_unlocks.append({"type": "spiritshop", "name": item_name,
                                       "created_at": 0, "ack": False})

            db.session.commit()
            logger.info(f"Purchase(magic/lootbox): user={user_id}, box={item_name}, "
                        f"cost={cost} {currency_type}, announced={len(update_unlocks)}")
            return _resp({
                "result": "ok",
                "currency": _currency_dict(user_id),
                "update_unlocks": update_unlocks,
                "get_lootboxes": _safe_open_boxes(user_id),
            })

        # ---- 普通节点(服饰/动作/…) ----
        existing_unlock = UnlockItem.query.filter_by(user_id=user_id,
                                                     name=item_name).first()
        if existing_unlock:
            # 已经拥有: 非消耗品不能买第二次, 把上面预扣的还回去(以前是白扣一次),
            # 并且如果这条 unlock 还没被客户端 ack 过就再喊一次 —— ack_unlock 之前是
            # 坏的(NameError -> result=error), 留下了一批 ack=false 的行, 玩家再点
            # 一次该节点就能补 ack 上。
            setattr(user_currency, currency_type, current_balance)
            update_unlocks = []
            if not existing_unlock.ack:
                update_unlocks.append({"type": "spiritshop", "name": item_name,
                                       "created_at": 0, "ack": False})
            db.session.commit()
            logger.info(f"User {user_id} already owns item: {item_name} "
                        f"(refunded {cost} {currency_type}, announced={len(update_unlocks)})")
            return _resp({
                "result": "ok",
                "currency": _currency_dict(user_id),
                "update_unlocks": update_unlocks,
                "get_lootboxes": _safe_open_boxes(user_id),
            })

        current_time = int(time.time())
        db.session.add(UnlockItem(user_id=user_id, name=item_name,
                                  unlock_type="spiritshop", ack=False,
                                  created_at=current_time))
        update_unlocks = [{
            "type": "spiritshop",
            "name": item_name,
            "created_at": 0,
            "ack": False,
        }]

        # 解锁动作时直接开到最高级(既有行为, 保留)。
        if is_emote_unlock(item_name):
            owned = {u.name for u in UnlockItem.query.filter_by(user_id=user_id).all()}
            owned.add(item_name)
            for lvl_name in family_levels_for(item_name):
                if lvl_name in owned:
                    continue
                db.session.add(UnlockItem(user_id=user_id, name=lvl_name,
                                          unlock_type="spiritshop", ack=False,
                                          created_at=current_time))
                owned.add(lvl_name)
                update_unlocks.append({"type": "spiritshop", "name": lvl_name,
                                       "created_at": 0, "ack": False})

        # 爱心节点(typ=heart / season_heart): 买下后给玩家加爱心/季节爱心。
        # 原来这里收完蜡烛就结束, 玩家"兑换了爱心却没加爱心"(用户实测:
        # "先祖兑换树的爱心兑换后没有增加爱心")。只在**首次**购买走这条路径
        # (已经拥有会早退并退款), 所以不会重复加。
        reward_cur, reward_amount = _node_reward(item_name)
        if reward_cur and reward_amount:
            setattr(user_currency, reward_cur,
                    int(getattr(user_currency, reward_cur, 0) or 0) + reward_amount)
            logger.info(f"Spirit heart reward: user={user_id}, item={item_name}, "
                        f"{reward_cur} +{reward_amount}")

        db.session.commit()
        logger.info(f"Purchase successful: user={user_id}, item={item_name}, "
                    f"cost={cost} {currency_type}, unlock_id={unlock_id}, "
                    f"unlocked_total={len(update_unlocks)}")

        return _resp({
            "result": "ok",
            "currency": _currency_dict(user_id),
            "update_unlocks": update_unlocks,
            "get_lootboxes": _safe_open_boxes(user_id),
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in purchase_spirit_shop_item: {str(e)}")
        import traceback
        traceback.print_exc()
        data = request.get_json(force=True, silent=True) or {}
        uid = data.get('user') or data.get('user_id')
        return _resp({"result": "error", "error": "Internal server error during purchase",
                      "code": "PURCHASE_ERROR", "details": str(e),
                      "currency": _currency_dict(uid), "update_unlocks": [],
                      "get_lootboxes": _safe_open_boxes(uid)})


def _currency_dict(user_id):
    """货币字典: 统一用 currency_helper.build_currency(与 get_currency 一致)。

    旧实现额外塞了一堆写死的 season_candle_14..23:0 / season_wax_17..23:0, 且不带
    season_heart_<N>, 客户端购买后会把这些字段动画到 0、下次 get_currency 再涨回 ——
    表现为"消费爱心后爱心掉光再涨回正常值"。
    """
    try:
        from business.helpers.currency_helper import build_currency
        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        return build_currency(cur)
    except Exception:
        return {}


def _safe_open_boxes(user_id):
    try:
        return _open_boxes(user_id)
    except Exception:
        return []


def _get_purchase_response(user_id, update_unlocks, success=True):
    """兼容旧调用点的响应构建(官方三键 + get_lootboxes; 不带 collectibles)。"""
    return _resp({
        "result": "ok" if success else "error",
        "currency": _currency_dict(user_id),
        "update_unlocks": update_unlocks,
        "get_lootboxes": _safe_open_boxes(user_id),
    })