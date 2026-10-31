# -*- coding: utf-8 -*-
"""
接口: /account/get_currency
函数: get_currency()
获取用户货币信息
"""
from flask import request, Response
from database.models import db, User, UserCurrency
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_currency():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        # 取货币行; 没有就按管理面板『账号默认数据』建一行。
        # [2026-10-06 修正] 以前这里写死 candles=0/wax=3, 面板把新号蜡烛设成 300
        # 也从来没生效过(新号进游戏看货币接口 -> 建 0 行 -> 显示 0 蜡烛)。
        from helpers.currency_helper import build_currency, ensure_user_currency
        user_currency = ensure_user_currency(user_id)
        try:
            db.session.commit()
        except Exception:
            # 并发下别的请求刚好建好了 -> 重新取一次, 别把取货币打回 500
            db.session.rollback()
            user_currency = UserCurrency.query.filter_by(user_id=user_id).first()

        # 季卡(vip)每日额外 1 根季蜡: 同一账号每个太平洋日只发一次(发放在 season_candle)。
        try:
            from account.season_pass_daily import grant_daily_bonus
            grant_daily_bonus(user_id)
        except Exception:
            pass

        # 构建响应: 统一用 currency_helper.build_currency, 与购买/季节结算/任务领取
        # 等所有接口保持同一套字段, 避免客户端把"缺失字段"动画到 0 再涨回(扣费异常)。
        currency = build_currency(user_currency)

        response_body = {"currency": currency}
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )
