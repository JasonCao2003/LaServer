# -*- coding: utf-8 -*-
r"""
接口: /account/commerce/receipt  (+ /account/commerce/fulfill, /account/commerce/update)

季卡/内购发货。

背景:
  客户端购买(0.15 ~ 0.32 都是这条路径)走 CommerceVerifyReceiptRequest ->
  POST /account/commerce/receipt。私服不做 Google/Apple 校验, 收到就当支付成功,
  按 config/iaplist.json 里的商品定义发货。参考实现:
  Sky-0.15.5-server-main/wbsky/route/account.py:4294 commerce_receipt /
                                :4379 commerce_fulfill / :4408 commerce_update

请求体(真机实测, 0.15.5 注释 4318 行):
  假购买路径(kUseAppStoreIAP=false)下**没有 transactions 数组**:
    {"user":"…","session":"…","platform":"fake",
     "receipt":"com.hwb.sky.FTCDL2","target_pid":"FTCDL2","target_uid":"…"}
  也有可能带 transactions: [{"product_id":…, "transaction_id":…}]。
  两种都兼容。

发货规则(config/iaplist.json 每条商品):
  currencyType / currencyCount   -> 主货币(如 season_candle+30)
  currencyType2 / currencyCount2 -> 副货币(如 season_pass_token+2)
  id 以 SPASS 开头               -> 同时把 user_currency.vip 置 1(季卡持有)
  unlocks 里是 "{{Seasons::iap_unlocks_*}}" 本地化占位符, 原样记录不解析。

响应(与客户端对账所需): errorcode=0 + result/status + transactions + currency
  —— currency 快照必须带全, 否则客户端买完会把蜡烛数字动画回退(见 currency_helper)。

幂等:
  消耗品每次购买都发(假购买 receipt 恒为 "com.hwb.sky.<pid>", 按它去重会把
  "再买一次"误判成重复); 订单只落一份流水到 instance/commerce_orders.json 备查。
"""

from flask import request, Response
from database.models import db, User, UserCurrency
from helpers.currency_helper import build_currency, ensure_user_currency
import json
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_IAP_FILE = os.path.join(_ROOT, "config", "iaplist.json")
_ORDERS_FILE = os.path.join(_ROOT, "data", "instance", "commerce_orders.json")
_PRODUCT_INFO = os.path.join(_ROOT, "resources", "static_responses", "account__commerce__product_info.json")

_orders_lock = threading.Lock()
_iap_cache = {}


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# 商品定义里的 currencyType -> UserCurrency 列名(两边大部分同名, 这里只列不同名/别名)
_COLUMN_ALIAS = {
    "heart": "heart",
    "hearts": "heart",
    "candle": "candles",
    "season_candles": "season_candle",
    "season_hearts": "season_heart",
    "pass_token": "season_pass_token",
    "season_pass_token": "season_pass_token",
}


