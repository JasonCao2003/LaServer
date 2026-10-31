# -*- coding: utf-8 -*-
"""
接口: /account/ack_collectible
确认收藏品(emote/动作)。该接口服务端无原动态实现。
版本分流由全局 static_responses 钩子统一处理:
- >=0.30.0 (库数据正是 0.30.0 抓取) 由全局钩子返回接口库静态响应。
- 低于 0.30.0 走到此处, 返回空 200 兜底 (旧版本不受影响, 不崩溃)。
"""
from flask import Response


def ack_collectible():
    return Response("{}", 200, mimetype="application/json")
