# -*- coding: utf-8 -*-
"""
共享空间(Shared Space)道具 —— "物品放置栏"里那些道具的注入。

问题(实测证据):
  * 官方 /service/status/api/v1/get_unlocks 的 status_unlocks 里带 **224 条**
    type="level" 的 CharSkyKid_Prop_*(道具/摆件: 地毯、抱枕、伞、篝火、钢琴...),
    见 static_responses/service__status__api__v1__get_unlocks.json。
  * 本服同一个接口的响应里 "CharSkyKid_Prop_" 出现 **0 次**
    (monitor_exports/cache/service__status__api__v1__get_unlocks.jsonc, 14 次抓包;
     库里 status_unlock 表的 8716 条全是 type='level' 的开门/开图, 没有道具)。
  * 共享空间里"放置物品"要用的就是这批道具 —— 账号一件都没有, 进共享空间点放置
    时**物品放置栏是空的/打不开**(用户反馈: "共享空间无法放置物品 物品放置栏打不开")。
    这不是游戏的问题, 是服务端没把道具发给客户端。

做法: 按客户端版本从 config/full_unlocks_template.json(道具和外观是同一份表,
447 条 _Prop_)里挑出道具, 转成官方 status_unlock 形状(type="level", ack=true)
注入 get_unlocks 的响应 —— **不写数据库**(和 full_unlock_helper 的"响应注入"同一思路,
关掉只要把注入那段注释掉, 账号数据一点没动)。

要改"给不给道具/给哪些", 改这里 or 改 config/full_unlocks_template.json 里的 _Prop_ 条目。
"""
import logging
import re
import threading

logger = logging.getLogger(__name__)

_cache = {}
_lock = threading.Lock()

_UA_VER = re.compile(r"Sky-[^/\s]+/(\d+)\.(\d+)\.(\d+)")

# 道具在官方 status_unlocks 里的 type 就是 "level"(见官方抓包), 不是 spiritshop
PROP_TYPE = "level"
# 官方样例里的 unlocked_at 是这个时间戳附近(2022 年), 用不上精确值
DEFAULT_UNLOCKED_AT = 1654570099

# 只要"玩家道具"(CharSkyKid_Prop_*), 不要 NPC 手里拿的那些(CharSkyNPC_Prop_*)
NAME_PREFIX = "CharSkyKid_"
NAME_MARK = "_Prop_"


def client_version():
    """从 User-Agent 里取客户端版本 (major, minor, patch); 取不到返回 None。

    统一走 client_version 纠偏(见 config/client_version_overrides.json):
    魔改老客户端可能谎报版本(实测 0.13.4 报 0.23.6.354648)。
    """
    try:
        from business.helpers.client_version import client_version_from_request
        return client_version_from_request()
    except Exception:
        pass
    try:
        from flask import request
        ua = request.headers.get("User-Agent", "") if request else ""
    except Exception:
        ua = ""
    m = _UA_VER.search(ua or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def prop_unlocks(version=None):
    """按版本返回道具解锁条目(官方 status_unlock 形状)。失败返回空表。

    注意参数名不能叫 client_version —— 会盖住上面的 client_version() 函数
    (踩过: TypeError: 'NoneType' object is not callable, 注入静默失效)。
    """
    key = version or ()
    with _lock:
        if key in _cache:
            return _cache[key]
    out = []
    try:
        from business.helpers.full_unlock_helper import full_unlocks
        for it in full_unlocks(client_version=version):
            nm = str(it.get("name") or "")
            if not (nm.startswith(NAME_PREFIX) and NAME_MARK in nm):
                continue
            out.append({
                "name": nm,
                "type": PROP_TYPE,
                "ack": True,
                "unlocked_at": int(it.get("created_at") or DEFAULT_UNLOCKED_AT),
            })
    except Exception as e:
        logger.warning("prop list load failed: %s", e)
    with _lock:
        _cache[key] = out
    return out


def inject(items, version=None):
    """把道具并进 status_unlocks 列表(同名的保留原有条目, 不覆盖)。返回新增条数。"""
    if not isinstance(items, list):
        return 0
    props = prop_unlocks(version if version is not None else client_version())
    have = {it.get("name") for it in items if isinstance(it, dict)}
    added = 0
    for p in props:
        if p["name"] not in have:
            items.append(p)
            have.add(p["name"])
            added += 1
    if added:
        logger.info("shared_space_props: injected %d props (total %d)", added, len(items))
    return added