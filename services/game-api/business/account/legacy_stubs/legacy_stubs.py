# -*- coding: utf-8 -*-
"""旧版(0.15.x)客户端仍在调用、但新版服务端已不再使用的 account 端点。

这些路径缺失时会落到 404->200 {} 兜底, 客户端拿到空对象而不是空列表,
部分老版本解析会报错。这里按 0.15.5 参考实现的形状返回空列表。
"""

import json

from flask import Response


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def get_daily_quests():
    return _resp({"daily_quests": []})


def get_weekly_quests():
    return _resp({"weekly_quests": []})


def get_season_quests_v2():
    return _resp({"season_quests": [], "has_season_quests_rewards": []})


def get_season_pass_defs():
    return _resp({"season_pass_defs": []})


def get_currency_defs():
    return _resp({"currency_defs": []})


def get_event_currency():
    return _resp({"event_currency": []})
