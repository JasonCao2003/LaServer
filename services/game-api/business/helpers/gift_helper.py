# -*- coding: utf-8 -*-
"""
好友赠礼(心火 / 爱心)公共逻辑 —— 供 account 与 service 两侧共用。

客户端(0.22, libBootloader0200.so.c)的赠礼协议:
  * /service/relationship/api/v1/free_gifts/send   请求 {user,session,target,gift_type}
        响应 {result, sent_free_gift:{currency_count,currency_type,gift_id,recvd,to_id,type}}
        (解析: sub_406018 组包 -> {target,gift_type}; sub_406078 收 sent_free_gift)
  * /service/relationship/api/v1/free_gifts/get_pending
        响应 {set_sent_free_gifts:[...],
              set_recvd_free_gifts:[{from_id,gift_id,recvd,to_currency_count,
                                     to_currency_type,type}]}
  * /service/relationship/api/v1/free_gifts/claim  请求 {user,session,from_id,gift_id}
        响应 {result, update_currency, delete_claimed_free_gift,
              get_buffs, set_app_badge_number}
  * /account/send_message                          请求 {user,session,target,gift_type}
        响应 {result, currency, set_sent_messages, set_recvd_messages}
  * /account/get_pending_messages                  响应 {set_sent_messages,set_recvd_messages}
  * /account/claim_message_gift(s)
        响应 {result, currency, set_recvd_messages:[{currency_count,currency_type,from_id,
              gift_id,msg_id,raw_message,type}], set_app_badge_number,
              get_buffs, get_buffs_sign}
  * /account/give_candle 的 set_sent_messages 里装的也是同一批 message gift 条目。

礼物品类: 枚举顺序取自 libBootloader0200.so 的指针表 off_146CEB0
  (sub_7BB90C -> 名字; sub_7BB930 -> 名字转枚举):
    0 ""(无效) 1 gift_rainbow 2 gift 3 gift_wax 4 gift_ap 5 social_feed_reward
    6 gift_heart_wax 7 gift_hearts  8 gift_scandle

礼包规则(见 /account/get_vars 里的 gift_heart_wax_count=5 / gift_wax_count=5):
  * 心火 gift_heart_wax : 免费, 收礼方 heart_wax +5
  * 烛火 gift_wax       : 免费, 收礼方 wax +5
  * 爱心 gift_hearts    : 送方扣 3 蜡烛, 收礼方 heart +1
        (与好友树 "赠送爱心" 节点 config/relationship_abilities.json #7 一致:
         cost=3 candles, reward_type=heart, reward_amount=1)
  * 其余(gift_ap/gift_rainbow/gift_scandle/social_feed_reward): 不可由玩家自发赠送,
        这里当免费无奖励处理, 只落一条记录。

所有赠礼都落在 FreeGift 表; 收礼方在 /free_gifts/get_pending 与
/account/get_pending_messages 都能看到同一条, 任意一处领取后即标记 received。
"""

import hashlib
import logging
import threading
import time

logger = logging.getLogger(__name__)

# 枚举顺序(与 off_146CEB0 一致); 下标即客户端 gift_type 数字。
GIFT_ENUM = [
    "",
    "gift_rainbow",
    "gift",
    "gift_wax",
    "gift_ap",
    "social_feed_reward",
    "gift_heart_wax",
    "gift_hearts",
    "gift_scandle",
]

# name -> {pay_currency, pay_amount, recv_currency, recv_amount}
GIFT_SPECS = {
    "gift_heart_wax": {"pay_currency": None, "pay_amount": 0,
                       "recv_currency": "heart_wax", "recv_amount": 5},
    "gift_wax": {"pay_currency": None, "pay_amount": 0,
                 "recv_currency": "wax", "recv_amount": 5},
    "gift_hearts": {"pay_currency": "candles", "pay_amount": 3,
                    "recv_currency": "heart", "recv_amount": 1},
    "gift": {"pay_currency": "candles", "pay_amount": 3,
             "recv_currency": "heart", "recv_amount": 1},
    "gift_scandle": {"pay_currency": None, "pay_amount": 0,
                     "recv_currency": "season_candle", "recv_amount": 1},
    "gift_ap": {"pay_currency": None, "pay_amount": 0,
                "recv_currency": None, "recv_amount": 0},
    "gift_rainbow": {"pay_currency": None, "pay_amount": 0,
                     "recv_currency": None, "recv_amount": 0},
    "social_feed_reward": {"pay_currency": None, "pay_amount": 0,
                           "recv_currency": None, "recv_amount": 0},
}

