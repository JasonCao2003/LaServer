# -*- coding: utf-8 -*-
# wing_buffs_ops.py - collect/convert/drop/deposit
from flask import request, Response
from infrastructure.database.models import db, User, UserWingBuff, UserCurrency
import json
import time

def _get_user(data):
    uid = data.get("user") or data.get("user_id")
    session = data.get("session")
    if not uid or not session:
        return None, None
    user = User.query.filter_by(id=uid, session=session).first()
    return user, uid

def _ok(body):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=200, mimetype="application/json")

def _err(msg, code=400):
    return Response(response=json.dumps({"error": msg}), status=code, mimetype="application/json")


def wing_buffs_collect():
    """POST /account/wing_buffs/collect - ????"""
    data = request.get_json(force=True) or {}
    user, uid = _get_user(data)
    if not user:
        return _err("invalid session", 401)
    names = data.get("names", [])
    if not names:
        name = data.get("name")
        if name:
            names = [name]
        else:
            return _err("no names")
    update = []
    for name in names:
        wb = UserWingBuff.query.filter_by(user_id=uid, name=name).first()
        if wb:
            wb.collected = True
        else:
            wb = UserWingBuff(user_id=uid, name=name, collected=True, deposited=False,
                             last_conversion=0, deposit_id=0)
            db.session.add(wb)
        update.append({"collected": True, "name": name})
    db.session.commit()
    return _ok({"result": "ok", "update_wing_buffs": update})


def wing_buffs_drop():
    """POST /account/wing_buffs/drop - ????"""
    data = request.get_json(force=True) or {}
    user, uid = _get_user(data)
    if not user:
        return _err("invalid session", 401)
    names = data.get("names", [])
    if not names:
        name = data.get("name")
        if name:
            names = [name]
        else:
            return _err("no names")
    update = []
    for name in names:
        wb = UserWingBuff.query.filter_by(user_id=uid, name=name).first()
        if wb:
            wb.collected = False
        update.append({"collected": False, "name": name})
    db.session.commit()
    return _ok({"result": "ok", "update_wing_buffs": update})


def wing_buffs_deposit():
    """POST /account/wing_buffs/deposit - ????"""
    data = request.get_json(force=True) or {}
    user, uid = _get_user(data)
    if not user:
        return _err("invalid session", 401)
    pairs = data.get("name_deposit_id_pairs", [])
    if not pairs:
        return _err("no pairs")
    update = []
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        name, dep_id = pair[0], pair[1]
        wb = UserWingBuff.query.filter_by(user_id=uid, name=name).first()
        if wb:
            wb.deposited = True
            wb.deposit_id = dep_id
            wb.collected = False
        else:
            wb = UserWingBuff(user_id=uid, name=name, collected=False, deposited=True,
                             deposit_id=dep_id, last_conversion=0)
            db.session.add(wb)
        update.append({"collected": False, "deposit_id": dep_id, "deposited": True, "name": name})
    db.session.commit()
    return _ok({"result": "ok", "update_wing_buffs": update})


def _seed_wing_buffs(uid):
    """献祭前把光翼目录播种进库(用户一条都没有时)。

    客户端对 /account/wing_buffs/convert 的期望是"把所有光翼折算成升华蜡烛",
    库里没有光翼行时转换结果为 0, 面板/界面就成了空转。这里按
    config/all_wing_buffs.json(与面板"全光翼"同一批文件)播种, deposited=False
    表示尚未献祭, collected=True 表示已拥有。
    """
    if UserWingBuff.query.filter_by(user_id=uid).count() > 0:
        return
    try:
        from business.helpers.full_unlock_helper import full_wing_buffs
        from infrastructure.database.models import User
        u = User.query.filter_by(id=uid).first()
        if not u:
            return
        seen = {r[0] for r in db.session.query(UserWingBuff.name)
                .filter_by(user_id=uid).all()}
        for b in full_wing_buffs() or []:
            name = b.get("name") if isinstance(b, dict) else b
            if not name or name in seen:
                continue
            seen.add(name)
            dep = int((b.get("deposit_id") or 0) if isinstance(b, dict) else 0)
            # 官方 deposit_id 是 64 位位掩码, 个别条目带第 64 位(2^63),
            # SQLite INTEGER 是有符号 64 位, 直接写会 OverflowError -> 整批播种回滚。
            dep &= 0x7FFFFFFFFFFFFFFF
            db.session.add(UserWingBuff(user_id=uid, name=name, collected=True,
                                        deposited=False, last_conversion=0,
                                        deposit_id=dep))
        db.session.flush()
    except Exception:
        db.session.rollback()
        import traceback as _tb
        _tb.print_exc()


def wing_buffs_convert():
    """POST /account/wing_buffs/convert - 献祭: 光翼折算升华蜡烛(1:1)"""
    data = request.get_json(force=True) or {}
    user, uid = _get_user(data)
    if not user:
        return _err("invalid session", 401)

    _seed_wing_buffs(uid)
    now = int(time.time())

    from business.helpers.currency_helper import build_currency, ensure_user_currency
    currency = ensure_user_currency(uid)

    wing_buffs = UserWingBuff.query.filter_by(user_id=uid).all()
    converted = 0
    for wb in wing_buffs:
        if wb.deposited:
            # 已经献祭过的光翼不再重复折算(避免反复 convert 刷升华蜡烛);
            # 想再来一次要走 wing_buffs/drop 把它掉回未献祭状态。
            continue
        currency.prestige = (currency.prestige or 0) + 1
        wb.deposited = True
        wb.last_conversion = now
        converted += 1
    db.session.commit()

    wing_buffs_list = []
    for wb in UserWingBuff.query.filter_by(user_id=uid).all():
        wing_buffs_list.append({
            "name": wb.name,
            "collected": bool(wb.collected),
            "deposited": bool(wb.deposited),
            "last_conversion": int(wb.last_conversion or 0),
            "deposit_id": int(wb.deposit_id or 0),
        })
    return _ok({
        "result": "ok",
        "currency": build_currency(currency),
        "wing_buffs": wing_buffs_list,
        "converted": converted,
    })
