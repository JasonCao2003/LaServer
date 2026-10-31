# -*- coding: utf-8 -*-
r"""
接口: /service/trust/api/v1/get
函数: get_trust()

官方抓包(monitor_exports/*.json, UA 0.33.2/0.33.7, 共 15 次)实测:

    请求: {"user":..., "user_id":..., "session":...,
           "npcs":["questap28","lighthorn","dizzy","jellydance","flag",
                   "airplanearms","questap29","revolvingdance","cuteclap",
                   "secrethandshake","whisper"]}
    响应: {"npcs":[],"players":[]}      (Content-Length: 24, 一字不差)

也就是说官方对"请求里点名的这些 NPC"回的也是空数组 —— "没有 trust 数据"
是正常状态, 客户端据此把每个目标当作 tier/xp=0。

问题: 本服之前**没有注册**这条路径 -> 落到 json_error_handlers 的兜底
`200 {}`。客户端读响应时是按 "npcs"/"players" 两个成员数组去取的(0.34.0
的反编译: 0x235d798 起, 先 bl 取 "npcs" 成员, cbz 判空后跳去取 "players"),
`{}` 会让两级成员查找都落空。虽然那两处都有 cbz 守着不会立刻炸, 但 0.34.0
新的好友树(羁绊/亲密度, 见 0.34.0 新增串 friend_affinity_zero /
howtoplay_friendship_trust)会在这份数据上对齐关系状态 —— 缺键和"官方那种
空数组成员"不是一回事, 这里补成与官方一字不差的形状。

返回固定为官方的 {"npcs": [], "players": []}; 不解析请求里的目标 id 去
"造" 条目 —— 官方自己就是空的, 造出来的不存在的 tier/xp 反而可能让客户端
去找不存在的 tier_def(见 account__trust__get_trust_tier_defs.json)。
"""
from flask import request, Response
import json
import logging

logger = logging.getLogger(__name__)


def _reply(body, status=200):
    return Response(
        response=json.dumps(body, ensure_ascii=False),
        status=status,
        mimetype="application/json",
    )


def get_trust():
    """trust 查询接口: 始终回官方那份空数组结构。"""
    try:
        raw = request.get_data(as_text=True) or ""
        data = None
        try:
            data = json.loads(raw) if raw.strip() else None
        except Exception:
            data = None
        if isinstance(data, dict):
            npcs = data.get("npcs")
            players = data.get("players")
            logger.info(
                "trust/get: user=%s npcs=%s players=%s",
                data.get("user") or data.get("user_id"),
                len(npcs) if isinstance(npcs, list) else npcs,
                len(players) if isinstance(players, list) else players,
            )
    except Exception as e:
        logger.warning("trust/get: 解析请求失败: %s", e)

    # 官方形状, 一字不差(Content-Length 24)
    return _reply({"npcs": [], "players": []})