def _product_map():
    """config/iaplist.json -> {id: 商品}。按 mtime 缓存。"""
    try:
        mtime = os.path.getmtime(_IAP_FILE)
    except OSError:
        return {}
    cached = _iap_cache.get("data")
    if cached is not None and cached[0] == mtime:
        return cached[1]
    products = {}
    try:
        with open(_IAP_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        for p in (data.get("iaplist") or []):
            if isinstance(p, dict) and p.get("id"):
                products[str(p["id"])] = p
    except Exception as e:  # 配置坏了不能把购买打 500
        logger.warning("读取 iaplist.json 失败: %r", e)
        products = {}
    _iap_cache["data"] = (mtime, products)
    return products


def _pick_any(data, *keys):
    for k in keys:
        v = data.get(k)
        if v not in (None, ""):
            return v
    return None


def _session_user(data):
    """/account/* 统一校验: user + session 对得上才返回 User 行。"""
    user_id = data.get("user") or data.get("user_id")
    session = data.get("session")
    if not user_id or not session:
        return None
    return User.query.filter_by(id=user_id, session=session).first()


def _grant(user_id, product):
    """把商品定义里的货币发到 user_currency, 返回发放明细。"""
    granted = []
    cur = ensure_user_currency(user_id)
    for tkey, ckey in (("currencyType", "currencyCount"), ("currencyType2", "currencyCount2")):
        ctype = product.get(tkey)
        count = _to_int(product.get(ckey))
        if not ctype or count <= 0:
            continue
        column = _COLUMN_ALIAS.get(str(ctype), str(ctype))
        if not hasattr(cur, column):
            logger.warning("iaplist 商品 %s 的货币 %s 没有对应列, 跳过", product.get("id"), ctype)
            continue
        total = (_to_int(getattr(cur, column)) + count)
        setattr(cur, column, total)
        granted.append({"currency": str(ctype), "count": count, "total": total})
    # 季卡(SPASS*): 客户端季节面板读 vip 标记是否持有季卡
    pid = str(product.get("id") or "")
    if pid.upper().startswith("SPASS"):
        cur.vip = 1
        granted.append({"currency": "vip", "count": 1, "total": 1})
    # 解锁项: "{{Seasons::iap_unlocks_*}}" 是本地化占位符, 原样记录不解析;
    # 真实解锁 id(如 CharSkyKid_Wing_ClassicOne_TGCBlue$) 写进 unlock_item 表,
    # 客户端 get_unlocks 里就能看到这件外观。
    from database.models import UnlockItem
    owned = {u.name for u in UnlockItem.query.filter_by(user_id=user_id).all()}
    now = int(time.time())
    for unl in (product.get("unlocks") or []):
        if not isinstance(unl, str) or not unl:
            continue
        if unl.startswith("{{"):
            granted.append({"unlock": unl})
            continue
        if unl in owned:
            granted.append({"unlock": unl, "already_owned": True})
            continue
        db.session.add(UnlockItem(user_id=user_id, name=unl, unlock_type="spiritshop",
                                  ack=False, created_at=now))
        granted.append({"unlock": unl})
    return granted


def _record_order(user_id, pid, tid, granted):
    """订单流水落盘(仅供排查/对账, 不影响发货)。"""
    try:
        os.makedirs(os.path.dirname(_ORDERS_FILE), exist_ok=True)
        with _orders_lock:
            orders = []
            if os.path.isfile(_ORDERS_FILE):
                try:
                    with open(_ORDERS_FILE, "r", encoding="utf-8") as f:
                        orders = json.load(f) or []
                except Exception:
                    orders = []
            orders.append({
                "user_id": user_id, "product_id": pid, "transaction_id": tid,
                "status": "verified", "at": int(time.time()), "granted": granted,
            })
            with open(_ORDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(orders[-5000:], f, ensure_ascii=False)
    except Exception as e:
        logger.warning("写 commerce 订单流水失败: %r", e)


def _currency_snapshot(user_id):
    try:
        return build_currency(UserCurrency.query.filter_by(user_id=user_id).first())
    except Exception:
        return {}


def commerce_receipt():
    """内购凭据校验并发货。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    me = _session_user(data)
    if me is None:
        return _resp({"errorcode": 1, "result": "invalid_session"}, 401)

    # 送礼场景: 受赠方在 target_uid; 普通购买发给下单人
    target_uid = _pick_any(data, "target_uid", "target_user")
    target = User.query.filter_by(id=target_uid).first() if target_uid else None
    receiver = target.id if target is not None else me.id

    txs = data.get("transactions")
    if isinstance(txs, dict):
        txs = [txs]
    if not isinstance(txs, list):
        txs = []
    if not txs:
        # 假购买路径: 商品 id 在 target_pid, 凭据在 receipt
        pid = _pick_any(data, "target_pid", "target_product_id", "product_id", "productId")
        if pid:
            txs = [{
                "product_id": pid,
                "transaction_id": (_pick_any(data, "receipt", "transaction_id", "transactionId") or pid),
            }]

    products = _product_map()
    out, granted_all = [], []
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        pid = _pick_any(tx, "product_id", "productId", "id")
        if not pid:
            continue
        pid = str(pid)
        tid = str(_pick_any(tx, "transaction_id", "transactionId", "tid") or uuid.uuid4())
        product = products.get(pid)
        if product is None:
            logger.warning("commerce/receipt: 未知商品 %s, 按空发货处理", pid)
            out.append({"product_id": pid, "transaction_id": tid, "result": "ok",
                        "status": "verified", "duplicate": False, "granted": []})
            continue
        granted = _grant(receiver, product)
        granted_all.extend(granted)
        _record_order(receiver, pid, tid, granted)
        out.append({"product_id": pid, "transaction_id": tid, "result": "ok",
                    "status": "verified", "duplicate": False, "granted": granted})
        logger.info("commerce/receipt: user=%s product=%s granted=%s", receiver, pid,
                    [g.get("currency") for g in granted if g.get("currency")])

    db.session.commit()
    resp = {
        "errorcode": 0, "result": "ok", "status": "ok",
        "transactions": out, "verified": out, "granted": granted_all,
        "currency": _currency_snapshot(receiver),
    }
    return _resp(resp)


def commerce_fulfill():
    """把订单标记为已发放(客户端买完都会调一次)。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    me = _session_user(data)
    if me is None:
        return _resp({"errorcode": 1, "result": "invalid_session"}, 401)
    tid = _pick_any(data, "target_tid", "transaction_id", "transactionId", "tid") or ""
    pid = _pick_any(data, "product_id", "productId") or ""
    cur = _currency_snapshot(me.id)
    return _resp({"errorcode": 0, "result": "ok", "status": "fulfilled",
                  "transaction_id": tid, "product_id": pid, "fulfilled": True,
                  "currency": cur})


def commerce_update():
    """受赠方信息更新(送礼场景)。客户端字段: gifter。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    return _resp({"errorcode": 0, "result": "ok", "status": "ok",
                  "gifter": _pick_any(data, "gifter", "user") or ""})


def commerce_product_info():
    """内购商品详情(部分版本会单独拉)。返回静态抓包, 缺失时回空表。"""
    try:
        with open(_PRODUCT_INFO, "r", encoding="utf-8-sig") as f:
            return Response(response=f.read(), status=200, mimetype='application/json')
    except Exception:
        return _resp({"products": [], "iaplist": []})
