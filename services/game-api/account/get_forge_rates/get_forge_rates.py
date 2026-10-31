# -*- coding: utf-8 -*-
"""
接口: /account/get_forge_rates
函数: get_forge_rates()

返回玩家的锻造(熔炼)档位。客户端**用它算每次锻造要花多少 wax**
(用户那次锻造请求里 cost=300 就是这么来的), 所以这里的档位必须与数据库里玩家
自己的 forge_rate 记录一致, 否则客户端报价和服务端扣款两套账。

旧实现: 完全不看数据库, 写死返回 wax=current_tier 26 / level 4(其实是把某次抓包
的响应硬编码进来了 —— 数据库 forge_rate 表里 wax 档确实大多是 26/4, 所以没暴露),
并且缺 user 时返回 400(客户端弹"非法请求。\n请重试。")。

现在:
  - 读玩家自己的 forge_rate 记录; 没有记录则回退到下面这份默认档位表(与原行为一致);
  - 任何情况都 200(缺参数也只是回默认档位, 不再 400)。
"""
from flask import Response
import json
import logging

logger = logging.getLogger(__name__)

# 默认档位表(玩家还没有 forge_rate 记录时使用; 与原硬编码完全一致)
_DEFAULT_FORGE_RATES = [
    {"source_type": "wax", "current_tier": 26, "current_level": 4,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 5, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_yellow_wax", "current_tier": 4, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 3, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_white_wax", "current_tier": 2, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 1, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_red_wax", "current_tier": 5, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 4, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_magenta_wax", "current_tier": 4, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 3, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_green_wax", "current_tier": 5, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 4, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_cyan_wax", "current_tier": 4, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 3, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_blue_wax", "current_tier": 5, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 4, "reset_acknowledged": False, "enabled": True},
    {"source_type": "color_black_wax", "current_tier": 2, "current_level": 1,
     "tier_completion": 0.0, "level_completion": 0.0, "level_tier_index": 0,
     "level_tier_count": 1, "reset_acknowledged": False, "enabled": True},
]


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def _row_payload(r):
    return {
        "source_type": r.source_type,
        "current_tier": r.current_tier,
        "current_level": r.current_level,
        "tier_completion": r.tier_completion or 0.0,
        "level_completion": r.level_completion or 0.0,
        "level_tier_index": r.level_tier_index or 0,
        "level_tier_count": r.level_tier_count or 0,
        "reset_acknowledged": bool(r.reset_acknowledged),
        "enabled": bool(r.enabled),
    }


def get_forge_rates():
    try:
        data = {}
        try:
            from flask import request
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            data = {}
        user_id = data.get('user') or data.get('user_id')

        rows = []
        if user_id:
            try:
                from database.models import ForgeRate
                rows = ForgeRate.query.filter_by(user_id=user_id).all()
            except Exception:
                rows = []
        if rows:
            return _resp({"forge_rates": [_row_payload(r) for r in rows]})
        return _resp({"forge_rates": _DEFAULT_FORGE_RATES})

    except Exception as e:
        logger.error(f"get_forge_rates error: {e}")
        return _resp({"forge_rates": _DEFAULT_FORGE_RATES})