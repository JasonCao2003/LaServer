from flask import request, Response
from database.models import db, User, UserCurrency, ForgeRate, SeasonUnlock
import json
import os
from helpers.game_control_helper import get_season_config, get_season_state

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def _disabled_flag_path():
    # config/season_finalize_disabled, 由管理面板"禁用该功能"开关控制
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "config", "season_finalize_disabled")

def season_finalize():
    # 管理面板禁用该接口: 返回合法的空 200 响应 (游戏正常接收但不结算季节),
    # 注意不能用 204/空体, 否则游戏客户端判定为失败并不断重试导致连接错误。
    if os.path.exists(_disabled_flag_path()):
        return Response(
            response=json.dumps({}, ensure_ascii=False),
            status=200,
            mimetype='application/json',
        )
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
        
        # 兜底建货币行时用管理面板『账号默认数据』(config/game_control.json ->
        # season.finalize_currency)。[2026-10-06 修正] 以前写死全 0, 面板设的新号
        # 初始蜡烛/爱心不生效; 而 season_finalize 常常比 get_currency 更早被调用,
        # 谁先建行由它决定 -> 两处必须用同一套默认值。
        from helpers.currency_helper import ensure_user_currency
        currency = ensure_user_currency(user_id)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            currency = UserCurrency.query.filter_by(user_id=user_id).first()

        forge_source_types = [
            "wax", "color_yellow_wax", "color_white_wax", "color_red_wax",
            "color_magenta_wax", "color_green_wax", "color_cyan_wax",
            "color_blue_wax", "color_black_wax"
        ]
        
        forge_rates = []
        for source_type in forge_source_types:
            forge_rate = ForgeRate.query.filter_by(user_id=user_id, source_type=source_type).first()
            if not forge_rate:
                defaults = {
                    "wax": {"current_tier": 26, "current_level": 4, "level_tier_count": 5, "reset_acknowledged": True},
                    "color_yellow_wax": {"current_tier": 4, "current_level": 1, "level_tier_count": 3},
                    "color_white_wax": {"current_tier": 2, "current_level": 1, "level_tier_count": 1},
                    "color_red_wax": {"current_tier": 5, "current_level": 1, "level_tier_count": 4},
                    "color_magenta_wax": {"current_tier": 4, "current_level": 1, "level_tier_count": 3},
                    "color_green_wax": {"current_tier": 5, "current_level": 1, "level_tier_count": 4},
                    "color_cyan_wax": {"current_tier": 4, "current_level": 1, "level_tier_count": 3},
                    "color_blue_wax": {"current_tier": 5, "current_level": 1, "level_tier_count": 4},
                    "color_black_wax": {"current_tier": 2, "current_level": 1, "level_tier_count": 1}
                }
                
                default_data = defaults.get(source_type, {"current_tier": 1, "current_level": 1, "level_tier_count": 1})
                
                forge_rate = ForgeRate(
                    user_id=user_id,
                    source_type=source_type,
                    current_tier=default_data["current_tier"],
                    current_level=default_data["current_level"],
                    level_tier_count=default_data["level_tier_count"],
                    reset_acknowledged=default_data.get("reset_acknowledged", False)
                )
                db.session.add(forge_rate)
            
            forge_rates.append(forge_rate)
        
        db.session.commit()
        
        _sc = get_season_config()
        _season_name = _sc["season_unlock_name"]
        season_unlock = SeasonUnlock.query.filter_by(user_id=user_id, name=_season_name).first()

        # 关键修复: 只有当赛季"确实已结束"(到达 开始+持续天数 或 被提前结束)时,
        # 才做货币转换并下发结束弹窗。赛季进行中(active)/未开始(future)时, 客户端
        # 进游戏也可能调用 season_finalize —— 若此时也转换+弹窗, 就会出现"开场动画
        # 播到一半弹季节结束窗"。因此进行中/未开始一律不转换、不弹窗, 只返回当前货币。
        try:
            _state = get_season_state().get("state")
        except Exception:
            _state = "active"
        season_ended = (_state == "ended")

        # 关键: 让"开始"与"终止"两个功能能正确循环往复。
        # 结束弹窗的幂等标记是 SeasonUnlock 行(每季 name 不同, 如 season_finish_28)。
        # 但标记若只增不减, 会破坏"重新开季"——管理面板把 force_end 改回 false 让本季
        # 重新 active 后, 上次留下的同名标记仍在, 导致下次真结束时 first_time 永远为
        # False, 终止弹窗再也不触发。
        # 因此: 当本季处于 active/future(即"开始功能"生效, 季节正在进行)时, 主动删除
        # 本季的结束标记, 使其回到"未结算"干净状态; 只有 ended 时才弹一次。这样
        # 开始→终止→再开始→再终止 每一轮都能正确弹窗, 进行中则绝不弹。
        if not season_ended and season_unlock is not None:
            try:
                db.session.delete(season_unlock)
                db.session.commit()
                season_unlock = None
            except Exception:
                db.session.rollback()

        # 一次性货币转换 + 弹窗: 仅在赛季已结束且该玩家尚未结算过时执行一次。
        # 用 SeasonUnlock 行是否存在作为"是否已结算过"的幂等标记。
        first_time = season_ended and (season_unlock is None)
        if first_time:
            # 剩余季蜡按比例折算成普通蜡烛, 然后清空季节货币(季蜡/季节爱心/季节烛火)。
            ratio = int(_sc.get("convert_ratio", 1) or 1)
            leftover_sc = int(currency.season_candle or 0)
            if leftover_sc > 0 and ratio > 0:
                currency.candles = int(currency.candles or 0) + leftover_sc * ratio
            currency.season_candle = 0
            currency.season_heart = 0
            currency.season_wax = 0

            season_unlock = SeasonUnlock(
                user_id=user_id,
                unlock_type="season",
                name=_season_name,
                created_at=0,
                ack=False,
            )
            db.session.add(season_unlock)
            db.session.commit()
        
        update_forge_rates = []
        for forge_rate in forge_rates:
            update_forge_rates.append({
                "source_type": forge_rate.source_type,
                "current_tier": forge_rate.current_tier,
                "current_level": forge_rate.current_level,
                "tier_completion": forge_rate.tier_completion,
                "level_completion": forge_rate.level_completion,
                "level_tier_index": forge_rate.level_tier_index,
                "level_tier_count": forge_rate.level_tier_count,
                "reset_acknowledged": forge_rate.reset_acknowledged,
                "enabled": forge_rate.enabled
            })
        
        # 季节结束弹窗只在"第一次结算"时下发该解锁(ack=False 触发弹窗+货币转换动画)。
        # 客户端确认弹窗是写到另一张表(UserUnlocks), SeasonUnlock.ack 不会翻转, 故
        # 这里以 first_time 为准: 之后再调用不再下发, 避免每次登录重复弹"季节结束"。
        if first_time:
            update_unlocks = [{
                "type": season_unlock.unlock_type,
                "name": season_unlock.name,
                "created_at": season_unlock.created_at,
                "ack": False,
            }]
        else:
            update_unlocks = []
        
        # 货币统一用 currency_helper.build_currency, 与其它接口字段一致(防扣费动画异常)
        # result 语义: "本次需要结算/弹结束总结的季节"。仅在真正首次结算(first_time,
        # 即季节确实已结束且该玩家尚未结算过)时带季节名; 季节进行中一律返回空, 否则
        # 客户端每次进游戏都以为有季节刚结束 -> 反复弹"季节结束"弹窗。
        from helpers.currency_helper import build_currency
        response_body = {
            "result": (["season_" + str(_sc["current_season"])] if first_time else []),
            "currency": build_currency(currency),
            "update_forge_rates": update_forge_rates,
            "update_unlocks": update_unlocks
        }
        
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
