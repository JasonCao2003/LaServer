# -*- coding: utf-8 -*-
"""
版本分流服饰数据库: 按客户端版本返回该版本应推送的服饰解锁(含 NPC)。

数据来源: E:/Sky 下各版本安装包内的 assets/Data/Resources/OutfitDefs.json
  (同一版本有多个包时, 取"整包直接含该文件"的最新 build; 安装包里没有对应版本的
   旧锚点 0.16.0/0.17.0 等按原表保留; 0.30.7~0.30.9 安装包已损坏, 这些客户端
   回落到 0.29.0 的数据)
config/outfit_version_map.json: [ {version, major, minor, patch, chars:[...]}, ... ] 升序。
  chars 存的是该版本 OutfitDefs.json 的"所有读取"——Char 玩家服饰 + CharSkyNPC +
  NPC_* 角色服饰 + Nest_* 等全部名字(完整覆盖, 不含漏项)。
  每个版本条目只含该版本自己的读取, 高版本条目绝不混入低版本(高版本不适用于低版本)。

分段规则(用户需求, 逐版本单独适配):
  "版本1到版本2之间(不含版本2)的客户端使用版本1的数据"。
  例: 0.1.9~0.2.5 用 0.1.9; 0.2.6~0.3.11 用 0.2.6; 0.22.0~0.22.4 用 0.22.0;
      0.23.6~0.23.9 用 0.23.5; 0.33.0~0.33.1 用 0.33.0; 0.34.5 及以上用 0.34.5。
  实现: 对客户端版本 V, 取"最大的 <= V 的已知版本"的服饰列表。
"""
import os
import json

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAP = None


def _map_path():
    return os.path.join(_root, "config", "outfit_version_map.json")


def _load_map():
    global _MAP
    if _MAP is not None:
        return _MAP
    try:
        with open(_map_path(), encoding="utf-8") as f:
            _MAP = json.load(f)
    except Exception:
        _MAP = []
    return _MAP


def _norm(ver):
    """规范化版本元组为 (major, minor, patch)。"""
    if ver is None:
        return (0, 0, 0)
    if isinstance(ver, (tuple, list)):
        v = tuple(int(x or 0) for x in ver)
        while len(v) < 3:
            v = v + (0,)
        return v[:3]
    s = str(ver).replace(",", ".")
    parts = [int(x) for x in s.split(".") if x.strip().isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def known_versions():
    """全部已知版本列表(升序), 每项 {major, minor, patch, chars}。
    chars 为完整服饰名集合(Char/NPC/Nest 全部)。"""
    return [{"major": e["major"], "minor": e["minor"],
             "patch": e.get("patch", 0), "chars": e["chars"]}
            for e in _load_map()]


def version_for_client(major, minor, patch=0):
    """返回客户端应使用的已知版本条目, 或 None。

    分段规则: 最大的 <= 客户端版本的已知版本。
    """
    client = _norm((major, minor, patch))
    vers = _load_map()
    if not vers:
        return None
    chosen = None
    for e in vers:
        ev = (e["major"], e["minor"], e.get("patch", 0))
        if ev <= client:
            chosen = e
        else:
            break
    if chosen is None:
        chosen = vers[0]  # 客户端低于所有已知版本 -> 用最早版本
    return chosen


def chars_for_version(major, minor, patch=0):
    """返回客户端版本应推送的服饰名字列表(含 NPC/Nest, 来自该版本 OutfitDefs 全部读取)。"""
    e = version_for_client(major, minor, patch)
    if e is not None:
        return list(e["chars"])
    # 回退: 从 full_unlocks_template.json 提取全部 Char
    try:
        with open(os.path.join(_root, "config", "full_unlocks_template.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        return [u["name"] for u in d.get("unlocks", [])
                if u.get("name", "").startswith("Char")]
    except Exception:
        return []


def unlocks_for_version(major, minor, patch=0, default_type="spiritshop",
                        ack=True, created_at=None):
    """返回客户端版本应推送的 unlock 条目列表。"""
    import time
    now = created_at if created_at is not None else 1654570099
    names = chars_for_version(major, minor, patch)
    return [{"name": n, "type": default_type, "ack": ack, "created_at": now}
            for n in names]


def client_version_from_ua(ua):
    """从 User-Agent 解析 (major, minor, patch), 失败返回 None。

    统一走 client_version 纠偏(见 config/client_version_overrides.json):
    魔改老客户端可能谎报版本(实测 0.13.4 报 0.23.6.354648)。
    """
    try:
        from helpers.client_version import client_version_from_ua as _cv
        return _cv(ua)
    except Exception:
        pass
    import re
    m = re.search(r'/(\d+)\.(\d+)\.(\d+)', ua or '')
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
