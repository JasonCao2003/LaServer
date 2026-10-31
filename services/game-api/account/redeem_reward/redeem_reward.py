# -*- coding: utf-8 -*-
"""
接口: /account/redeem_reward
函数: redeem_reward()

兑换(领取)一份"奖励" —— 0.3.15 的教程流程里会用它领 NPC 送的新手礼物。

请求体(反编译确认, D:\\工具\\SO\\Sky0315.c)
------------------------------------------
由 sub_100473AC4 (0x100473AC4, Sky0315.c:942595 一带) 构造, 只有三个字段:
    {"user": "<uuid>", "session": "<32 位 hex>", "id": "<奖励 id 字符串>"}
URL 是硬编码的常量 "/account/redeem_reward" (Sky0315.c:942783), 客户端不读服务端
下发的地址。`id` 的值来自关卡数据, 不在代码里: 0.3.15 包里的
    Data/Levels/SkyHub2/Objects.level.bin
    Data/Levels/HubReveal/Objects.level.bin
这两个文件里各有一个 BstNode_34603010 的数据项带 `npc_gift_tutorial` 字段,
所以这个接口实际收到的 id 就是 "npc_gift_tutorial"。

响应(实测形状)
--------------
官方/参考服务端一律回 200 + {"result": "ok"}, 不带奖励明细。
    * E:/beifen/Sky/refs/Sky-0.15.5-server-main/.../wbsky/route/account.py:4836
          def redeem_reward(): return jsonify({"result": "ok"})
    * E:/beifen/Sky/refs/十二的光遇服务器_v3.39-.../accountStubRequests.js:449
          makeWriteRequest("/account/redeem_reward", () => ({ "result": "ok" }))

客户端怎么判成功(Sky0315.c sub_10096FE88, 0x10096FE88)
----------------------------------------------------
    if ( slot->state != 1 || slot->http_status != 200 )  -> 失败
失败后: 播 "PurchaseFailure" 音效、打日志 "RedeemReward Request failed: <id>
(retrying)"、第一次失败弹 toast **system_connectionissue_03**(连接问题,请稍后重试),
然后每 2.5 秒重发, **无限重试**。
成功后才把教程标记 #190 = "received_npc_gift" 置上(Sky0315.c:995506 的 switch)。

所以这个接口**绝对不能回 4xx/5xx**(本仓库 index.py 的 json_error_handlers 会把
404 兜成 200 + {}, 而 {} 缺 result 也不算成功路径, 客户端照样无限重试)。抓包证据
(monitor_exports/cache/account__redeem_reward.jsonc)就是连续 140 次 200 + {}
被重发了 2 分多钟。这里一律 200 + {"result": "ok"}。

调用频率: 一次教程流程只该成功一次, 但客户端失败会重发, 所以本接口必须是幂等的
—— 重复调用直接再回一次 ok(不报错、不重复发奖)。奖励本身由客户端按 id 自行
播放/发放(教程标记存在客户端), 服务端只做"确认收到"。
"""
import logging

from flask import jsonify, request

from database.models import User

logger = logging.getLogger(__name__)

# 0.3.15 已知会发过来的 id(仅用于日志/排查, 不在此做白名单限制)
_KNOWN_REWARD_IDS = {
    "npc_gift_tutorial",
}


def redeem_reward():
    """把所有 redeem_reward 请求都当成功处理(见文件头说明)。

    无论 user/session 是否有效、id 认不认识, 都回 200 + {"result": "ok"}:
    客户端拿不到 2xx 会无限重试并一直弹"连接问题"。
    """
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        data = {}

    user_id = data.get("user") or ""
    session = data.get("session") or ""
    reward_id = data.get("id") or ""

    user = None
    if user_id and session:
        try:
            user = User.query.filter_by(id=user_id, session=session).first()
        except Exception:  # noqa: BLE001 - 查库失败不能影响返回
            logger.exception("redeem_reward 查会话失败 user=%s", user_id)

    logger.info(
        "redeem_reward: id=%r user=%s session=%s 会话=%s%s",
        reward_id, user_id or "-", (session[:8] + "...") if session else "-",
        "有效" if user else "无效/未找到",
        "" if reward_id in _KNOWN_REWARD_IDS else " (未知 id, 照样回 ok)",
    )

    return jsonify({"result": "ok"}), 200
