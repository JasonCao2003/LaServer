# -*- coding: utf-8 -*-
"""
接口: /service/trust/api/v1/get_capped
函数: get_capped()

行为:
  - 客户端登录后会请求该接口。若请求里的 user_id 在数据库中不存在,
    则用同一 id 自动创建一个账号(便于通过 trycloudflare/外网首次进入的
    新客户端直接落库, 无需先走 create 流程)。
  - 自动建号同样遵守"自动授权"开关(auto_auth_helper): 开启后此刻之后
    新建的账号自动授权并标记 AUTO_AUTH(供面板标黄)。
  - 返回一个安全的空 capped 响应(trust 上限数据), 不影响客户端流程。
"""
from flask import request, jsonify, Response
from database.models import db, User
import json
import uuid
import time
import logging

logger = logging.getLogger(__name__)


def _extract_user_id(data):
    """从请求体中尽量取出 user_id。"""
    if not isinstance(data, dict):
        return None
    for k in ("user", "user_id", "userId"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _ensure_account(user_id):
    """库里没有该 id 就创建同 id 账号。返回 (created: bool)。"""
    try:
        existing = User.query.filter_by(id=user_id).first()
        if existing:
            return False
    except Exception as e:
        logger.error(f"get_capped 查询用户失败: {e}", exc_info=True)
        return False

    ip_address = request.remote_addr or '0.0.0.0'
    user_agent = request.headers.get('User-Agent', '')
    created_at = int(time.time())

    # 自动授权开关(与 create/auth_create 行为一致)
    auto_authorized = 0
    remark = ''
    try:
        from helpers.auto_auth_helper import should_auto_authorize, AUTO_AUTH_MARK
        if should_auto_authorize(created_at):
            auto_authorized = 1
            remark = AUTO_AUTH_MARK
    except Exception:
        pass

    # 自动建号: 未授权账号会话保留 UNAUTH_ 前缀; 已自动授权则用普通会话
    session = (uuid.uuid4().hex if auto_authorized
               else f"UNAUTH_{uuid.uuid4().hex}")
    try:
        new_user = User(
            id=user_id,
            device='Unknown Device',
            device_identifier=uuid.uuid4().hex,
            session=session,
            recovery_token=uuid.uuid4().hex + uuid.uuid4().hex,
            platform='',
            ip_address=ip_address,
            is_authorized=auto_authorized,
            remark=remark,
            created_at=created_at,
            device_name='Unknown Device',
            device_type='Unknown',
            device_model='Unknown Model',
            device_os='Unknown OS',
            user_agent=user_agent[:500],
        )
        db.session.add(new_user)
        db.session.commit()
        # 货币行按面板『账号默认数据』初始化(与登录自动建号一致, 否则新号 0 蜡烛)
        try:
            from helpers.currency_helper import ensure_user_currency
            ensure_user_currency(user_id)
            db.session.commit()
        except Exception as _ce:
            db.session.rollback()
            logger.warning(f"get_capped 建号后初始化货币行失败 {user_id}: {_ce}")
        _txt = "已自动授权" if auto_authorized else "未授权"
        logger.info(f"get_capped 自动建号: {user_id} ({_txt})")
        print(f"[get_capped] 自动建号: {user_id} ({_txt})")
        return True
    except Exception as e:
        db.session.rollback()
        logger.error(f"get_capped 自动建号失败 {user_id}: {e}", exc_info=True)
        return False


def get_capped():
    """trust capped 接口: 不存在则建号, 始终返回安全空响应。"""
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        data = None
    if data is None:
        try:
            data = json.loads(request.get_data(as_text=True) or "{}")
        except Exception:
            data = {}

    user_id = _extract_user_id(data)
    if user_id:
        _ensure_account(user_id)

    # 安全空响应: trust 上限数据为空, 不影响客户端
    body = {"capped_activities": [], "capped": []}
    return Response(json.dumps(body, ensure_ascii=False),
                    status=200, mimetype="application/json")
