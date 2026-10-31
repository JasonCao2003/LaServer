# -*- coding: utf-8 -*-
"""
新建账号全解锁辅助 (改版: 响应注入, 不写库)

由管理面板"新建账号全解锁"按钮控制: 开启后, 在开启时间点之后新建的账号
在"拉取数据"时(如 get_unlocks / get_collectibles / get_wing_buffs /
status get_unlocks)由对应接口直接把准备好的完整解锁 JSON 数据发回去,
不往数据库写入任何解锁行(避免破坏原接口数据流程)。

各数据来源(config 下准备好的 JSON, 与面板"推送全解锁"完全同一批文件):
    全动作 : quandongzuo.json            -> collectible 数据
    开全门 : kaiquantu.json              -> status_unlock 数据
    全物品 : full_unlocks_template.json  -> unlock 数据
    全光翼 : all_wing_buffs.json         -> wing_buff 数据

配置文件: config/auto_full_unlock.json
    {"enabled": true/false, "since": <开启时的unix秒时间戳>}

面板与服务端共用同一份配置文件, 即时生效(不缓存)。
"""
from paths import SERVICE_ROOT
import os
import json
import threading

_lock = threading.Lock()


def _root_dir():
    return SERVICE_ROOT


def _config_path():
    return os.path.join(_root_dir(), "config", "auto_full_unlock.json")


def load_auto_full_unlock():
    """读取新建账号全解锁配置。返回 {"enabled": bool, "since": int}。失败返回关闭状态。"""
    path = _config_path()
    try:
        with _lock:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        return {
            "enabled": bool(data.get("enabled", False)),
            "since": int(data.get("since", 0) or 0),
        }
    except Exception:
        return {"enabled": False, "since": 0}


def should_auto_full_unlock(created_at):
    """给定账号创建时间(unix秒), 判断拉取数据时是否应直接返回完整解锁。"""
    cfg = load_auto_full_unlock()
    if not cfg["enabled"]:
        return False
    return int(created_at or 0) >= cfg["since"]


# ---------------------------------------------------------------
# 读取准备好的完整解锁 JSON 数据(失败返回空列表, 绝不让接口 500)
# ---------------------------------------------------------------

def _load_json(name, key):
    try:
        with open(os.path.join(_root_dir(), "config", name), encoding="utf-8") as f:
            d = json.load(f)
        return d.get(key, []) if isinstance(d, dict) else d
    except Exception:
        return []


def full_collectibles():
    """全动作: config/quandongzuo.json 的 collectibles 列表。"""
    return _load_json("quandongzuo.json", "collectibles")


def full_status_unlocks():
    """开全门/全图: config/kaiquantu.json 的 status_unlocks 列表。"""
    return _load_json("kaiquantu.json", "status_unlocks")


def full_emote_unlocks():
    """全动作: config/default_emotes.json 的 emotes 列表(440 条 emote_upgrade_*)。

    动作是按 `emote_upgrade_<族>_<NN>` 的 **UnlockItem** 逐档解锁的(不是 Collectible),
    所以"全动作"要发这份清单客户端才会亮。优先读 default_emotes.json, 缺失时退回
    从 config/unlock.json 里筛 emote_upgrade_*(两份内容一致, 440 条)。
    """
    em = _load_json("default_emotes.json", "emotes")
    if not em:
        em = [it for it in _load_json("unlock.json", "unlocks")
              if str((it or {}).get("name", "")).startswith("emote_upgrade_")]
    return em