# 赠送记录保留多久(秒)。官方免费礼物是每日可领, 这里放宽到 30 天。
GIFT_TTL_SECONDS = 30 * 24 * 3600


def normalize_gift_type(gift_type):
    """把客户端传来的 gift_type 归一成字符串名。

    客户端发的是名字(sub_7BB90C), 但老接口/别的版本可能发数字枚举, 两种都接受。
    无法识别返回 ""。
    """
    if gift_type is None:
        return ""
    if isinstance(gift_type, bool):
        return ""
    if isinstance(gift_type, int):
        if 0 <= gift_type < len(GIFT_ENUM):
            return GIFT_ENUM[gift_type]
        return ""
    text = str(gift_type).strip()
    if not text:
        return ""
    if text.lstrip("-").isdigit():
        try:
            idx = int(text)
        except ValueError:
            return ""
        if 0 <= idx < len(GIFT_ENUM):
            return GIFT_ENUM[idx]
        return ""
    if text in GIFT_SPECS:
        return text
    return ""


def gift_spec(name):
    """取礼包规则; 未知名当"免费无奖励"。"""
    return GIFT_SPECS.get(name) or {
        "pay_currency": None, "pay_amount": 0,
        "recv_currency": None, "recv_amount": 0,
    }


_gift_id_lock = threading.Lock()
_gift_id_last = [0]


def new_gift_id():
    """生成 gift_id。

    客户端把它放在 64 位 int 字段里(claim 用 sub_C92144 写整数), 所以必须是能塞进
    64 位的正整数。用"毫秒时间戳 + 自增"保证递增且不重复。
    """
    with _gift_id_lock:
        now = int(time.time() * 1000)
        value = now * 1000 + (_gift_id_last[0] % 1000)
        if value <= _gift_id_last[0]:
            value = _gift_id_last[0] + 1
        _gift_id_last[0] = value
        return value


def _ensure_currency(user_id):
    from infrastructure.database.models import db, UserCurrency
    cur = UserCurrency.query.filter_by(user_id=user_id).first()
    if not cur:
        cur = UserCurrency(user_id=user_id, candles=0, heart=0, heart_wax=0,
                           prestige=0, prestige_vip=0, prestige_wax=0,
                           season_candle=0, season_wax=0, season_wax_14=0,
                           season_heart=0, vip=0, wax=3)
        db.session.add(cur)
        db.session.flush()
    return cur


def charge_sender(sender_id, name):
    """按礼包规则扣送方的钱。返回 (ok, pay_currency, pay_amount)。

    免费礼包直接返回 (True, "", 0)。
    """
    spec = gift_spec(name)
    pay_cur = spec.get("pay_currency")
    pay_amount = int(spec.get("pay_amount") or 0)
    if not pay_cur or pay_amount <= 0:
        return True, "", 0

    from infrastructure.database.models import UserCurrency
    cur = _ensure_currency(sender_id)
    have = int(getattr(cur, pay_cur, 0) or 0)
    if have < pay_amount:
        return False, pay_cur, pay_amount
    setattr(cur, pay_cur, have - pay_amount)
    return True, pay_cur, pay_amount


def create_gift(sender_id, target_id, name, pay_currency="", pay_amount=0,
                commit=False, now=None):
    """落一条 FreeGift 记录并返回它(默认不 commit, 由调用方决定)。"""
    from infrastructure.database.models import db, FreeGift
    now = int(now or time.time())
    gift = FreeGift(
        sender_id=sender_id,
        receiver_id=target_id,
        gift_type=name,
        gift_id=str(new_gift_id()),
        sent_at=now,
        received=False,
        expires_at=now + GIFT_TTL_SECONDS,
    )
    db.session.add(gift)
    if commit:
        db.session.commit()
    return gift


def sent_entry(gift):
    """送方视角的一条记录。

    /free_gifts/send 的 sent_free_gift 与 /account/give_candle 的 set_sent_messages
    字段一致: {currency_count, currency_type, gift_id, msg_id, recvd, to_id, type}。
    currency_count/currency_type 记"送方为这份礼物花了什么"(免费心火就是 0/"")。
    """
    name = gift.gift_type or ""
    spec = gift_spec(name)
    pay_cur = spec.get("pay_currency") or ""
    pay_amount = int(spec.get("pay_amount") or 0)
    gid = _gift_id_int(gift.gift_id)
    return {
        "currency_count": pay_amount,
        "currency_type": pay_cur,
        "gift_id": gid,
        "msg_id": gid,
        "recvd": bool(gift.received),
        "to_id": gift.receiver_id,
        "type": name,
    }


