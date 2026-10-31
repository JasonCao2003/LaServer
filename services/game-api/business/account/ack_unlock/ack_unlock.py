# -*- coding: utf-8 -*-
"""
接口: /account/ack_unlock
函数: ack_unlock()

兑换树/先祖商店里买完东西后, 客户端弹"已解锁"提示, 玩家把它点掉时调这个接口做确认。

官方抓包(static_responses/account__ack_unlock.json):
    {"result": true,
     "update_unlocks": [{"ack": true, "created_at": 1717267947,
                         "name": "CharSkyKid_Hair_TopSharp", "type": "spiritshop"}]}

客户端解析(libBootloader 0.22.0):
  * sub_43ABB8 = URL, sub_43ABC4 = 请求体构造(只写 name), sub_43AD2C = 响应解析:
      - "result"(6) 用 bool 解析(sub_7BE1F0) 置内部成功标志 a1+112;
      - "update_unlocks"(14) 塞进解锁表 *(a2+472)+1048(和 get_unlocks 同一张表)。
  * 请求体三个字段都会带(user/session 由客户端公共部分注入)。

原实现的两个毛病(线上实测: 3 次调用全部回 {"result":"error"}):
  1. 调用了 check_request_params / validate_session 这两个**既没定义也没 import** 的
     函数 -> NameError -> 直接落到 except -> 永远 {"result":"error"} -> 客户端认为
     ack 失败, 买来的东西一直停在"未确认"状态(玩家看到的就是"兑换了没解锁")。
  2. 读写的是 UserUnlocks —— 一张只有本文件引用的历史遗留 JSON 表(全仓库 grep 只有
     这里在用), 而线上真正的解锁数据在 UnlockItem 表(get_unlocks 就是按 UnlockItem
     读的)。所以就算不报错也改不到地方。

现在: 按 name 找 UnlockItem, 置 ack=True, 回官方形状。
"""
from flask import request, Response
from infrastructure.database.models import db, User, UnlockItem
import json
import logging

logger = logging.getLogger(__name__)


def _reply(body):
    """业务失败也一律 200(客户端收到非 2xx 会弹"非法请求")。"""
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=200, mimetype='application/json')


def _check_request_params(request_data, required_params):
    if not request_data:
        return "Request body is empty or not valid JSON"
    missing_params = [p for p in required_params if p not in request_data]
    if missing_params:
        return "Missing required parameters: " + ", ".join(missing_params)
    return None


def _validate_session(user_id, session_token):
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user is not None


def ack_unlock():
    try:
        request_data = request.get_json(force=True, silent=True) or {}

        problem = _check_request_params(request_data, ['user', 'session', 'name'])
        if problem:
            return _reply({"result": problem})

        user_id = request_data.get('user')
        session_token = request_data.get('session')
        if not _validate_session(user_id, session_token):
            return _reply({"result": "unauthorized"})

        name = (request_data.get('name') or '').strip()
        if not name:
            return _reply({"result": "missing_parameters: name"})

        unlock = UnlockItem.query.filter_by(user_id=user_id, name=name).first()
        if unlock is None:
            # 客户端只有真的买到手了才会来 ack, 而这里没记录(例如魔法节点走的
            # lootbox 分支以前根本不落 unlock)。补一条, 状态才和 get_unlocks 一致。
            import time
            logger.warning(f"ack_unlock: no UnlockItem for user={user_id} name={name}; "
                           f"creating it as acked")
            unlock = UnlockItem(user_id=user_id, name=name, unlock_type="spiritshop",
                                ack=True, created_at=int(time.time()))
            db.session.add(unlock)
        else:
            unlock.ack = True
        db.session.commit()

        return _reply({
            "result": True,
            "update_unlocks": [{
                "type": unlock.unlock_type or "spiritshop",
                "name": unlock.name,
                "created_at": unlock.created_at or 0,
                "ack": True,
            }],
        })

    except Exception as e:
        logger.error(f"An error has occurred in ack_unlock: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return _reply({"result": "error"})