def _with_full_emotes(items):
    """把全动作(440 条)并到解锁列表**最前面**(按 name 去重, 原顺序保留在后面)。

    动机(0.22.0 实测): 客户端拿到的解锁表里动作只有 6 条(wave 01~06),
    而库里"拥有全动作"的账号有 440 条 `emote_upgrade_*` -> 客户端的动作面板
    "明明解锁了全动作还是灰色"。动作排最前面, 是为了让下游按体积从**尾部**
    裁剪(见 get_unlocks 的体积闸门)时绝不会裁掉动作。
    """
    emotes = full_emote_unlocks()
    if not emotes:
        return items
    # 原列表里的动作条目一律挪到最前面重排(否则它们会留在尾部, 被体积闸门先裁掉 ——
    # 例如 wave 01~06 本来就在服饰清单里, 裁掉后连默认的"挥手"都会变灰)
    rest = [it for it in items if not str(it.get("name", "")).startswith("emote_upgrade_")]
    seen = {it.get("name") for it in rest}
    out = []
    for e in emotes:
        nm = (e or {}).get("name")
        if not nm or nm in seen:
            continue
        out.append({
            "name": nm,
            "type": e.get("type", "spiritshop"),
            "ack": bool(e.get("ack", False)),
            "created_at": e.get("created_at", 1654570099),
        })
        seen.add(nm)
    out.extend(rest)
    return out


def _client_version_from_request():
    """从 Flask 请求的 User-Agent 解析客户端版本 (major, minor, patch), 无则 None。

    统一走 client_version 纠偏(见 config/client_version_overrides.json):
    有的魔改老客户端谎报版本(实测 0.13.4 报 0.23.6.354648), 不纠偏就会把
    0.23 时代的服饰名发给老引擎 -> 不认识 -> 全解锁不生效 / 闪退。
    """
    try:
        from business.helpers.client_version import client_version_from_request
        return client_version_from_request()
    except Exception:
        pass
    try:
        from flask import request as _req
        ua = _req.headers.get("User-Agent", "") if _req else ""
    except Exception:
        return None
    if not ua:
        return None
    import re
    m = re.search(r"/(\d+)\.(\d+)\.(\d+)", ua)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def full_unlocks(client_version=None):
    """全物品/能力(版本分流): 按客户端版本返回该版本应推送的 unlocks 列表。

    版本分流规则(逐版本单独适配, 高版本不适用于低版本): 分段表
    config/outfit_version_map.json 决定 —— 版本1到版本2之间(不含版本2)的客户端
    使用版本1的数据; 每个版本条目只含该版本自己安装包 (E:/Sky 下各版本 apk) 里
    assets/Data/Resources/OutfitDefs.json 的"所有读取" (Char 玩家服饰 + CharSkyNPC
    + NPC_* 角色服饰 + Nest_* 等, 包括 npc 的), 绝不混入更高版本条目。例如:
      0.1.9~0.2.5   -> 0.1.9 的数据 (127 件)
      0.16.0~0.16.9 -> 0.16.0 的数据 (531 件, 原表保留)
      0.22.0~0.22.4 -> 0.22.0 的数据 (768 件)
      0.23.6~0.23.9 -> 0.23.5 的数据 (856 件)
      0.33.0~0.33.1 -> 0.33.0 的数据 (1519 件)
      0.34.5 及以上  -> 0.34.5 的数据 (1678 件, 含 96 件 NPC/Nest)

    client_version 可显式传入 (major, minor, patch); 不传时自动从请求
    User-Agent 解析。解析不到版本或映射缺失时回退到
    full_unlocks_template.json(旧行为/最高版本)。
    """
    if client_version is None:
        client_version = _client_version_from_request()
    try:
        from business.helpers.outfit_version_db import unlocks_for_version, version_for_client
        if client_version is not None:
            (maj, minr, pat) = (client_version + (0, 0, 0))[:3]
            entry = version_for_client(maj, minr, pat)
            if entry is not None:
                names = entry["chars"]
                # 版本化服饰(Char/NPC/Nest 全部) + 合并原有非服饰项(动作/能力/传送等)
                base = _load_json("full_unlocks_template.json", "unlocks")
                seen = set(names)
                out = [{"name": n, "type": "spiritshop", "ack": True,
                        "created_at": 1654570099} for n in names]
                for it in base:
                    nm = it.get("name")
                    if nm and not str(nm).startswith("Char") and nm not in seen:
                        out.append(it)
                        seen.add(nm)
                return _with_full_emotes(out)
    except Exception:
        pass
    # 回退: 原始全量
    return _with_full_emotes(_load_json("full_unlocks_template.json", "unlocks"))


def full_wing_buffs():
    """全光翼: config/all_wing_buffs.json 的 wing_buffs 列表。"""
    return _load_json("all_wing_buffs.json", "wing_buffs")