def recvd_entry(gift):
    """收方视角的一条记录。

    /free_gifts/get_pending 的 set_recvd_free_gifts 与
    /account/claim_message_gift 的 set_recvd_messages 字段一致:
    {from_id, gift_id, msg_id, recvd, to_currency_count, to_currency_type, type}
    (claim_message_gift 额外带 currency_count/currency_type/raw_message, 见
     recvd_message_entry)。
    """
    name = gift.gift_type or ""
    spec = gift_spec(name)
    gid = _gift_id_int(gift.gift_id)
    return {
        "from_id": gift.sender_id,
        "gift_id": gid,
        "msg_id": gid,
        "recvd": bool(gift.received),
        "to_currency_count": int(spec.get("recv_amount") or 0),
        "to_currency_type": spec.get("recv_currency") or "",
        "type": name,
    }


def recvd_message_entry(gift):
    """message 版本(claim_message_gift 的 set_recvd_messages) —— 用 currency_* 命名。"""
    name = gift.gift_type or ""
    spec = gift_spec(name)
    gid = _gift_id_int(gift.gift_id)
    return {
        "currency_count": int(spec.get("recv_amount") or 0),
        "currency_type": spec.get("recv_currency") or "",
        "from_id": gift.sender_id,
        "gift_id": gid,
        "msg_id": gid,
        "raw_message": None,
        "type": name,
    }


def _gift_id_int(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def pending_gifts(user_id, now=None):
    """返回 (未过期的送出的记录, 未过期的收到的记录) 两个列表。"""
    from infrastructure.database.models import FreeGift
    now = int(now or time.time())
    try:
        sent = FreeGift.query.filter_by(sender_id=user_id).filter(
            (FreeGift.expires_at.is_(None)) | (FreeGift.expires_at > now)
        ).order_by(FreeGift.sent_at.desc()).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("gift_helper: 读取已送礼物失败: %s", exc)
        sent = []
    try:
        recvd = FreeGift.query.filter_by(receiver_id=user_id, received=False).filter(
            (FreeGift.expires_at.is_(None)) | (FreeGift.expires_at > now)
        ).order_by(FreeGift.sent_at.asc()).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("gift_helper: 读取待收礼物失败: %s", exc)
        recvd = []
    return sent, recvd


def claim_gift(receiver_id, gift_id, from_id=None):
    """领取一份礼物。

    返回 (ok, gift|None, message)。ok=True 时已经给收礼方加了货币并标记 received,
    但**没有 commit**(调用方 commit)。
    """
    from infrastructure.database.models import db, FreeGift
    gid = str(gift_id)
    gift = FreeGift.query.filter_by(gift_id=gid, receiver_id=receiver_id).first()
    if gift is None and from_id is not None:
        # gift_id 有时会被拼成 "from_id:gift_id"; 再宽一点, 只按 from_id 兜底。
        gift = FreeGift.query.filter_by(receiver_id=receiver_id,
                                        sender_id=str(from_id)).filter(
            FreeGift.received.is_(False)).order_by(
            FreeGift.sent_at.desc()).first()
    if gift is None:
        return False, None, "gift_not_found"
    if gift.received:
        return False, gift, "already_received"

    spec = gift_spec(gift.gift_type or "")
    recv_cur = spec.get("recv_currency")
    recv_amount = int(spec.get("recv_amount") or 0)
    if recv_cur and recv_amount > 0:
        cur = _ensure_currency(receiver_id)
        setattr(cur, recv_cur, int(getattr(cur, recv_cur, 0) or 0) + recv_amount)
    gift.received = True
    gift.received_at = int(time.time())
    try:
        db.session.flush()
    except Exception:  # noqa: BLE001
        pass
    return True, gift, "ok"


def pending_count(user_id):
    try:
        _sent, recvd = pending_gifts(user_id)
        return len(recvd)
    except Exception:  # noqa: BLE001
        return 0


def buffs_sign(buffs):
    """get_buffs_sign: 官方是一串 64 位十六进制。空 buff 列表给空表的哈希即可。"""
    try:
        import json as _json
        payload = _json.dumps(buffs, sort_keys=True, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        payload = "[]"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

